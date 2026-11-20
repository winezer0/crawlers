"""Comparable discovery/request metrics; unknown browser network totals stay unknown."""
from collections import Counter
from urllib.parse import urlsplit, urlunsplit, unquote_plus

PROTOCOL_HEADERS = {'rsc', 'next-router-prefetch', 'next-router-segment-prefetch',
                    'next-router-state-tree', 'next-url', 'x-deployment-id'}


def protocol_headers(headers):
    values = {k.lower():str(v) for k,v in (headers or {}).items()}
    allowed = PROTOCOL_HEADERS | ({'user-agent','accept','accept-language'} if values.get('rsc') == '1' else set())
    return {k:v for k,v in values.items() if k in allowed}


def is_rsc(url):
    return any(unquote_plus(p.split('=', 1)[0]) == '_rsc' for p in urlsplit(url).query.split('&'))


def protocol_identity(url, headers):
    h = protocol_headers(headers)
    if h.get('rsc') != '1':
        return None
    p = urlsplit(url)
    query = '&'.join(v for v in p.query.split('&') if unquote_plus(v.split('=', 1)[0]) != '_rsc')
    # Deployment changes are checked through response equality, never ignored
    # on the strength of the URL alone. Other application query values remain.
    h.pop('x-deployment-id', None)
    for name in ('user-agent','accept','accept-language'):
        h.pop(name,None)
    return urlunsplit((p.scheme, p.netloc, p.path, query, '')), tuple(sorted(h.items()))


def equivalent_rsc(a, b):
    return (a.get('status') == b.get('status') == 200
            and 'text/x-component' in a.get('content_type', '').lower()
            and 'text/x-component' in b.get('content_type', '').lower()
            and not any(r.get('error') or r.get('truncated') for r in (a, b))
            and bool(a.get('sha256')) and a['sha256'] == b.get('sha256')
            and protocol_identity(a['url'], a.get('request_headers')) is not None
            and protocol_identity(a['url'], a.get('request_headers')) == protocol_identity(b['url'], b.get('request_headers')))


def mode_metrics(mode, candidates, rows, run):
    attempted, successful = set(), set()
    boundaries = 0
    completion = run.get('crawl_completion', {})
    audited_requests = completion.get('requests') if completion else None
    method_audit_complete = mode == 'basecrawler' and audited_requests is not None and all('method' in request for request in audited_requests)
    if method_audit_complete:
        rows = [{
            'url': request.get('requested_url', ''),
            'final_url': request.get('final_url') or request.get('requested_url', ''),
            'method': request.get('method'),
            'status': request.get('http_status', 0),
            'error': request.get('error') or ('' if request.get('state') == 'complete' else request.get('state', '')),
        } for request in audited_requests]
    for row in rows:
        req = row if mode == 'basecrawler' else row.get('request', {})
        url = req.get('url') if mode == 'basecrawler' else req.get('endpoint')
        if not url:
            continue
        key = (req.get('method', 'GET'), url)
        status = row.get('status', 0) if mode == 'basecrawler' else row.get('response', {}).get('status_code', 0)
        boundary = row.get('error') == 'max depth reached'
        boundaries += boundary
        if status or (row.get('error') and not boundary):
            attempted.add(key)
        if (200 <= (status or 0) < 300 and not row.get('error') and not row.get('truncated')
                and row.get('final_url', url) == url):
            successful.add(key)
    reasons = {r['code']: r['count'] for r in completion.get('reasons', [])}
    return {'discovered_method_urls': len({(r['method'], r['url']) for r in candidates}),
            'observed_attempted_method_urls': len(attempted),
            'observed_successful_method_urls': len(successful),
            'requests_attempted': completion.get('requests_attempted') if mode == 'basecrawler' else None,
            'request_count_scope': 'crawler dispatches; bootstrap/retries/redirect hops excluded' if mode == 'basecrawler' else 'unknown; response records do not certify total browser network requests',
            'seconds': run['seconds'], 'boundary_discoveries': boundaries,
            'analysis_failures': reasons.get('analysis_failure', 0) if mode == 'basecrawler' else None,
            'execution_limits': {k:v for k,v in reasons.items() if k.startswith(('execution_', 'module_'))},
            'unvisited': len(completion.get('unvisited', [])),
            'unvisited_reasons': dict(Counter(r['reason'] for r in completion.get('unvisited', []))),
            'run_status': run['status']}


