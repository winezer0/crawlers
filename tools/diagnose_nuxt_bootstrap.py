"""Replay recorded Nuxt modules offline; controls are diagnostics, not acceptance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urljoin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--traffic', type=Path, required=True)
    parser.add_argument('--modules', type=Path, required=True)
    parser.add_argument('--test-binary', type=Path, required=True)
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    records = [json.loads(line) for line in args.traffic.read_text(encoding='utf-8').split('\n') if line.strip()]
    document = next(r for r in records if r.get('resource_type') == 'document' and r.get('status') == 200)
    html, url = document['body'], document['url']
    (out / 'document.html').write_text(html, encoding='utf-8')
    scripts = re.findall(r'<script\b([^>]*)>(.*?)</script\s*>', html, re.S | re.I)
    entry = next(urljoin(url, re.search(r'\bsrc="([^"]+)"', attrs)[1]) for attrs, _ in scripts
                 if re.search(r'\btype="module"', attrs) and re.search(r'\bsrc="([^"]+)"', attrs))
    config = next(body for attrs, body in scripts if not attrs.strip() and 'window.__NUXT__.config=' in body)
    manifest, hashes = {}, {}
    for path in args.modules.glob('*.js'):
        module_url = urljoin(entry, path.name)
        data = path.read_bytes()
        saved = out / path.name
        saved.write_bytes(data)
        manifest[module_url] = str(saved)
        recorded = next((r['body'] for r in records if r.get('url') == module_url and isinstance(r.get('body'), str)), None)
        hashes[module_url] = {'sha256': hashlib.sha256(data).hexdigest(),
                              'recorded_body_matches': recorded.encode('utf-8') == data if recorded is not None else None}
    (out / 'modules.json').write_text(json.dumps(manifest), encoding='utf-8')
    runtime = Path(manifest[entry]).read_text(encoding='utf-8')
    cases = {'page-entry': f'import {json.dumps(entry)};',
             'runtime': runtime,
             'runtime-observed-config-control': config + '\n' + runtime,
             'page-observed-config-control': config + '\n' + f'import {json.dumps(entry)};'}
    summaries = {}
    for name, source in cases.items():
        fixture, report = out / (name + '.js'), out / (name + '.json')
        fixture.write_text(source, encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if not k.startswith('ROUTE_EXEC_')}
        env.update(ROUTE_EXEC_FIXTURE=str(fixture), ROUTE_EXEC_SOURCE_URL=entry,
                   ROUTE_EXEC_DOCUMENT_URL=url, ROUTE_EXEC_DOCUMENT_HTML=str(out / 'document.html'),
                   ROUTE_EXEC_MODULE_MANIFEST=str(out / 'modules.json'), ROUTE_EXEC_TIMEOUT='30s',
                   ROUTE_EXEC_REPORT=str(report), ROUTE_EXEC_EXPLORE_INTERACTIONS='1')
        result = subprocess.run([str(args.test_binary.resolve()), '-test.run', '^TestDownloadedRuntimeFixture$', '-test.v'],
                                env=env, capture_output=True, timeout=45)
        (out / (name + '.log')).write_bytes(result.stdout + result.stderr)
        data = json.loads(report.read_text(encoding='utf-8')) if report.exists() else {}
        summaries[name] = {k: data.get(k) for k in ('status', 'error', 'elapsed_seconds', 'requests', 'runtime_state', 'runtime_diagnostics', 'environment_gaps')}
        summaries[name]['exit_code'] = result.returncode
        print(name, json.dumps(summaries[name], ensure_ascii=False), flush=True)
    (out / 'summary.json').write_text(json.dumps({'cases': summaries, 'inputs': hashes, 'network_requests': 0,
        'coverage_accepted': False, 'controls_modify_fixture_only': True}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
