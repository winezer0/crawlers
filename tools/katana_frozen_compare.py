"""Freeze one maximum Katana run per site/mode; subsequently execute only basecrawler."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import shutil
import statistics
from pathlib import Path
from types import SimpleNamespace

import katana_effective_compare as bench
from katana_regression import jaccard


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scoring_fingerprint():
    return {name:digest(Path(__file__).parent/name) for name in
            ('katana_frozen_compare.py','katana_effective_compare.py','crawler_metrics.py')}


def select_maximum(folders):
    # Count distinct method/URL pairs, not duplicate output lines or parameter
    # annotations. Never combine different runs to construct a larger reference.
    return max(sorted(folders), key=lambda p: len({(r['method'], r['url']) for r in read(p / 'candidates.json')}))


def freeze(sources, output):
    if output.exists():
        raise ValueError('Frozen directory already exists; choose a new version')
    choices, settings = {}, {}
    keys = ('seconds', 'pages', 'depth', 'rate', 'strategy', 'proxy', 'katana_sha256')
    for source in sources:
        config = read(source / 'config.json')
        for site in config['targets'].split(','):
            profile = {k: config[k] for k in keys}
            if site in settings and settings[site] != profile:
                raise ValueError('Incompatible reference configurations for ' + site)
            settings[site] = profile
            for mode in bench.MODES[1:]:
                choices.setdefault((site, mode), []).extend(
                    p for p in sorted(source.glob('round-*/' + site + '/' + mode))
                    if (p / 'run.json').exists() and (p / 'candidates.json').exists())
    selected = {key: select_maximum(paths) for key, paths in choices.items()}
    manifest = {'policy': 'one maximum distinct method/URL run per site/mode, no cross-round union', 'sites': {}}
    for site in sorted(settings):
        info = {'settings': settings[site], 'modes': {}}
        for mode in bench.MODES[1:]:
            src = selected[(site, mode)]
            dst = output / site / mode
            shutil.copytree(src, dst)
            info['modes'][mode] = {'source': str(src.resolve()), 'run': read(dst / 'run.json'),
                                   'candidates_sha256': digest(dst / 'candidates.json'),
                                   'results_sha256': digest(dst / 'results.jsonl')}
        # Reuse saved HTTP evidence, independent of which crawl run was chosen.
        evidence = [p for (s, _), folders in choices.items() if s == site for f in folders
                    if (p := f.parent / 'verification.jsonl').exists()]
        if evidence:
            src = max(sorted(set(evidence)), key=lambda p: len(bench.jsonlines(p)))
            shutil.copy2(src, output / site / 'verification.jsonl')
            if (src.parent / 'control.json').exists():
                shutil.copy2(src.parent / 'control.json', output / site / 'control.json')
            info['http_evidence_source'] = str(src.resolve())
        manifest['sites'][site] = info
    bench.write_json(output / 'frozen.json', manifest)
    return manifest


def validate_reference(reference):
    manifest = read(reference / 'frozen.json')
    for site, info in manifest['sites'].items():
        for mode, saved in info['modes'].items():
            folder = reference / site / mode
            if digest(folder / 'candidates.json') != saved['candidates_sha256'] or digest(folder / 'results.jsonl') != saved['results_sha256']:
                raise ValueError('Frozen reference changed: ' + site + '/' + mode)
            if read(folder / 'run.json') != saved['run']:
                raise ValueError('Frozen run metadata changed: ' + site + '/' + mode)
    return manifest


def summarize(output, targets, rounds, base_concurrency=1):
    sites = []
    for target in targets:
        reports = [read(output / f'round-{n:02}' / target.site_id / 'effective.json') for n in range(1, rounds + 1)]
        sets = [{(r['method'], r['url']) for r in read(output / f'round-{n:02}' / target.site_id / 'basecrawler/candidates.json')}
                for n in range(1, rounds + 1)]
        stability = min((jaccard(a, b) for i, a in enumerate(sets) for b in sets[i+1:]), default=1)
        sites.append({'site': target.site_id, 'coverage': [r['coverage'] for r in reports],
                      'discovery_coverage': [r.get('discovery_coverage', r['coverage']) for r in reports],
                      'successful_reference_coverage': [r.get('successful_reference_coverage') for r in reports],
                      'crawl_complete': [r.get('crawl_complete', False) for r in reports],
                      'strict_failure_reasons': [r.get('strict_failure_reasons', []) for r in reports],
                      'metrics': [read(output / f'round-{n:02}' / target.site_id / 'metrics.json')
                                  for n in range(1, rounds + 1)
                                  if (output / f'round-{n:02}' / target.site_id / 'metrics.json').exists()],
                      'base_seconds': [r['runs']['basecrawler']['seconds'] for r in reports],
                      'base_concurrency': base_concurrency,
                      'reference_seconds': {m: reports[0]['runs'][m]['seconds'] for m in bench.MODES[1:]},
                      'base_stability': stability, 'missing': [r['missing'] for r in reports],
                      'passed': rounds >= 3 and stability >= .9 and all(r['passed'] for r in reports)})
    result = {'acceptance_version': 'complete-crawl-v2', 'sites': sites,
              'base_concurrency': base_concurrency,
              'passed_sites': sum(s['passed'] for s in sites), 'rounds': rounds,
              'goal_met': sum(s['passed'] for s in sites) >= math.ceil(.8 * len(sites)),
              'reference_policy': 'frozen single maximum per site/mode; no Katana process executed',
              'limitations': ['Reference times are historical single runs; base times are new bounded-budget runs.',
                              'Saved HTTP evidence is reused. This does not measure fresh reference-site availability.']}
    bench.write_json(output / 'regression.json', result)
    lines = ['# 固定 Katana 基线对比', '', '| 站点 | base 均秒 | Katana 静态历史秒 | Katana 浏览器历史秒 | 各轮覆盖 | base 稳定度 | 通过 |', '|---|---:|---:|---:|---|---:|---|']
    for s in sites:
        cov = ', '.join(f'{c:.2%}' if c is not None else 'n/a' for c in s['coverage'])
        lines.append(f"| {s['site']} | {statistics.mean(s['base_seconds']):.2f} | {s['reference_seconds']['katana-static']:.2f} | {s['reference_seconds']['katana-browser']:.2f} | {cov} | {s['base_stability']:.3f} | {s['passed']} |")
    lines += ['', f"正式通过 {result['passed_sites']}/{len(sites)}；总体目标达成：{result['goal_met']}。", '', result['reference_policy'], *result['limitations']]
    lines += ['', '## Discovery and execution metrics (separate from strict acceptance)', '',
              '| Site | Round | Discovered method/URL | Observed successful method/URL | Dispatch attempts | Analysis failures | Unvisited | Seconds |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for s in sites:
        for n, metric in enumerate(s['metrics'], 1):
            m = metric['modes']['basecrawler']
            lines.append(f"| {s['site']} | {n} | {m['discovered_method_urls']} | {m['observed_successful_method_urls']} | {m['requests_attempted']} | {m['analysis_failures']} | {m['unvisited']} | {m['seconds']:.2f} |")
    lines += ['', '## Discovery, request execution, and completion', '',
              '| Site | Base c | Round | Discovery | Exact successful GET | Crawl complete | Unvisited | Strict failure reasons |',
              '|---|---:|---:|---:|---:|---|---:|---|']
    for s in sites:
        for n, metric in enumerate(s['metrics'], 1):
            m = metric['modes']['basecrawler']
            execution = metric.get('reference_execution', {})
            discovery = metric.get('discovery_coverage')
            success = execution.get('successful_reference_coverage')
            discovery_text = f'{discovery:.2%}' if discovery is not None else 'n/a'
            success_text = f'{success:.2%}' if success is not None else 'n/a'
            failures = s['strict_failure_reasons'][n-1] if n-1 < len(s['strict_failure_reasons']) else []
            lines.append(f"| {s['site']} | {s['base_concurrency']} | {n} | {discovery_text} | {success_text} | {s['crawl_complete'][n-1]} | {m['unvisited']} | {', '.join(failures) or 'none'} |")
    lines += ['', 'Katana network totals and JavaScript analysis failures are unknown, not zero. See each site metrics.json for separate static/browser reference coverage.',
              'RSC verification replays observed protocol headers; ordinary HTML GET cannot certify a Flight response. Cache-token aliases require matching context and response hashes.']
    (output / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return result


def run(args):
    manifest = validate_reference(args.reference)
    target_ids = args.targets.split(',') if args.targets else list(manifest['sites'])
    unknown = set(target_ids) - (manifest['sites'].keys() & bench.common.TARGETS.keys())
    if unknown:
        raise ValueError('Targets absent from frozen reference or registry: ' + ', '.join(sorted(unknown)))
    targets = [bench.common.TARGETS[s] for s in target_ids]
    base_concurrency = getattr(args, 'base_concurrency', 1)
    if base_concurrency < 1:
        raise ValueError('Basecrawler concurrency must be positive')
    control_urls = getattr(args, 'control_urls', {})
    config = {'reference': str(args.reference.resolve()), 'reference_sha256': digest(args.reference / 'frozen.json'),
              'scoring_sha256':scoring_fingerprint(),
              'base': str(args.base.resolve()), 'base_sha256': digest(args.base), 'targets': target_ids,
              'rounds': args.rounds, 'base_concurrency': base_concurrency, 'control_urls': control_urls}
    if (args.output / 'config.json').exists():
        if not args.resume or read(args.output / 'config.json') != config:
            raise ValueError('Output configuration changed; use a new directory')
    bench.write_json(args.output / 'config.json', config)
    for n in range(1, args.rounds + 1):
        def run_site(target):
            site = target.site_id
            options = SimpleNamespace(**{k: v for k, v in manifest['sites'][site]['settings'].items() if k != 'katana_sha256'},
                                      base=args.base.resolve(), output=args.output / f'round-{n:02}', resume=args.resume,
                                      base_concurrency=base_concurrency, control_urls=control_urls,
                                      verify_timeout=12, verify_workers=4, verify_limit=0)
            folder = options.output / site
            for mode in bench.MODES[1:]:
                if not (folder / mode).exists(): shutil.copytree(args.reference / site / mode, folder / mode)
            evidence = args.reference / site if n == 1 else args.output / f'round-{n-1:02}' / site
            for name in ('verification.jsonl', 'control.json'):
                if (evidence / name).exists() and not (folder / name).exists(): shutil.copy2(evidence / name, folder / name)
            if not args.verify_only:
                bench.run_one(target, 'basecrawler', options)
            bench.verify_site(target, options)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(run_site, targets))
    summarize(args.output, targets, args.rounds, base_concurrency)


def compare_saved(args):
    """Re-score saved base runs against exactly the same frozen reference."""
    manifest = validate_reference(args.reference)
    if args.output.exists() and not args.resume:
        raise ValueError('Choose a new saved-comparison output directory')
    sites = {}
    for source in args.source:
        for folder in sorted(source.glob('round-*/*')):
            if getattr(args,'targets','') and folder.name not in args.targets.split(','):
                continue
            if folder.name in manifest['sites'] and (folder / 'basecrawler/run.json').exists():
                sites.setdefault(folder.name, []).append(folder)
    control_urls = getattr(args, 'control_urls', {})
    bench.write_json(args.output / 'config.json', {'reference': str(args.reference.resolve()), 'saved_sources': [str(p.resolve()) for p in args.source], 'scoring_sha256':scoring_fingerprint(), 'control_urls': control_urls})
    for site, folders in sites.items():
        for n, folder in enumerate(folders, 1):
            output = args.output / f'round-{n:02}' / site
            shutil.copytree(folder / 'basecrawler', output / 'basecrawler', dirs_exist_ok=args.resume)
            for mode in bench.MODES[1:]:
                shutil.copytree(args.reference / site / mode, output / mode, dirs_exist_ok=args.resume)
            for name in ('verification.jsonl', 'control.json'):
                if (folder / name).exists(): shutil.copy2(folder / name, output / name)
            options = SimpleNamespace(**{k:v for k,v in manifest['sites'][site]['settings'].items() if k!='katana_sha256'},
                                      output=output.parent, control_urls=control_urls,
                                      verify_timeout=12, verify_workers=4, verify_limit=0)
            bench.verify_site(bench.common.TARGETS[site], options)
    bench.write_json(args.output / 'provenance.json', {'policy': 'Saved base results re-scored; no crawler executed', 'rounds': {s: len(f) for s, f in sites.items()}})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    f = sub.add_parser('freeze'); f.add_argument('--source', type=Path, action='append', required=True); f.add_argument('--output', type=Path, required=True)
    r = sub.add_parser('run'); r.add_argument('--reference', type=Path, required=True); r.add_argument('--output', type=Path, required=True)
    r.add_argument('--base', type=Path, default=bench.ROOT / 'basecrawler/bin/basecrawler.exe'); r.add_argument('--targets', default='')
    r.add_argument('--rounds', type=int, default=3); r.add_argument('--workers', type=int, default=3)
    r.add_argument('--base-concurrency', type=int, default=1)
    r.add_argument('--control-url', action='append', default=[], metavar='SITE_ID=URL')
    r.add_argument('--resume', action='store_true'); r.add_argument('--verify-only', action='store_true')
    c = sub.add_parser('compare-saved'); c.add_argument('--reference', type=Path, required=True)
    c.add_argument('--source', type=Path, action='append', required=True); c.add_argument('--output', type=Path, required=True)
    c.add_argument('--resume', action='store_true')
    c.add_argument('--targets', default='', help='limit saved-run re-scoring without executing crawlers')
    c.add_argument('--control-url', action='append', default=[], metavar='SITE_ID=URL')
    args = parser.parse_args()
    try:
        args.control_urls = bench.parse_control_url_overrides(getattr(args, 'control_url', []))
    except ValueError as e:
        parser.error(str(e))
    if args.action == 'freeze': freeze(args.source, args.output)
    elif args.action == 'compare-saved': compare_saved(args)
    else:
        if args.rounds < 1 or args.workers < 1: parser.error('rounds/workers must be positive')
        run(args)
