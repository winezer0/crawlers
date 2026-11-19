"""Trace every missing benchmark URL to saved source and traversal evidence."""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

import katana_effective_compare as bench


def norm(value):
    return bench.common.normalize_url(value or '')


def explain(observations, fetched, discovered, depth, reasons):
    evidence = []
    for row in observations:
        parent = norm(row.get('source')) or norm(row.get('headers', {}).get('Referer'))
        if not parent and not row.get('reference_mode'):
            continue
        info = {'parent': parent, 'tag': row.get('tag', ''), 'attribute': row.get('attribute', '')}
        if row.get('reference_mode'):
            info['reference_mode'] = row['reference_mode']
            if row.get('reference_error'):
                info['katana_observation'] = 'candidate_only:' + str(row['reference_error'])
            elif row.get('reference_status') is not None:
                info['katana_observation'] = 'response:' + str(row['reference_status'])
            else:
                info['katana_observation'] = 'candidate_only_without_response'
        parents = fetched.get(parent, [])
        links = discovered.get(parent, [])
        if parents:
            info['base_parent_results'] = [{k: p[k] for k in ('depth', 'status', 'error') if k in p} for p in parents]
            if any(p.get('error') or p.get('status', 200) >= 400 for p in parents):
                info['finding'] = 'parent_request_failed'
            elif all(p.get('depth', 0) >= depth for p in parents):
                info['finding'] = 'parent_at_depth_boundary'
            else:
                info['finding'] = 'parent_fetched_requires_parser_or_runtime_reproduction'
        elif links:
            info['base_parent_discoveries'] = [{k: p[k] for k in ('depth', 'source', 'referer') if k in p} for p in links]
            if all(p.get('depth', 0) > depth for p in links):
                info['finding'] = 'parent_discovered_beyond_depth'
            elif reasons & {'url_limit', 'duration_limit', 'timeout'}:
                info['finding'] = 'parent_not_fetched_with_budget_limit'
            else:
                info['finding'] = 'parent_discovered_not_fetched_requires_filter_trace'
        else:
            info['finding'] = 'parent_not_observed_requires_discovery_trace'
        if info not in evidence:
            evidence.append(info)
    return evidence or [{'finding': 'reference_has_no_saved_source_requires_reproduction'}]


def analyze(root, output_root=None):
    output_root = (output_root or root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
    frozen = json.loads((Path(config['reference']) / 'frozen.json').read_text(encoding='utf-8')) if 'reference' in config else None
    references = collections.defaultdict(list)
    folders = sorted(p for p in root.glob('round-*/*') if p.is_dir())
    for folder in folders:
        for mode in bench.MODES[1:]:
            for row in bench.jsonlines(folder / mode / 'results.jsonl'):
                request = row.get('request', {})
                url = norm(request.get('endpoint'))
                if url:
                    response = row.get('response') or {}
                references[(folder.name, url)].append({
                    **request,
                    'reference_mode': mode,
                    'reference_error': row.get('error', ''),
                        'reference_status': response.get('status_code'),
                })
    details = []
    for folder in folders:
        report_path = folder / 'effective.json'
        if not report_path.exists():
            raise ValueError('Verification unfinished: ' + str(folder))
        report = json.loads(report_path.read_text(encoding='utf-8'))
        fetched, discovered = collections.defaultdict(list), collections.defaultdict(list)
        for name, index in [('results.jsonl', fetched), ('discoveries.jsonl', discovered)]:
            for row in bench.jsonlines(folder / 'basecrawler' / name):
                index[norm(row.get('url'))].append(row)
        reasons = {r['code'] for r in report['runs']['basecrawler'].get('crawl_completion', {}).get('reasons', [])}
        for url in report['missing']:
            details.append({'round': folder.parent.name, 'site': folder.name, 'url': url,
                            'evidence': explain(references[(folder.name, url)], fetched, discovered,
                                                frozen['sites'][folder.name]['settings']['depth'] if frozen else config['depth'], reasons),
                            'note': 'Source evidence classifies the next diagnostic step; it does not prove a parser defect.'})
    bench.write_json(output_root / 'gap-analysis.json', details)
    counts = collections.Counter((d['site'], e['finding']) for d in details for e in d['evidence'])
    reference_counts = collections.Counter(
        (d['site'], e['reference_mode'], e['katana_observation'])
        for d in details for e in d['evidence'] if e.get('reference_mode')
    )
    lines = ['# 缺失 URL 来源核对', '', '逐条证据见 gap-analysis.json。每个 URL 可有多个来源；下表统计来源证据，不等于独立 URL 数。', '',
             '| 站点 | 证据分类 | 数量 |', '|---|---|---:|']
    lines += [f'| {site} | {finding} | {count} |' for (site, finding), count in sorted(counts.items())]
    lines += ['', '## Katana reference outcome', '',
              '| Site | Mode | Reference outcome | Count |', '|---|---|---|---:|']
    lines += [f'| {site} | {mode} | {outcome} | {count} |'
              for (site, mode, outcome), count in sorted(reference_counts.items())]
    lines += ['', '深度/预算截断不直接证明解析缺陷；父页面已抓取也需要对原始脚本和运行时加载进一步复现。网络失败、未核验 URL 仍以 effective.json 为准。']
    (output_root / 'gap-analysis.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'missing_round_url_pairs': len(details), 'source_evidence': sum(counts.values())}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, help='Write reports here; defaults to --input.')
    args = parser.parse_args()
    analyze(args.input.resolve(), args.output.resolve() if args.output else None)
