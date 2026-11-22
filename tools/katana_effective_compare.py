"""Browser-free basecrawler versus Katana static + headless, with HTTP evidence.

Run and verify are separate so interrupted crawls and verification can resume.
No query values or numeric path segments are merged without response evidence.
"""
from __future__ import annotations

import argparse
import concurrent.futures
from collections import Counter
import hashlib
import json
import math
import re
import subprocess
import tempfile
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import crawler_compare as common
from crawler_metrics import (protocol_headers, protocol_identity, is_rsc, equivalent_rsc,
                              mode_metrics, gap_evidence, source_execution_metrics)

ROOT = common.ROOT
MODES = ('basecrawler', 'katana-static', 'katana-browser')
ASSET = re.compile(r'\.(?:js|mjs|css|png|apng|avif|jpe?g|gif|svg|ico|webp|woff2?|ttf|map|mp[34]|pdf|zip)$', re.I)
# Match Katana's per-host HTML-page cap separately from Basecrawler's total
# request safety ceiling. A total-URL cap is not equivalent to Katana -mdp.
BASE_MAX_URLS = 2000
BASE_MAX_SEEN = 200000


def usable_control(row):
    code = row.get('status', 0)
    ordinary_control = 200 <= code < 300 or code in (404, 410)
    completed_html_error = (
        500 <= code < 600
        and 'html' in str(row.get('content_type') or '').lower()
        and bool(row.get('sha256'))
    )
    return (ordinary_control or completed_html_error) and not row.get('error') and not row.get('truncated')


def parse_control_url_overrides(assignments):
    overrides = {}
    for assignment in assignments or []:
        site, separator, url = assignment.partition('=')
        if not separator or not site or not url:
            raise ValueError('control URL must use SITE_ID=URL')
        if site not in common.TARGETS:
            raise ValueError('unknown control URL site: ' + site)
        if site in overrides:
            raise ValueError('duplicate control URL for site: ' + site)
        overrides[site] = url
    return overrides


def control_url_for(target, overrides=None):
    url = (overrides or {}).get(target.site_id)
    if not url:
        return urljoin(target.seed, '/__crawler_benchmark_missing_20261118__')
    parsed, seed = urlsplit(url), urlsplit(target.seed)
    if parsed.scheme not in ('http', 'https') or (parsed.scheme.lower(), parsed.netloc.lower()) != (seed.scheme.lower(), seed.netloc.lower()):
        raise ValueError('control URL must have the same origin as the target seed: ' + target.site_id)
    return url


def reference_execution_metrics(valid_urls, rows, completion=None):
    valid = set(valid_urls)
    attempted, successful = set(), set()
    audited_requests = completion.get('requests') if completion else None
    method_audit_complete = audited_requests is not None and all('method' in request for request in audited_requests)
    if method_audit_complete:
        rows = [{
            'url': request.get('requested_url', ''),
            'requested_url': request.get('requested_url', ''),
            'final_url': request.get('final_url') or request.get('requested_url', ''),
            'method': request.get('method'),
            'status': request.get('http_status', 0),
            'error': request.get('error') or ('' if request.get('state') == 'complete' else request.get('state', '')),
            'content_type': request.get('content_type', ''),
            'truncated': bool(request.get('truncated')),
            'request_headers': request.get('protocol_headers') or {},
        } for request in audited_requests]
    for row in rows:
        requested_url = common.normalize_url(row.get('requested_url') or row.get('url') or '')
        final_url = common.normalize_url(row.get('final_url') or row.get('url') or requested_url)
        method = str(row.get('method') or 'GET').upper()
        if method != 'GET' or requested_url not in valid:
            continue
        status = int(row.get('status') or row.get('status_code') or 0)
        error = str(row.get('error') or '')
        if status or error and error != 'max depth reached':
            attempted.add(requested_url)
        if (200 <= status < 300 and not error and not row.get('truncated')
                and final_url == requested_url):
            successful.add(requested_url)
    coverage = lambda urls: len(urls) / len(valid) if valid else None
    return {
        'dispatched_reference_urls': len(attempted),
        'dispatched_reference_coverage': coverage(attempted),
        'successful_reference_urls': len(successful),
        'successful_reference_coverage': coverage(successful),
        'successful_reference_missing': sorted(valid - successful),
    }, attempted, successful


_NAVIGATION_TIMING_FIELDS = {
    'domainlookupstart', 'domainlookupend', 'connectstart', 'connectend',
    'secureconnectionstart', 'requeststart', 'responsestart', 'responseend',
    'dominteractive', 'domcomplete', 'loadeventstart', 'loadeventend',
    'firstpaint', 'firstcontentfulpaint', 'transfersize',
    'decodedbodysize', 'encodedbodysize',
}