def source_execution_metrics(candidates, completion, valid_reference_urls=()):
    """Break Basecrawler discovery and request execution down by provenance."""
    from crawler_compare import normalize_url

    valid = set(valid_reference_urls)
    stats = {}

    def entry(source):
        return stats.setdefault(source, {
            'discovered_method_urls': set(),
            'crawlable_method_urls': set(),
            'reference_targets_discovered': set(),
            'scheduled_method_urls': set(),
            'scheduled_reference_targets': set(),
            'dispatches': 0,
            'attempted_method_urls': set(),
            'successful_http_responses': 0,
            'exact_successful_method_urls': set(),
            'successful_method_urls': set(),
            'reference_targets_attempted': set(),
            'reference_targets_successful': set(),
            'unvisited_method_urls': set(),
            'unvisited_by_reason': Counter(),
        })

    for row in candidates:
        method = str(row.get('method') or 'GET').upper()
        url = normalize_url(row.get('url') or '')
        if not url:
            continue
        pair = (method, url)
        records = row.get('source_records') or []
        if records:
            by_source = {}
            for record in records:
                source = record.get('source') or 'unknown'
                by_source.setdefault(source, []).append(record)
        else:
            sources = row.get('sources') or [row.get('source') or 'unknown']
            by_source = {source: [{'confidence': row.get('confidence', '')}] for source in sources}
        for source, source_records in by_source.items():
            item = entry(source)
            item['discovered_method_urls'].add(pair)
            confidences = [str(record.get('confidence') or '').lower() for record in source_records]
            if any(confidence not in ('clue', 'template') for confidence in confidences):
                item['crawlable_method_urls'].add(pair)
            if method == 'GET' and url in valid:
                item['reference_targets_discovered'].add(url)

    for request in completion.get('requests') or []:
        source = request.get('source') or 'unknown'
        item = entry(source)
        method = str(request.get('method') or 'GET').upper()
        url = normalize_url(request.get('requested_url') or '')
        if not url:
            continue
        pair = (method, url)
        item['dispatches'] += 1
        item['scheduled_method_urls'].add(pair)
        item['attempted_method_urls'].add(pair)
        if method == 'GET' and url in valid:
            item['scheduled_reference_targets'].add(url)
            item['reference_targets_attempted'].add(url)
        status = int(request.get('http_status') or 0)
        final_url = normalize_url(request.get('final_url') or url)
        response_success = (200 <= status < 300 and not request.get('error')
                            and request.get('state', 'complete') == 'complete')
        if response_success:
            item['successful_http_responses'] += 1
        if response_success and final_url == url:
            item['exact_successful_method_urls'].add(pair)
            item['successful_method_urls'].add(pair)
            if method == 'GET' and url in valid:
                item['reference_targets_successful'].add(url)

    for row in completion.get('unvisited') or []:
        source = row.get('source') or 'unknown'
        item = entry(source)
        method = str(row.get('method') or 'GET').upper()
        url = normalize_url(row.get('url') or '')
        if url:
            pair = (method, url)
            item['scheduled_method_urls'].add(pair)
            item['unvisited_method_urls'].add(pair)
            if method == 'GET' and url in valid:
                item['scheduled_reference_targets'].add(url)
        item['unvisited_by_reason'][row.get('reason') or 'unknown'] += 1

    result = {}
    set_fields = (
        'discovered_method_urls', 'crawlable_method_urls', 'reference_targets_discovered',
        'scheduled_method_urls', 'scheduled_reference_targets',
        'attempted_method_urls', 'successful_method_urls', 'reference_targets_attempted',
        'reference_targets_successful', 'unvisited_method_urls',
    )
    for source, item in sorted(stats.items()):
        output = {key: len(item[key]) for key in set_fields}
        output['dispatches'] = item['dispatches']
        output['successful_http_responses'] = item['successful_http_responses']
        output['exact_successful_method_urls'] = len(item['exact_successful_method_urls'])
        output['unvisited_by_reason'] = dict(sorted(item['unvisited_by_reason'].items()))
        result[source] = output
    return result


def gap_evidence(url, data, base_run, base_rows):
    pending = {r['url']:r for r in base_run.get('crawl_completion', {}).get('unvisited', [])}
    parents = sorted({r.get('referer') for m in ('katana-static','katana-browser')
                      for r in data[m] if r['url'] == url and r.get('referer')})
    if url in pending:
        return {'classification':'discovered_unvisited', 'pending':pending[url]}
    blocked = [pending[p] for p in parents if p in pending]
    if blocked:
        return {'classification':'parent_unvisited', 'parents':blocked}
    scanned = {r['url'] for r in base_rows if 200 <= r.get('status', 0) < 300}
    if any(p in scanned for p in parents):
        return {'classification':'historical_edge_not_rediscovered', 'reference_parents':parents,
                'limitation':'Current parent was fetched; entry drift versus parser omission still requires response-body inspection.'}
    return {'classification':'source_trace_required', 'reference_parents':parents}
