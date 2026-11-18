"""Repeat a uniform three-mode benchmark with frozen reference and HTTP evidence."""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import itertools
import json
import math
import shutil
import statistics
from pathlib import Path

import katana_effective_compare as bench


def jaccard(a, b):
    return len(a & b) / len(a | b) if a | b else 1.0


def aggregate(output, targets, rounds):
    sites = []
    for target in targets:
        reports = [json.loads((output / f'round-{n:02}' / target.site_id / 'effective.json').read_text(encoding='utf-8')) for n in range(1, rounds + 1)]
        modes = {}
        for mode in bench.MODES:
            sets = [{(r['method'], r['url']) for r in json.loads((output / f'round-{n:02}' / target.site_id / mode / 'candidates.json').read_text(encoding='utf-8'))} for n in range(1, rounds + 1)]
            seconds = [r['runs'][mode]['seconds'] for r in reports]
            modes[mode] = {'seconds': seconds, 'mean_seconds': statistics.mean(seconds), 'median_seconds': statistics.median(seconds),
                           'min_jaccard': min((jaccard(a, b) for a, b in itertools.combinations(sets, 2)), default=1),
                           'statuses': [r['runs'][mode]['status'] for r in reports]}
        site = {'site': target.site_id, 'modes': modes, 'coverage_by_round': [r['coverage'] for r in reports],
                'valid_reference_by_round': [r['verified_valid_reference'] for r in reports],
                'unresolved_by_round': [len(r['unresolved']) for r in reports],
                'missing_by_round': [r['missing'] for r in reports],
                'passed': rounds >= 3 and all(r['passed'] for r in reports) and all(m['min_jaccard'] >= .90 for m in modes.values())}
        sites.append(site)
    result = {'rounds': rounds, 'passed_sites': sum(s['passed'] for s in sites), 'required_sites': len(targets), 'sites': sites,
              'goal_met': rounds >= 3 and sum(s['passed'] for s in sites) >= math.ceil(.8 * len(targets)),
              'criterion': 'Each round >=95% frozen union GET coverage, usable references, no unresolved URLs; >=3 rounds and all modes Jaccard >=0.90.',
              'limitations': ['The cohort shares one HTTP evidence snapshot; it does not measure three independent HTTP availabilities.',
                              'Configured duration/page budgets bound traversal. Wall times are not normalized work throughput.',
                              'Methods, parameters and runtime-only business flows require the separate contract benchmark.']}
    bench.write_json(output / 'regression.json', result)
    lines = ['# 三轮统一对标', '', '| 站点 | base 均秒 | Katana 静态均秒 | Katana 浏览器均秒 | 三轮覆盖 | 最低稳定度 | 正式通过 |', '|---|---:|---:|---:|---|---:|---|']
    for s in sites:
        timings = [f"{s['modes'][m]['mean_seconds']:.2f}" for m in bench.MODES]
        coverage = ', '.join(f'{v:.2%}' if v is not None else 'n/a' for v in s['coverage_by_round'])
        lines.append('| ' + ' | '.join([s['site'], *timings, coverage, f"{min(m['min_jaccard'] for m in s['modes'].values()):.3f}", str(s['passed'])]) + ' |')
    lines += ['', f"正式通过 {result['passed_sites']}/{len(targets)}；总目标达成：{result['goal_met']}。", '', *result['limitations']]
    (output / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--targets', default='testasp,testaspnet,framework-vue,framework-react,framework-angular,framework-nextjs,framework-sveltekit')
    p.add_argument('--rounds', type=int, default=3)
    p.add_argument('--seconds', type=int, default=60)
    p.add_argument('--pages', type=int, default=80)
    p.add_argument('--depth', type=int, default=2)
    p.add_argument('--rate', type=int, default=2)
    p.add_argument('--workers', type=int, default=3)
    p.add_argument('--strategy', choices=['breadth-first', 'depth-first'], default='breadth-first')
    p.add_argument('--proxy', default='')
    p.add_argument('--base', type=Path, default=bench.ROOT / 'basecrawler/bin/basecrawler.exe')
    p.add_argument('--reference-runs-from', type=Path, help='reuse explicitly recorded Katana runs with identical target/budget/proxy configuration')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--summarize-only', action='store_true')
    p.add_argument('--verify-only', action='store_true', help='reuse saved runs and only finish HTTP verification/reporting')
    args = p.parse_args()
    if min(args.rounds, args.seconds, args.pages, args.depth, args.rate, args.workers) < 1:
        p.error('rounds, seconds, pages, depth, rate, workers must be positive')
    args.output = args.output.resolve()
    targets = [bench.common.TARGETS[t] for t in args.targets.split(',')]
    if args.summarize_only:
        aggregate(args.output, targets, args.rounds)
        return
    config = vars(args).copy()
    config.pop('resume'); config.pop('summarize_only'); config.pop('verify_only'); config.pop('output')
    config = {k:str(v.resolve()) if isinstance(v,Path) else v for k,v in config.items()}
    config['base_sha256'] = hashlib.sha256(args.base.read_bytes()).hexdigest()
    config['katana_sha256'] = hashlib.sha256((bench.ROOT / 'othercrawlers/katana.exe').read_bytes()).hexdigest()
    reference_copies=[]
    if args.reference_runs_from:
        args.reference_runs_from=args.reference_runs_from.resolve()
        previous=json.loads((args.reference_runs_from/'config.json').read_text(encoding='utf-8'))
        for key in ('targets','rounds','seconds','pages','depth','rate','strategy','proxy','katana_sha256'):
            if previous.get(key)!=config.get(key):p.error('reference configuration differs: '+key)
        for n in range(1,args.rounds+1):
            for target in targets:
                for mode in bench.MODES[1:]:
                    src=args.reference_runs_from/f'round-{n:02}'/target.site_id/mode
                    if not (src/'run.json').exists():p.error('reference run is unfinished: '+str(src))
                    record=json.loads((src/'run.json').read_text(encoding='utf-8'))
                    if record['binary_sha256']!=config['katana_sha256']:p.error('reference binary mismatch: '+str(src))
                    dst=args.output/f'round-{n:02}'/target.site_id/mode
                    reference_copies.append((src,dst))
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = args.output / 'config.json'
    if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8')) != config:
        p.error('output configuration/binary changed; choose a new output directory')
    if manifest.exists() and not args.resume:
        p.error('output already exists; use --resume or a new directory')
    bench.write_json(manifest, config)
    for src,dst in reference_copies:
        if not dst.exists():
            shutil.copytree(src,dst)
            bench.write_json(dst/'provenance.json',{'copied_from':str(src),'original_command_preserved':True})
    args.base = args.base.resolve()
    args.chrome = bench.common.find_chrome(None)
    if not args.chrome:
        p.error('Chrome is required for the browser reference')
    args.verify_timeout = 12; args.verify_workers = 4; args.verify_limit = 0
    root = args.output
    for n in ([] if args.verify_only else range(1, args.rounds + 1)):
        run_args = copy.copy(args); run_args.output = root / f'round-{n:02}'
        order = bench.MODES[(n-1) % 3:] + bench.MODES[:(n-1) % 3]
        def run_site(target):
            for mode in order:
                if args.reference_runs_from and mode!='basecrawler':continue
                bench.run_one(target, mode, run_args)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(run_site, targets))
    reference = root / 'reference'
    for target in targets:
        for mode in bench.MODES[1:]:
            rows = {}
            for n in range(1, args.rounds + 1):
                for row in json.loads((root / f'round-{n:02}' / target.site_id / mode / 'candidates.json').read_text(encoding='utf-8')):
                    rows[json.dumps(row, sort_keys=True)] = row
            bench.write_json(reference / target.site_id / (mode + '.json'), list(rows.values()))
    args.reference_snapshot = reference
    for n in range(1, args.rounds + 1):
        verify_args = copy.copy(args); verify_args.output = root / f'round-{n:02}'
        if n > 1:
            for target in targets:
                for name in ('verification.jsonl', 'control.json'):
                    src = root / f'round-{n-1:02}' / target.site_id / name
                    dst = verify_args.output / target.site_id / name
                    if src.exists() and not dst.exists(): shutil.copy2(src, dst)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(lambda t: bench.verify_site(t, verify_args), targets))
        bench.summarize(verify_args, targets)
    aggregate(root, targets, args.rounds)


if __name__ == '__main__':
    main()