def classify_non_get_telemetry(request):
    """Identify browser navigation telemetry by JSON payload shape, not URL."""
    body = request.get('body')
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (TypeError, ValueError):
            return None
    if not isinstance(body, dict):
        return None

    event_fields = {'eventtype', 'event_type', 'event'}
    page_identity_fields = {'pageloadid', 'page_load_id', 'navigationid', 'navigation_id'}
    event_field = next((key for key in body if key.lower() in event_fields), None)
    identity_field = next((key for key in body if key.lower() in page_identity_fields), None)
    if not event_field or not identity_field:
        return None

    timing_paths = []

    def collect_timings(value, prefix=''):
        if isinstance(value, dict):
            for key, child in value.items():
                path = f'{prefix}.{key}' if prefix else str(key)
                if key.lower() in _NAVIGATION_TIMING_FIELDS:
                    timing_paths.append(path)
                collect_timings(child, path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                collect_timings(child, f'{prefix}[{index}]')

    collect_timings(body)
    if len(timing_paths) < 3:
        return None
    return {
        'category': 'browser_performance_telemetry',
        'evidence_fields': sorted([event_field, identity_field, *timing_paths]),
    }


def observed_non_get_target_observations(site, modes, target_rows):
    """Split observed successful non-GET targets from telemetry evidence."""
    candidates = {(str(row.get('method') or 'GET').upper(), common.normalize_url(row.get('url') or ''))
                  for mode in modes for row in target_rows[mode]}
    candidates.discard(('GET', None))
    application_targets = set()
    telemetry = {}
    for mode in modes:
        for row in jsonlines(site / mode / 'results.jsonl'):
            request = row.get('request') or {}
            response = row.get('response') or {}
            method = str(request.get('method') or 'GET').upper()
            url = common.normalize_url(request.get('endpoint') or '')
            status = int(response.get('status_code') or 0)
            pair = (method, url)
            if method == 'GET' or not url or pair not in candidates or not 200 <= status < 300 or row.get('error'):
                continue
            classification = classify_non_get_telemetry(request)
            if not classification:
                application_targets.add(pair)
                continue
            item = telemetry.setdefault(pair, {
                'method': method,
                'url': url,
                'status_codes': set(),
                'reference_modes': set(),
                'category': classification['category'],
                'evidence_fields': set(),
            })
            item['status_codes'].add(status)
            item['reference_modes'].add(mode)
            item['evidence_fields'].update(classification['evidence_fields'])

    excluded = []
    for pair, item in sorted(telemetry.items()):
        if pair in application_targets:
            continue
        excluded.append({
            **item,
            'status_codes': sorted(item['status_codes']),
            'reference_modes': sorted(item['reference_modes']),
            'evidence_fields': sorted(item['evidence_fields']),
        })
    return application_targets, excluded


def observed_non_get_targets(site, modes, target_rows):
    """Return successful application non-GET targets (excluding telemetry)."""
    return observed_non_get_target_observations(site, modes, target_rows)[0]


def method_aware_discovery_metrics(valid_get_urls, non_get_targets, base_method_urls):
    valid_get = {common.normalize_url(url) for url in valid_get_urls}
    exact_get = valid_get & {url for method, url in base_method_urls if method == 'GET'}
    non_get_found = set(non_get_targets) & set(base_method_urls)
    denominator = len(valid_get) + len(non_get_targets)
    found = len(exact_get) + len(non_get_found)
    return {
        'valid_targets': denominator,
        'discovered_targets': found,
        'coverage': found / denominator if denominator else None,
        'exact_get_targets': len(valid_get),
        'exact_get_discovered': len(exact_get),
        'non_get_targets': len(non_get_targets),
        'non_get_discovered': len(non_get_found),
    }


def strict_acceptance_failures(valid, discovery_coverage, unresolved, runs, execution):
    failures = []
    if not valid:
        failures.append('no_http_confirmed_reference_urls')
    if discovery_coverage is None or discovery_coverage < .95:
        failures.append('discovery_coverage_below_95_percent')
    success_coverage = execution.get('successful_reference_coverage')
    if success_coverage is None or success_coverage < .95:
        failures.append('successful_request_coverage_below_95_percent')
    if unresolved:
        failures.append('unresolved_reference_urls')
    if any(run.get('status') != 'completed' for run in runs.values()):
        failures.append('one_or_more_tool_runs_not_completed')
    base_completion = runs.get('basecrawler', {}).get('crawl_completion', {})
    if base_completion.get('complete') is not True:
        failures.append('basecrawler_crawl_incomplete')
    return failures


def acceptance_dimensions(valid, execution, crawl_complete, strict_failures):
    """Expose reference-target coverage separately from full crawl completion."""
    coverage = execution.get('successful_reference_coverage')
    return {
        'execution_coverage_passed': bool(valid) and coverage is not None and coverage >= .95,
        'crawl_completion_passed': crawl_complete is True,
        'strict_passed': not strict_failures,
    }


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def jsonlines(path):
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding='utf-8-sig', errors='replace').splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def _append_source_record(entry, filename, req, status):
    source = str(req.get('source') or '')
    referer = req.get('referer') or req.get('headers', {}).get('Referer') or (source if source.startswith('http') else '')
    confidence = str(req.get('confidence') or '').lower()
    evidence = str(req.get('evidence') or '')
    kind = str(req.get('kind') or '')
    tag = str(req.get('tag') or '')
    attribute = str(req.get('attribute') or '')
    signature = (filename, source, confidence, evidence, kind, tag, attribute)
    indexes = entry.setdefault('_source_record_index', {})
    record = indexes.get(signature)
    if record is None:
        record = {'record_file': filename, 'source': source, 'confidence': confidence,
                  'evidence': evidence, 'kind': kind, 'tag': tag, 'attribute': attribute,
                  'referers': [], 'depths': [], 'status_codes': []}
        indexes[signature] = record
        entry.setdefault('source_records', []).append(record)
    if referer and referer not in record['referers']:
        record['referers'].append(referer)
    depth = req.get('depth')
    if depth is not None and depth not in record['depths']:
        record['depths'].append(depth)
    if status and status not in record['status_codes']:
        record['status_codes'].append(status)


def candidates(folder, mode, target):
    result = {}
    files = ['results.jsonl', 'discoveries.jsonl'] if mode == 'basecrawler' else ['results.jsonl']
    for name in files:
        for row in jsonlines(folder / name):
            req = row if mode == 'basecrawler' else row.get('request', {})
            raw = req.get('url') if mode == 'basecrawler' else req.get('endpoint')
            url = common.normalize_url(raw or '')
            if not url or not common.in_scope(url, target):
                continue
            method = (req.get('method') or 'GET').upper()
            headers = protocol_headers(req.get('headers'))
            params = req.get('params', [])
            json_body_params = req.get('json_body_params', [])
            key = (method, url, tuple(sorted(params or [])), tuple(sorted(json_body_params or [])))
            entry = result.get(key)
            if entry is None:
                source = req.get('source', '')
                entry = {'url': url, 'method': method, 'source': source,
                         'confidence': str(req.get('confidence') or '').lower(),
                         'evidence': str(req.get('evidence') or ''),
                         'headers': headers, 'params': params, 'json_body_params': json_body_params,
                         'referer': req.get('referer') or req.get('headers', {}).get('Referer') or (source if str(source).startswith('http') else ''),
                         'source_records': []}
                result[key] = entry
            # Results are visited before source edges, so this only chooses a
            # primary provenance once. All later evidence remains attached.
            if not entry.get('source') and req.get('source'):
                entry['source'] = req['source']
                entry['confidence'] = str(req.get('confidence') or '').lower()
                entry['evidence'] = str(req.get('evidence') or '')
                entry['referer'] = req.get('referer') or req.get('headers', {}).get('Referer') or ''
            _append_source_record(entry, name, req,
                                  (row.get('status', 0) if mode == 'basecrawler' else row.get('response', {}).get('status_code', 0)))
            entry.setdefault('header_profiles', [])
            if headers and headers not in entry['header_profiles']:
                entry['header_profiles'].append(headers)
    for entry in result.values():
        entry.pop('_source_record_index', None)
        entry['sources'] = sorted({record['source'] for record in entry['source_records'] if record['source']})
        for record in entry['source_records']:
            record['referers'].sort()
            record['depths'].sort()
            record['status_codes'].sort()
        entry['source_records'].sort(key=lambda record: (
            record['record_file'], record['source'], record['confidence'], record['evidence'],
            record['kind'], record['tag'], record['attribute']))
    return list(result.values())


def run_one(target, mode, args):
    folder = args.output / target.site_id / mode
    folder.mkdir(parents=True, exist_ok=True)
    time_limit = args.seconds
    if mode == 'basecrawler':
        base_rate_limit = getattr(args, 'base_rate_limit', None)
        rate_limit = args.rate if base_rate_limit is None else base_rate_limit
        base_seconds = getattr(args, 'base_seconds_limit', None)
        time_limit = args.seconds if base_seconds is None else base_seconds
        base_pages = getattr(args, 'base_max_pages_per_host', None)
        page_limit = args.pages if base_pages is None else base_pages
        base_depth = getattr(args, 'base_depth_limit', None)
        depth_limit = args.depth if base_depth is None else base_depth
        base_max_urls = getattr(args, 'base_max_urls', None)
        max_urls = BASE_MAX_URLS if base_max_urls is None else base_max_urls
        base_max_seen = getattr(args, 'base_max_seen', None)
        max_seen = BASE_MAX_SEEN if base_max_seen is None else base_max_seen
        command = [str(args.base), '-u', target.seed, '-d', str(depth_limit), '-c', str(getattr(args, 'base_concurrency', 1)),
                   '--max-urls', str(max_urls), '--mdp', str(page_limit),
                   '--timeout', '15', '--max-body', '16777216',
                   '--scope', 'fqdn', '--rl', str(rate_limit), '--ct', str(time_limit) + 's',
                   '--no-fuzz', '--no-testfill', '--no-probe-openapi', '--fsu', '-s', 'all',
                   '--stable-host-order', '-v', '--discoveries', str(folder / 'discoveries.jsonl'),
                   '--report', str(folder / 'report.json'), '-o', str(folder / 'results.jsonl')]
        if base_max_seen is not None:
            command += ['--max-seen', str(max_seen)]
        command += ['--strategy', args.strategy]
    else:
        command = [str(ROOT / 'othercrawlers/katana.exe'), '-u', target.seed, '-d', str(args.depth),
                   '-c', '1', '-p', '1', '-timeout', '15', '-rl', str(args.rate), '-hrl', str(args.rate),
                   '-mdp', str(args.pages), '-ct', str(args.seconds) + 's', '-fs', 'fqdn',
                   '-jc', '-jsl', '-fx', '-fsu', '-j', '-or', '-ob', '-silent', '-duc',
                   '-mrs', '16777216', '-o', str(folder / 'results.jsonl')]
        command += ['-s', args.strategy]
        if mode == 'katana-browser':
            command += ['-hl', '-sc', '-scp', str(args.chrome), '-nos', '-xhr']
            command += ['-ho', 'disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable']
    if args.proxy:
        command += ['--proxy' if mode == 'basecrawler' else '-proxy', args.proxy]
    binary_hash = hashlib.sha256(Path(command[0]).read_bytes()).hexdigest()
    if args.resume and (folder / 'run.json').exists() and (folder / 'command.json').exists():
        previous = json.loads((folder / 'run.json').read_text(encoding='utf-8'))
        previous_command = json.loads((folder / 'command.json').read_text(encoding='utf-8'))['command']
        if previous['status'] == 'completed' and previous_command == command and previous.get('binary_sha256') == binary_hash:
            return previous
    write_json(folder / 'command.json', {'command': command, 'seed': target.seed,
               'scope': target.allowed_hosts, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')})
    started = time.monotonic()
    try:
        r = subprocess.run(command, cwd=ROOT, capture_output=True, encoding='utf-8', errors='replace', timeout=time_limit + 60)
        status = 'completed' if r.returncode == 0 else 'failed'
        stdout, stderr, code = r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        # taskkill is unnecessary here: subprocess.run kills its direct process.
        status, code = 'timeout', None
        stdout, stderr = str(e.stdout or ''), str(e.stderr or '')
    (folder / 'stdout.txt').write_text(stdout, encoding='utf-8')
    (folder / 'stderr.txt').write_text(stderr, encoding='utf-8')
    rows = candidates(folder, mode, target)
    raw_rows = jsonlines(folder / 'results.jsonl')
    if mode != 'basecrawler' and raw_rows and all(r.get('error') and r.get('error') != 'max depth reached' for r in raw_rows):
        status = 'failed_requests'
    completion = {}
    if mode == 'basecrawler' and (folder / 'report.json').exists():
        completion = json.loads((folder / 'report.json').read_text(encoding='utf-8')).get('completion', {})
        if status == 'completed' and completion.get('status') in ('timed_out','canceled','failed'):
            status = {'timed_out':'timeout','canceled':'canceled','failed':'failed'}[completion['status']]
        reasons = {r['code'] for r in completion.get('reasons', [])}
        if status == 'completed' and reasons & {'request_failure', 'analysis_failure', 'fatal_error', 'canceled'}:
            status = 'partial_failure'
    if status == 'completed' and not rows:
        status = 'empty_unverified'
    write_json(folder / 'candidates.json', rows)
    record = {'site': target.site_id, 'mode': mode, 'status': status, 'exit_code': code,
              'seconds': time.monotonic() - started, 'candidates': len(rows),
              'binary_sha256': binary_hash, 'crawl_completion': completion,
              'concurrency': getattr(args, 'base_concurrency', 1) if mode == 'basecrawler' else 1,
              'rate_limit': (getattr(args, 'base_rate_limit', None) if getattr(args, 'base_rate_limit', None) is not None else args.rate) if mode == 'basecrawler' else args.rate,
              'time_limit_seconds': (getattr(args, 'base_seconds_limit', None) if getattr(args, 'base_seconds_limit', None) is not None else args.seconds) if mode == 'basecrawler' else args.seconds,
              'max_pages_per_host': (getattr(args, 'base_max_pages_per_host', None) if getattr(args, 'base_max_pages_per_host', None) is not None else args.pages) if mode == 'basecrawler' else args.pages,
               'max_depth': (getattr(args, 'base_depth_limit', None) if getattr(args, 'base_depth_limit', None) is not None else args.depth) if mode == 'basecrawler' else args.depth,
               'max_urls': max_urls if mode == 'basecrawler' else None,
               'max_seen': max_seen if mode == 'basecrawler' else None,
              'request_errors': sum(bool(r.get('error')) and r.get('error') != 'max depth reached' for r in raw_rows),
              'boundary_discoveries': sum(r.get('error') == 'max depth reached' for r in raw_rows)}
    write_json(folder / 'run.json', record)
    log_record = {k:v for k,v in record.items() if k != 'crawl_completion'}
    log_record['completion_status'] = completion.get('status')
    log_record['completion_reasons'] = completion.get('reasons', [])
    log_record['unvisited_count'] = len(completion.get('unvisited', []))
    print(json.dumps(log_record), flush=True)
    return record


def fetch(url, timeout, proxy='', referer='', request_headers=None):
    started = time.monotonic()
    try:
        # Windows/Python urllib cannot use the configured SOCKS proxy. curl
        # shares the actual environment proxy profile used by the crawlers.
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'body'
            command = [shutil.which('curl.exe') or 'curl', '-sS', '-L', '--max-time', str(timeout),
                                '--max-filesize', '2097152', '-A', 'CrawlerBenchmark/1.0',
                                '-o', str(output), '-w', '%{json}']
            if proxy: command += ['--proxy', proxy]

            for name, value in protocol_headers(request_headers).items():
                if '\r' not in value and '\n' not in value:
                    command += ['-H', name + ': ' + value]
            if referer:
                a,b=urlsplit(url),urlsplit(referer)
                if (a.scheme.lower(),a.netloc.lower()) == (b.scheme.lower(),b.netloc.lower()):
                    command += ['-H','Referer: '+referer,'-H','Sec-Fetch-Site: same-origin']
            r = subprocess.run(command + [url], capture_output=True, timeout=timeout+3)
            meta = json.loads(r.stdout)
            if r.returncode and r.returncode != 63:
                raise ValueError(r.stderr.decode('utf-8', errors='replace'))
            body = output.read_bytes() if output.exists() else b''
            text = body.decode('utf-8', errors='replace')
            canonical = re.search(r'<link\b(?=[^>]*\brel=[\x22\x27]canonical[\x22\x27])[^>]*\bhref=[\x22\x27]([^\x22\x27]+)', text, re.I)
            title = re.search(r'<title[^>]*>(.*?)</title>', text, re.I | re.S)
            return {'url': url, 'final_url': meta.get('url_effective', url), 'status': int(meta['http_code']),
                    'content_type': meta.get('content_type') or '', 'bytes': len(body),
                    'truncated': r.returncode == 63,
                    'sha256': hashlib.sha256(body).hexdigest(),
                    'canonical': urljoin(meta.get('url_effective', url), canonical[1]) if canonical else '',
                    'title': title[1].strip() if title else '',
                    'observed_referer': referer,
                    'request_headers': protocol_headers(request_headers),
                    'verified_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                    'seconds': time.monotonic() - started, 'error': ''}
    except Exception as e:
        return {'url': url, 'status': 0, 'error': str(e), 'seconds': time.monotonic() - started}


def classify(row, control):
    code = row['status']
    if not code:
        return 'network_error'
    if code in (404, 410):
        return 'not_found'
    if code in (401, 403, 429):
        return 'restricted_or_blocked'
    if (500 <= code < 600
            and 'html' in str(row.get('content_type') or '').lower()
            and row.get('sha256')
            and row.get('sha256') == control.get('sha256')
            and not row.get('error')
            and not row.get('truncated')
            and usable_control(control)):
        return 'html_fallback'
    if code < 200 or code >= 400:
        return 'unconfirmed'
    if row.get('truncated'):
        return 'body_truncated'
    if is_rsc(row.get('url', '')) or protocol_headers(row.get('request_headers')).get('rsc') == '1':
        if protocol_headers(row.get('request_headers')).get('rsc') != '1':
            return 'protocol_context_missing'
        return 'valid_rsc' if 'text/x-component' in row.get('content_type', '').lower() else 'protocol_fallback'
    if row.get('final_url') != row.get('url') and re.search(r'/(?:login|signin|sign-in)(?:/|$)', urlsplit(row.get('final_url', '')).path, re.I):
        return 'authentication_redirect'
    if row.get('sha256') == control.get('sha256') and 'html' in row.get('content_type', ''):
        return 'html_fallback'
    if re.search(r'404|not found|page not found|找不到|页面不存在', row.get('title', ''), re.I):
        return 'soft_404'
    if 'html' in row.get('content_type', ''):
        if not usable_control(control):
            return 'unconfirmed_control'
        return 'valid_page'
    if 'json' in row.get('content_type', ''):
        return 'valid_json'
    return 'other_content'


def candidate_is_crawlable(row):
    records = row.get('source_records') or []
    confidences = [str(record.get('confidence') or '').lower() for record in records]
    if not confidences:
        confidences = [str(row.get('confidence') or '').lower()]
    # Empty confidence is the normal label for links and historical output
    # created before confidence metadata existed. Explicit clues/templates are
    # never treated as visited coverage.
    return any(confidence not in ('clue', 'template') for confidence in confidences)


def reference_target_lifecycle(valid_get_urls, non_get_targets, candidates, completion, comparisons=()):
    """Join immutable reference targets to Basecrawler discovery, queue and request evidence."""
    target_pairs = {('GET', common.normalize_url(url)) for url in valid_get_urls}
    target_pairs.update((str(method).upper(), common.normalize_url(url)) for method, url in non_get_targets)
    target_pairs.discard(('GET', None))
    target_pairs = {(method, url) for method, url in target_pairs if url}

    candidate_index = {}
    for row in candidates:
        method = str(row.get('method') or 'GET').upper()
        url = common.normalize_url(row.get('url') or '')
        if url:
            candidate_index.setdefault((method, url), []).append(row)

    attempt_index = {}
    for request in completion.get('requests') or []:
        method = str(request.get('method') or 'GET').upper()
        url = common.normalize_url(request.get('requested_url') or '')
        if url:
            attempt_index.setdefault((method, url), []).append(request)

    pending_index = {}
    for pending in completion.get('unvisited') or []:
        method = str(pending.get('method') or 'GET').upper()
        url = common.normalize_url(pending.get('url') or '')
        if url:
            pending_index.setdefault((method, url), []).append(pending)

    comparison_index = {}
    for detail in comparisons:
        url = common.normalize_url(detail.get('url') or '')
        if url:
            comparison_index[('GET', url)] = detail

    lifecycle = []
    for method, url in sorted(target_pairs):
        candidate_rows = candidate_index.get((method, url), [])
        attempts = sorted(attempt_index.get((method, url), []), key=lambda row: row.get('sequence', 0))
        pending = pending_index.get((method, url), [])
        queued_pending = [row for row in pending if row.get('queue_state') != 'not_queued']
        deferred = [row for row in pending if row.get('queue_state') == 'not_queued']
        sources = {}
        for row in candidate_rows:
            records = row.get('source_records') or [{
                'source': row.get('source') or 'unknown',
                'confidence': row.get('confidence') or '',
                'evidence': row.get('evidence') or '',
            }]
            for record in records:
                source = record.get('source') or 'unknown'
                key = (source, str(record.get('confidence') or '').lower(),
                       record.get('evidence') or '', record.get('record_file') or '')
                source_row = sources.setdefault(key, {
                    'source': source, 'confidence': key[1], 'evidence': key[2],
                    'record_file': key[3],
                    'referers': set(), 'depths': set(),
                })
                source_row['referers'].update(record.get('referers') or ([row.get('referer')] if row.get('referer') else []))
                source_row['depths'].update(record.get('depths') or ([row['depth']] if row.get('depth') is not None else []))

        discovered = bool(candidate_rows)
        crawlable = any(candidate_is_crawlable(row) for row in candidate_rows)
        successful_attempts = []
        for request in attempts:
            request_url = common.normalize_url(request.get('requested_url') or '')
            final_url = common.normalize_url(request.get('final_url') or request_url)
            status = int(request.get('http_status') or 0)
            request_error = request.get('error') or ('' if request.get('state') == 'complete' else request.get('state', ''))
            if (200 <= status < 300 and not request_error and not request.get('truncated')
                    and final_url == url):
                successful_attempts.append(request)

        if not discovered:
            state = 'not_discovered'
        elif successful_attempts:
            state = 'dispatched_exact_http_success'
        elif attempts:
            latest = attempts[-1]
            status = int(latest.get('http_status') or 0)
            request_error = latest.get('error') or ('' if latest.get('state') == 'complete' else latest.get('state', ''))
            if latest.get('truncated'):
                state = 'dispatched_response_truncated'
            elif latest.get('state') == 'timeout' or request_error == 'timeout':
                state = 'dispatched_timeout'
            elif latest.get('state') == 'canceled' or request_error == 'canceled':
                state = 'dispatched_canceled'
            elif request_error:
                state = 'dispatched_request_error'
            elif status and not 200 <= status < 300:
                state = 'dispatched_http_error'
            elif common.normalize_url(latest.get('final_url') or url) != url:
                state = 'dispatched_redirected'
            else:
                state = 'dispatched_unverified'
        elif queued_pending:
            state = 'queued_not_dispatched'
        elif method != 'GET':
            state = 'discovered_interface_not_sent'
        elif not crawlable:
            state = 'discovered_non_crawlable'
        elif deferred:
            state = 'discovered_not_queued'
        else:
            state = 'discovered_not_scheduled_reason_unknown'

        comparison = comparison_index.get((method, url), {})
        row = {
            'method': method,
            'url': url,
            'state': state,
            'discovered': discovered,
            'crawlable': crawlable,
            'scheduled': bool(attempts or queued_pending),
            'dispatched': bool(attempts),
            'dispatch_count': len(attempts),
            'exact_http_success': bool(successful_attempts),
            'effective_reference_covered': comparison.get('base_covered') if comparison else None,
            'sources': [{**sources[key], 'referers': sorted(sources[key]['referers']),
                         'depths': sorted(sources[key]['depths'])} for key in sorted(sources)],
            'requests': [{
                'sequence': request.get('sequence'),
                'source': request.get('source') or '',
                'state': request.get('state') or '',
                'http_status': request.get('http_status') or 0,
                'content_type': request.get('content_type') or '',
                'final_url': request.get('final_url') or request.get('requested_url') or '',
                'truncated': bool(request.get('truncated')),
                'error': request.get('error') or '',
                'protocol_headers': request.get('protocol_headers') or {},
            } for request in attempts],
            'unvisited': [{
                'reason': pending_row.get('reason') or 'unknown',
                'queue_state': pending_row.get('queue_state') or 'queued',
                'source': pending_row.get('source') or '',
                'referer': pending_row.get('referer') or '',
                'depth': pending_row.get('depth'),
            } for pending_row in pending],
            'not_scheduled_reasons': sorted({pending_row.get('reason') or 'unknown' for pending_row in deferred}),
        }
        lifecycle.append(row)
    return lifecycle


def eligible(row, mode='katana'):
    p = urlsplit(row['url'])
    return (row['method'] == 'GET' and not ASSET.search(p.path)
            and not any(x in row['url'] for x in ('EXPR', '%7B', '{', '%5C', '\\'))
            and (mode != 'basecrawler' or candidate_is_crawlable(row)))


BASE_ONLY_SAMPLE_PER_SOURCE = 5


def base_only_source_samples(base_rows, reference):
    grouped = {}
    for row in base_rows:
        if row['url'] in reference or not eligible(row, 'basecrawler'):
            continue
        positive_records = [record for record in row.get('source_records', [])
                            if str(record.get('confidence') or '').lower() not in ('clue', 'template')]
        sources = {record.get('source') or 'unknown' for record in positive_records}
        if not sources:
            sources = {row.get('source') or 'unknown'}
        for source in sources:
            grouped.setdefault(source, set()).add(row['url'])
    samples = {}
    for source, urls in grouped.items():
        samples[source] = sorted(urls, key=lambda value: hashlib.sha256(value.encode()).hexdigest())[:BASE_ONLY_SAMPLE_PER_SOURCE]
    return samples


def verify_site(target, args):
    site = args.output / target.site_id
    data = {m: json.loads((site / m / 'candidates.json').read_text(encoding='utf-8')) for m in MODES}
    # Rebuild Basecrawler candidates from raw records so source, evidence and
    # confidence are not lost in older flattened candidates.json snapshots.
    data['basecrawler'] = candidates(site / 'basecrawler', 'basecrawler', target)
    write_json(site / 'basecrawler' / 'candidates.json', data['basecrawler'])
    # Frozen candidate files predate protocol metadata. Enrich in memory from
    # their raw records without changing the frozen reference or rerunning it.
    for mode in MODES:
        observed = {r['url']: r.get('headers', {}) for r in candidates(site / mode, mode, target) if r.get('headers')}
        for row in data[mode]:
            if observed.get(row['url']): row['headers'] = observed[row['url']]
    if getattr(args, 'reference_snapshot', None):
        for mode in MODES[1:]:
            data[mode] = json.loads((args.reference_snapshot / target.site_id / (mode + '.json')).read_text(encoding='utf-8'))
    # Only real GET pages/data can be verified with GET. POST and template
    # contracts remain visible but never receive invented success labels.
    sets = {m: {r['url'] for r in rows if eligible(r, m)} for m, rows in data.items()}
    reference = sets['katana-static'] | sets['katana-browser']
    non_get_reference, excluded_non_get_telemetry = observed_non_get_target_observations(
        site, MODES[1:], data)
    # Verify all reference candidates plus potential basecrawler aliases, not
    # thousands of unrelated base-only sitemap URLs. Alias success still needs
    # the final URL or identical HTML evidence below.
    def alias_path(u):
        p = urlsplit(u)
        return p.netloc, p.path.removesuffix('.html').rstrip('/')
    prior = {r['url']:r for r in jsonlines(site/'verification.jsonl')}
    # Image proxy endpoints may have no extension. Hundreds of new image query
    # values cannot prove a page alias and need not be probed for that purpose.
    ref_paths = {alias_path(u) for u in reference if u not in prior or is_rsc(u)
                 or any(t in prior[u].get('content_type','').lower() for t in ('html','json'))}
    observed_headers = {}
    for m in reversed(MODES[1:] + MODES[:1]):
        for r in data[m]:
            if r.get('headers'): observed_headers[r['url']] = r['headers']
    base_headers = {r['url']:r['headers'] for r in data['basecrawler'] if r.get('headers')}
    ref_protocols = {protocol_identity(u,observed_headers.get(u)) for u in reference if is_rsc(u)} - {None}
    potential_aliases = {u for u in sets['basecrawler'] if alias_path(u) in ref_paths and
                         (not is_rsc(u) or protocol_identity(u,base_headers.get(u)) in ref_protocols)}
    source_samples = base_only_source_samples(data['basecrawler'], reference)
    sampled_sources = {}
    for source, sample_urls in source_samples.items():
        for url in sample_urls:
            sampled_sources.setdefault(url, []).append(source)
    urls = sorted(reference | potential_aliases | sampled_sources.keys(), key=lambda u: hashlib.sha256(u.encode()).hexdigest())
    headers = {u: observed_headers.get(u,{}) for u in urls}
    for u in urls:
        if u in base_headers and protocol_identity(u,base_headers[u]) == protocol_identity(u,headers[u]):
            headers[u] = dict(headers[u], **base_headers[u])
    cache = {r['url']: r for r in jsonlines(site / 'verification.jsonl') if r.get('status') and r.get('status') not in (400,401,403,429) and not r.get('error') and not r.get('truncated')}
    control_url = control_url_for(target, getattr(args, 'control_urls', None))
    control_path = site / 'control.json'
    control = json.loads(control_path.read_text(encoding='utf-8')) if control_path.exists() else {}
    # Freeze a successful control alongside the per-URL evidence. A transient
    # failure during resume must not silently replace the baseline control.
    offline = getattr(args, 'offline', False)
    if not offline and (not usable_control(control) or control.get('url') != control_url):
        for _ in range(2):
            control = fetch(control_url, args.verify_timeout, args.proxy)
            if usable_control(control):
                break
        write_json(control_path, control)
    if offline:
        # Preserve every saved response status as evidence. Failed or blocked
        # responses remain unresolved, but are never retried during re-scoring.
        cache = {r['url']: r for r in jsonlines(site / 'verification.jsonl')
                 if r.get('status') and not r.get('error')}
    cache = {u:r for u,r in cache.items() if not is_rsc(u) or (headers.get(u) and protocol_headers(r.get('request_headers')) == headers[u])}
    pending = [] if offline else [u for u in urls if u not in cache]
    if args.verify_limit:
        pending = pending[:args.verify_limit]
    site.mkdir(parents=True, exist_ok=True)
    start_lock = threading.Lock()
    next_start = 0.0
    def request_once(url,referer=''):
        nonlocal next_start
        with start_lock:
            delay = next_start - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            next_start = time.monotonic() + 1 / args.rate
        return fetch(url,args.verify_timeout,args.proxy,referer,headers.get(url))
    def limited_fetch(url):
        origin = urlsplit(url)
        ref = next((r.get('referer', '') for rows in data.values() for r in rows if r['url'] == url and r.get('referer') and
                    (urlsplit(r['referer']).scheme, urlsplit(r['referer']).netloc) == (origin.scheme, origin.netloc)), '')
        row = request_once(url, ref if is_rsc(url) else '')
        if row.get('status') in (400,401,403):
            origin=urlsplit(url)
            ref=next((r.get('referer','') for rows in data.values() for r in rows if r['url']==url and r.get('referer') and
                      (urlsplit(r['referer']).scheme,urlsplit(r['referer']).netloc)==(origin.scheme,origin.netloc)), '')
            if ref:
                context = request_once(url,ref)
                if 200<=context.get('status',0)<300:
                    context['initial_status']=row['status']
                    context['verification_profile']='observed_same_origin'
                    row=context
        return row
    if pending:
        with (site / 'verification.jsonl').open('a', encoding='utf-8') as f:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.verify_workers) as pool:
                futures = [pool.submit(limited_fetch, url) for url in pending]
                for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
                    row = future.result()
                    cache[row['url']] = row
                    f.write(json.dumps(row, ensure_ascii=False) + '\n')
                    f.flush()
                    if i % 50 == 0:
                        print(json.dumps({'site': target.site_id, 'verified_new': i, 'pending_total': len(pending)}), flush=True)
    categories = {u: classify(v, control) for u, v in cache.items()}
    valid = {u for u in reference if categories.get(u) in ('valid_page', 'valid_json', 'valid_rsc')}
    base = sets['basecrawler']
    base_method_urls = {(str(r.get('method') or 'GET').upper(), common.normalize_url(r.get('url') or ''))
                        for r in data['basecrawler'] if candidate_is_crawlable(r)}
    # Final URL and identical HTML allow confirmed aliases only. JSON content
    # equality does not establish endpoint equivalence.
    base_finals = {cache[u].get('final_url') for u in base if u in cache and categories.get(u) in ('valid_page','valid_json')}
    base_hashes = {cache[u]['sha256'] for u in base if u in cache and categories.get(u) == 'valid_page'}
    covered, missing, details = [], [], []
    for url in sorted(valid):
        row = cache[url]
        rsc_match = categories[url] == 'valid_rsc' and any(
            equivalent_rsc(row,dict(cache[u],request_headers=base_headers.get(u,{})))
            for u in base if u in cache)
        hit = (url in base if categories[url] != 'valid_rsc' else rsc_match) or (categories[url] != 'valid_rsc' and row.get('final_url') in base_finals) or (categories[url] == 'valid_page' and row.get('sha256') in base_hashes)
        (covered if hit else missing).append(url)
        match = ('exact' if url in base and hit else 'redirect' if row.get('final_url') in base_finals and hit
                 else 'rsc_response_and_context' if rsc_match else 'html_alias' if hit else 'missing')
        details.append({'url': url, 'method': 'GET', 'base_covered': hit, 'match': match,
                        'reference_modes': [m for m in MODES[1:] if url in sets[m]],
                        'http_status': row['status'], 'content_type': row.get('content_type'),
                        'final_url': row.get('final_url'), 'sha256': row.get('sha256'),
                        'analysis': '' if hit else 'Requires source trace; no URL-count-based explanation inferred.'})
    unresolved = sorted(u for u in reference if categories.get(u) not in ('valid_page', 'valid_json', 'valid_rsc', 'not_found', 'html_fallback', 'soft_404', 'other_content'))
    runs = {m: json.loads((site / m / 'run.json').read_text(encoding='utf-8')) for m in MODES}
    for m, run in runs.items():
        if m == 'basecrawler' and run['status'] == 'completed' and run.get('crawl_completion',{}).get('status') in ('timed_out','canceled','failed'):
            run['status'] = {'timed_out':'timeout','canceled':'canceled','failed':'failed'}[run['crawl_completion']['status']]
            write_json(site/m/'run.json',run)
        if run['status'] == 'completed' and not data[m]:
            run['status'] = 'empty_unverified'
            write_json(site / m / 'run.json', run)
        raw_rows = jsonlines(site / m / 'results.jsonl')
        if m != 'basecrawler' and raw_rows:
            run['request_errors'] = sum(bool(r.get('error')) and r.get('error') != 'max depth reached' for r in raw_rows)
            run['boundary_discoveries'] = sum(r.get('error') == 'max depth reached' for r in raw_rows)
            write_json(site / m / 'run.json', run)
        if m != 'basecrawler' and raw_rows and all(r.get('error') and r.get('error') != 'max depth reached' for r in raw_rows):
            run['status'] = 'failed_requests'
            write_json(site / m / 'run.json', run)
    ratio = len(covered) / len(valid) if valid else None
    base_result_rows = jsonlines(site / 'basecrawler/results.jsonl')
    completion = runs.get('basecrawler', {}).get('crawl_completion', {})
    execution, dispatched_urls, successful_urls = reference_execution_metrics(valid, base_result_rows, completion)
    discovery_metrics = method_aware_discovery_metrics(valid, non_get_reference, base_method_urls)
    non_get_covered = non_get_reference & base_method_urls
    all_method_valid = discovery_metrics['valid_targets']
    all_method_covered = discovery_metrics['discovered_targets']
    all_method_discovery = discovery_metrics['coverage']
    execution['successful_get_reference_coverage'] = execution['successful_reference_coverage']
    execution['non_get_reference_targets'] = len(non_get_reference)
    execution['non_get_discovered_targets'] = len(non_get_covered)
    execution['non_get_discovery_coverage'] = len(non_get_covered) / len(non_get_reference) if non_get_reference else None
    execution['excluded_non_get_telemetry_targets'] = len(excluded_non_get_telemetry)
    execution['all_method_discovery_coverage'] = all_method_discovery
    for detail in details:
        detail['verification_request_headers'] = cache[detail['url']].get('request_headers', {})
        detail['base_dispatched'] = detail['url'] in dispatched_urls
        detail['base_successful'] = detail['url'] in successful_urls
        detail['base_discovered'] = ('GET', common.normalize_url(detail['url'])) in base_method_urls
        if not detail['base_covered']:
            detail['gap_evidence'] = gap_evidence(detail['url'], data, runs['basecrawler'], base_result_rows)
    target_lifecycle = reference_target_lifecycle(
        valid, non_get_reference, data['basecrawler'], completion, details)
    lifecycle_by_pair = {(row['method'], row['url']): row for row in target_lifecycle}
    for detail in details:
        pair = ('GET', common.normalize_url(detail['url']))
        detail['execution_lifecycle'] = lifecycle_by_pair.get(pair)
    (site / 'comparison.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in details), encoding='utf-8')
    (site / 'reference-target-lifecycle.jsonl').write_text(
        ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in target_lifecycle), encoding='utf-8')
    metrics = {m: mode_metrics(m, data[m], base_result_rows if m == 'basecrawler' else jsonlines(site/m/'results.jsonl'), runs[m]) for m in MODES}
    metrics['basecrawler']['reference_execution'] = execution
    metrics['basecrawler']['reference_target_lifecycle'] = dict(sorted(Counter(
        row['state'] for row in target_lifecycle).items()))
    metrics['basecrawler']['source_execution'] = source_execution_metrics(
        data['basecrawler'], completion, valid)
    base_pairs = {(r['method'], r['url']) for r in data['basecrawler']}
    crawlable_pairs = {(r['method'], r['url']) for r in data['basecrawler'] if candidate_is_crawlable(r)}
    metrics['basecrawler']['crawlable_discovered_method_urls'] = len(crawlable_pairs)
    metrics['basecrawler']['clue_or_template_method_urls'] = len(base_pairs - crawlable_pairs)
    for m in MODES[1:]:
        refs = valid & sets[m]
        metrics[m]['effective_reference'] = len(refs)
        metrics[m]['base_covered'] = sum(u in covered for u in refs)
    source_candidate_counts = {}
    for row in data['basecrawler']:
        pair = (row['method'], row['url'])
        records = row.get('source_records') or [{'source': row.get('source') or 'unknown',
                                                  'confidence': row.get('confidence', '')}]
        for record in records:
            source = record.get('source') or 'unknown'
            source_stats = source_candidate_counts.setdefault(source, {'candidate_method_urls': set(),
                                                                        'crawlable_method_urls': set()})
            source_stats['candidate_method_urls'].add(pair)
            if str(record.get('confidence') or '').lower() not in ('clue', 'template'):
                source_stats['crawlable_method_urls'].add(pair)
    candidate_audit = {
        'candidate_method_urls': len(base_pairs),
        'crawlable_method_urls': len(crawlable_pairs),
        'clue_or_template_method_urls': len(base_pairs - crawlable_pairs),
        'sample_policy': f'At most {BASE_ONLY_SAMPLE_PER_SOURCE} deterministic GET URLs per source, excluding Katana URLs and non-crawlable clues/templates.',
        'sampled_base_only_urls': len(sampled_sources),
        'sources': {},
    }
    for source, stats in sorted(source_candidate_counts.items()):
        urls_for_source = source_samples.get(source, [])
        sample_classes = Counter(categories.get(url, 'unverified') for url in urls_for_source)
        candidate_audit['sources'][source] = {
            'candidate_method_urls': len(stats['candidate_method_urls']),
            'crawlable_method_urls': len(stats['crawlable_method_urls']),
            'sampled_base_only_urls': len(urls_for_source),
            'sample_classes': dict(sorted(sample_classes.items())),
            'confirmed_not_found_or_fallback': sum(sample_classes.get(name, 0) for name in ('not_found', 'html_fallback', 'soft_404')),
            'sample_urls': [{'url': url, 'classification': categories.get(url, 'unverified')} for url in urls_for_source],
        }
    write_json(site/'metrics.json', {'modes':metrics, 'discovery_coverage':all_method_discovery,
        'reference_execution':execution, 'strict_acceptance_version':'complete-crawl-v3',
        'excluded_non_get_telemetry': excluded_non_get_telemetry,
        'protocol_evidence_required':True,
        'reference_target_lifecycle': {
            'file': 'reference-target-lifecycle.jsonl',
            'targets': len(target_lifecycle),
            'states': dict(sorted(Counter(row['state'] for row in target_lifecycle).items())),
        },
        'reference_freshness':'historical frozen crawl; saved HTTP evidence may also be historical',
        'basecrawler_candidate_audit':candidate_audit})
    strict_failures = strict_acceptance_failures(all_method_valid, all_method_discovery, unresolved, runs, execution)
    crawl_complete = runs['basecrawler'].get('crawl_completion', {}).get('complete') is True
    dimensions = acceptance_dimensions(bool(valid), execution, crawl_complete, strict_failures)
    report = {'schema_version':2, 'protocol_aware':True, 'site': target.site_id, 'reference_candidates': len(reference), 'verified_valid_reference': len(valid),
              'covered': len(covered), 'coverage': ratio, 'discovery_coverage': all_method_discovery,
              'effective_reference_coverage': ratio,
              'exact_get_discovery_coverage': (discovery_metrics['exact_get_discovered'] / discovery_metrics['exact_get_targets']
                                               if discovery_metrics['exact_get_targets'] else None),
              'all_method_covered': all_method_covered, 'all_method_valid_reference_targets': all_method_valid,
              'exact_get_discovered': discovery_metrics['exact_get_discovered'],
              'exact_get_reference_targets': discovery_metrics['exact_get_targets'],
              'dispatched_reference_urls': execution['dispatched_reference_urls'],
              'dispatched_reference_coverage': execution['dispatched_reference_coverage'],
              'successful_reference_urls': execution['successful_reference_urls'],
              'successful_reference_coverage': execution['successful_reference_coverage'],
              'successful_reference_missing': execution['successful_reference_missing'],
              'non_get_reference_targets': len(non_get_reference),
              'non_get_discovered_targets': len(non_get_covered),
              'non_get_discovery_coverage': execution['non_get_discovery_coverage'],
              'excluded_non_get_telemetry_targets': len(excluded_non_get_telemetry),
              'excluded_non_get_telemetry': excluded_non_get_telemetry,
              'all_method_discovery_coverage': all_method_discovery,
              'crawl_complete': crawl_complete, 'crawl_completion_passed': dimensions['crawl_completion_passed'],
              'strict_passed': dimensions['strict_passed'], 'strict_failure_reasons': strict_failures,
              'missing': missing, 'unresolved': unresolved,
              'verification_classes': dict(__import__('collections').Counter(categories.values())),
              'basecrawler_candidate_audit': candidate_audit,
              'runs': runs, 'passed': dimensions['strict_passed'],
              'discovery_coverage_passed': bool(all_method_valid) and all_method_discovery >= .95 and not unresolved,
              'execution_coverage_passed': dimensions['execution_coverage_passed'],
              'note': 'Execution is scored from crawler request-attempt audit, independently of output deduplication. Requested and final URLs must match for an exact successful response; aliases do not count.'}
    write_json(site / 'effective.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('runs', 'missing', 'unresolved')}), flush=True)
    return report


def summarize(args, targets):
    rows = []
    for t in targets:
        p = args.output / t.site_id / 'effective.json'
        if p.exists():
            rows.append(json.loads(p.read_text(encoding='utf-8')))
    passed = sum(r['passed'] for r in rows)
    goal = len(rows) == len(targets) and passed >= math.ceil(.8 * len(targets))
    write_json(args.output / 'acceptance.json', {'goal_met': goal, 'required_sites': len(targets), 'evaluated_sites': len(rows), 'passed_sites': passed,
               'acceptance_version': 'complete-crawl-v3',
               'criteria': '>=80% sites; per site >=95% all-method discovery and exact successful GET coverage, no unresolved GET references, all tool runs completed, and Basecrawler crawl_completion.complete=true',
               'limitations': ['This does not certify all methods or runtime-only business flows.', 'Content equality proves HTML aliases only, not distinct API equivalence.']})
    lines = ['# Katana 双模式有效结果对比', '', '| 站点 | base秒 | Katana静态秒 | Katana浏览器秒 | 已核验有效参考 | 覆盖 | 待核验 | 静态/浏览器参考状态 | 达标 |', '|---|---:|---:|---:|---:|---:|---:|---|---|']
    for r in rows:
        seconds = [f"{r['runs'][m]['seconds']:.2f}" for m in MODES]
        coverage = f"{r['coverage']:.2%}" if r['coverage'] is not None else 'n/a'
        reference_status = '/'.join(r['runs'][m]['status'] for m in MODES[1:])
        lines.append('| ' + ' | '.join([r['site'], *seconds, str(r['verified_valid_reference']), coverage, str(len(r['unresolved'])), reference_status, str(r['passed'])]) + ' |')
    lines += ['', f'达标站点：{passed}/{len(targets)}；总目标达成：{goal}。', '', '只核验 GET 页面/JSON；方法、参数、模板及业务有效性另行验收。未核验、网络错误和浏览器失败不会被当成达标。', '启用 basecrawler --fsu；发现记录保留过滤前证据。参数值和数字 ID 不做武断归并，HTML 别名需响应证据。']
    execution_lines = ['', '## Execution acceptance (complete-crawl-v3)', '',
                       '| Site | Base c | Discovery | Exact successful GET | App non-GET | Telemetry excluded | Crawl complete | Strict failure reasons |',
                       '|---|---:|---:|---:|---:|---:|---|---|']
    for r in rows:
        base_run = r['runs']['basecrawler']
        completion = base_run.get('crawl_completion', {})
        discovery = r.get('discovery_coverage')
        successful = r.get('successful_reference_coverage')
        discovery_text = f'{discovery:.2%}' if discovery is not None else 'n/a'
        successful_text = f'{successful:.2%}' if successful is not None else 'n/a'
        complete_text = str(completion.get('complete') is True)
        reasons = ', '.join(r.get('strict_failure_reasons', [])) or 'none'
        non_get_text = f"{r.get('non_get_discovered_targets', 0)}/{r.get('non_get_reference_targets', 0)}"
        telemetry_count = str(r.get('excluded_non_get_telemetry_targets', 0))
        execution_lines.append('| ' + ' | '.join([r['site'], str(base_run.get('concurrency', 1)), discovery_text,
                                                  successful_text, non_get_text, telemetry_count,
                                                  complete_text, reasons]) + ' |')
    execution_lines += ['', '### Excluded non-GET telemetry evidence', '',
                        'Successful non-GET requests with page/event identity and at least three browser navigation timing fields are excluded from the application-target denominator. Classification uses request JSON shape, not URL or host.', '',
                        '| Site | Method | URL | HTTP status | Category | Payload fields |',
                        '|---|---|---|---:|---|---|']
    for r in rows:
        for item in r.get('excluded_non_get_telemetry', []):
            url = item['url'].replace('|', '%7C')
            fields = ', '.join(item.get('evidence_fields', []))
            statuses = ', '.join(str(value) for value in item.get('status_codes', []))
            execution_lines.append(f"| {r['site']} | {item['method']} | {url} | {statuses} | {item['category']} | {fields} |")
    lines += execution_lines
    (args.output / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return goal


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['run', 'verify', 'summarize'])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--targets', default='testasp,testaspnet,framework-vue,framework-react,framework-angular,framework-nextjs,framework-sveltekit')
    p.add_argument('--base', type=Path, default=ROOT / 'basecrawler/bin/basecrawler.exe')
    p.add_argument('--chrome', type=Path, default=common.find_chrome(None))
    p.add_argument('--depth', type=int, default=2)
    p.add_argument('--base-concurrency', type=int, default=1, help='Basecrawler worker profile; Katana reference stays at concurrency 1')
    p.add_argument('--strategy', choices=['breadth-first', 'depth-first'], default='breadth-first')
    p.add_argument('--pages', type=int, default=100)
    p.add_argument('--seconds', type=int, default=90)
    p.add_argument('--rate', type=int, default=2)
    p.add_argument('--proxy', default='', help='explicit proxy for every mode; otherwise each tool uses its environment/defaults')
    p.add_argument('--workers', type=int, default=3)
    p.add_argument('--verify-limit', type=int, default=0, help='0 = verify all; a positive limit cannot certify unresolved URLs')
    p.add_argument('--verify-timeout', type=int, default=12)
    p.add_argument('--verify-workers', type=int, default=4, help='in-flight verification requests per site; start rate still limited by --rate')
    p.add_argument('--control-url', action='append', default=[], metavar='SITE_ID=URL', help='same-origin missing-page control; repeat for multiple sites')
    p.add_argument('--resume', action='store_true')
    args = p.parse_args()
    args.output = args.output.resolve()
    if args.rate < 1 or args.workers < 1 or args.verify_workers < 1 or args.base_concurrency < 1:
        p.error('rate, workers, verify-workers, and base-concurrency must be positive')
    targets = [common.TARGETS[t] for t in args.targets.split(',')]
    try:
        args.control_urls = parse_control_url_overrides(args.control_url)
        for target in targets:
            control_url_for(target, args.control_urls)
    except ValueError as e:
        p.error(str(e))
    args.output.mkdir(parents=True, exist_ok=True)
    if args.action == 'run':
        if not args.chrome or not args.chrome.is_file():
            p.error('Chrome is required for the Katana browser reference')
        write_json(args.output / 'config.json', {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()})
        # Modes sequential per host, sites parallel: avoids unfair rate contention.
        def run_site(t):
            for m in MODES:
                run_one(t, m, args)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(run_site, targets))
    elif args.action == 'verify':
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(lambda t: verify_site(t, args), targets))
        summarize(args, targets)
    else:
        summarize(args, targets)


if __name__ == '__main__':
    main()
