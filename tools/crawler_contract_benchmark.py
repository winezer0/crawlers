"""Local, reproducible HTTP method/parameter/testfill comparison; Windows binaries only."""
from __future__ import annotations

import argparse
import collections
import hashlib
import http.server
import json
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import katana_effective_compare as bench

CONTRACTS = [
    {'method': 'GET', 'path': '/api/widgets/{id}', 'query': [], 'json': [], 'form': []},
    {'method': 'POST', 'path': '/api/widgets/{id}', 'query': [], 'json': ['name'], 'form': []},
    {'method': 'PUT', 'path': '/api/widgets/{id}', 'query': [], 'json': ['name'], 'form': []},
    {'method': 'PATCH', 'path': '/api/widgets/{id}', 'query': [], 'json': ['enabled'], 'form': []},
    {'method': 'DELETE', 'path': '/api/widgets/{id}', 'query': [], 'json': [], 'form': []},
    {'method': 'GET', 'path': '/api/search', 'query': ['q', 'limit'], 'json': [], 'form': []},
    {'method': 'POST', 'path': '/api/form', 'query': [], 'json': [], 'form': ['username', 'role']},
]


def contract_index(method, url):
    path = urlsplit(url).path
    for i, c in enumerate(CONTRACTS):
        if method == c['method'] and re.fullmatch(re.escape(c['path']).replace(r'\{id\}', '[^/]+'), path):
            return i
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seconds', type=int, default=20)
    p.add_argument('--base', type=Path, default=bench.ROOT/'basecrawler/bin/basecrawler.exe')
    p.add_argument('--base-only', action='store_true', help='Run Basecrawler only; do not launch Katana.')
    p.add_argument('--katana-reference', type=Path, help='Reuse saved Katana static/browser contract records.')
    args = p.parse_args()
    if args.katana_reference and not args.base_only:
        p.error('--katana-reference requires --base-only')
    if args.katana_reference:
        args.katana_reference = args.katana_reference.resolve()
        if not (args.katana_reference/'fixture.json').is_file():
            p.error('--katana-reference must point to a completed contract benchmark directory')
    args.output = args.output.resolve(); args.output.mkdir(parents=True, exist_ok=False)
    lock = threading.Lock(); received = []; active = ''

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def handle_request(self):
            path = urlsplit(self.path).path
            body = self.rfile.read(int(self.headers.get('Content-Length', '0'))).decode(errors='replace')
            with lock:
                received.append({'mode': active, 'method': self.command, 'url': origin + self.path, 'body': body, 'content_type': self.headers.get('Content-Type', '')})
            if path == '/':
                content = b'<body data-id="7"><a href="/api/search?q=sample&limit=2">search</a><form method="post" action="/api/form"><input name="username"><select name="role"><option>reader</option><option>editor</option></select><button>send</button></form><script src="/app.js"></script></body>'
                kind = 'text/html'
            elif path == '/app.js': content, kind = compiled.encode(), 'application/javascript'
            elif path == '/app.js.map': content, kind = source_map, 'application/json'
            elif path.startswith('/api/'):
                content, kind = json.dumps({'ok': True, 'method': self.command, 'path': path}).encode(), 'application/json'
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.send_header('Content-Type', kind); self.end_headers(); self.wfile.write(content)
        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_request

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    origin = f'http://127.0.0.1:{server.server_port}'
    js = '''const axios={create:({baseURL})=>({
get:(p)=>fetch(baseURL+p),post:(p,data)=>fetch(baseURL+p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),
put:(p,data)=>fetch(baseURL+p,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),
patch:(p,data)=>fetch(baseURL+p,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),
delete:(p)=>fetch(baseURL+p,{method:'DELETE'})})};
const api=axios.create({baseURL:BASE});const id=document.body.dataset.id;
api.get(`/api/widgets/${id}`);api.post(`/api/widgets/${id}`,{name:'demo'});
api.put(`/api/widgets/${id}`,{name:'demo'});api.patch(`/api/widgets/${id}`,{enabled:true});api.delete(`/api/widgets/${id}`);
api.get('/api/search?q=sample&limit=2');'''
    compiled = js.replace('baseURL:BASE', 'baseURL:' + json.dumps(origin)) + '\n//# sourceMappingURL=/app.js.map'
    source = js.replace('baseURL:BASE', 'baseURL:process.env.REACT_APP_API_URL')
    source_map = json.dumps({'version': 3, 'sources': ['src/api.js'], 'sourcesContent': [source]}).encode()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fixture_sha256 = hashlib.sha256(source.encode()).hexdigest()
    bench.write_json(args.output / 'fixture.json', {'contracts': CONTRACTS, 'origin': origin, 'source_sha256': fixture_sha256})
    modes = [('basecrawler-3', 3)] if args.base_only else [
        ('basecrawler-3', 3), ('basecrawler-5', 5), ('katana-static', 0), ('katana-browser', 0)
    ]
    reports = []
    try:
        if args.katana_reference:
            frozen_fixture = json.loads((args.katana_reference/'fixture.json').read_text(encoding='utf-8'))
            if frozen_fixture.get('source_sha256') != fixture_sha256:
                raise ValueError('Frozen Katana reference fixture source does not match this fixture.')
        for mode, cases in modes:
            active = mode
            folder = args.output / mode; folder.mkdir()
            if cases:
                command = [str(args.base.resolve()), '-u', origin+'/', '-d', '3', '-c', '1', '--max-urls', '15', '--ct', str(args.seconds)+'s',
                           '--no-robots', '--no-sitemap', '--no-llms', '--no-fuzz', '--no-probe-openapi', '--no-route-expand',
                           '--max-cases-per-interface', str(cases), '--testdata', 'json', '--testdata-out', str(folder/'testfill.json'),
                           '--report', str(folder/'report.json'), '--discoveries', str(folder/'discoveries.jsonl'), '-o', str(folder/'results.jsonl'), '-s', 'all']
            else:
                command = [str(bench.ROOT/'othercrawlers/katana.exe'), '-u', origin+'/', '-d', '3', '-c', '1', '-p', '1', '-mdp', '15', '-ct', str(args.seconds)+'s',
                           '-jc', '-jsl', '-fx', '-aff', '-j', '-silent', '-duc', '-or', '-o', str(folder/'results.jsonl')]
                if mode.endswith('browser'):
                    command += ['-hl', '-sc', '-scp', str(bench.common.find_chrome(None)), '-nos', '-xhr', '-ho', 'disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable']
            start=time.perf_counter(); run=subprocess.run(command, capture_output=True, timeout=args.seconds+45); wall=time.perf_counter()-start
            (folder/'stdout.txt').write_bytes(run.stdout); (folder/'stderr.txt').write_bytes(run.stderr)
            binary_sha256 = hashlib.sha256(Path(command[0]).read_bytes()).hexdigest()
            bench.write_json(folder/'run.json', {'command':command, 'seconds':wall, 'exit_code':run.returncode, 'binary_sha256':binary_sha256})
            rows = bench.jsonlines(folder/'results.jsonl') + (bench.jsonlines(folder/'discoveries.jsonl') if cases else [])
            discovered = collections.defaultdict(list)
            for row in rows:
                r = row if cases else row.get('request', {})
                method = (r.get('method') or 'GET').upper(); url = r.get('url') if cases else r.get('endpoint', '')
                index = contract_index(method, url or '')
                if index is not None: discovered[index].append(r)
            confirmations = json.loads((folder/'testfill.json').read_text(encoding='utf-8')) if cases else []
            actual = [r for r in received if r['mode'] == mode]
            details=[]
            for i, contract in enumerate(CONTRACTS):
                observations = [r for r in actual if contract_index(r['method'], r['url']) == i]
                keys={'query':set(), 'json':set(), 'form':set()}; empty_values=set()
                for r in observations:
                    query=parse_qs(urlsplit(r['url']).query,keep_blank_values=True)
                    keys['query'].update(query)
                    empty_values.update(k for k,v in query.items() if all(x=='' for x in v))
                    if 'json' in r['content_type']:
                        try:
                            payload=json.loads(r['body']); keys['json'].update(payload if isinstance(payload,dict) else [])
                        except ValueError: pass
                    else:
                        form=parse_qs(r['body'],keep_blank_values=True)
                        keys['form'].update(form)
                        empty_values.update(k for k,v in form.items() if all(x=='' for x in v))
                generated = [r for r in confirmations if contract_index(r.get('method',''),r.get('url',''))==i]
                missing = {kind:sorted(set(contract[kind])-keys[kind]) for kind in keys}
                details.append({**contract, 'discovered':bool(discovered[i]), 'received':len(observations), 'parameter_keys_received':{k:sorted(v) for k,v in keys.items()},
                                'missing_parameter_keys':missing, 'empty_parameter_values':sorted(empty_values),
                                'testfill_cases':len(generated), 'case_limit_ok':not cases or len(generated)<=cases,
                                'wire_contract_passed':bool(observations) and not any(missing.values()) and not empty_values})
            report={'mode':mode,'seconds':wall,'exit_code':run.returncode,'binary_sha256':binary_sha256,'fixture_source_sha256':fixture_sha256,
                    'configured_cases_per_interface':cases or None,'contracts':details,
                    'wire_contracts_passed':sum(r['wire_contract_passed'] for r in details), 'contract_count':len(CONTRACTS),
                    'note':'Local fixture only. Browser requests and navigation are counted separately from generated testfill cases.'}
            bench.write_json(folder/'contracts.json',report);reports.append(report);print(json.dumps(report),flush=True)
    finally: server.shutdown()
    if args.katana_reference:
        frozen_reports = []
        for mode in ('katana-static', 'katana-browser'):
            folder = args.katana_reference/mode
            report = json.loads((folder/'contracts.json').read_text(encoding='utf-8'))
            run = json.loads((folder/'run.json').read_text(encoding='utf-8'))
            report.update({'binary_sha256':run['binary_sha256'], 'fixture_source_sha256':fixture_sha256,
                           'frozen_reference':True, 'reference_path':str(args.katana_reference)})
            frozen_reports.append(report)
        reports = frozen_reports + reports
    (args.output/'received.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in received),encoding='utf-8')
    bench.write_json(args.output/'comparison.json',reports)
    lines=['# 本地接口契约对比','','| 工具 | 秒 | 已发送完整契约 | 每接口用例上限通过 |','|---|---:|---:|---|']
    for r in reports:
        label = r['mode'] + (' (frozen)' if r.get('frozen_reference') else '')
        lines.append(f"| {label} | {r['seconds']:.3f} | {r['wire_contracts_passed']}/{r['contract_count']} | {all(c['case_limit_ok'] for c in r['contracts'])} |")
    lines+=['','统计实际收到的方法与参数；生成用例数与导航、浏览器运行时请求分别核对。该结果不代表真实站点业务成功率。']
    if args.katana_reference:
        lines += ['', f'Katana rows are reused from `{args.katana_reference}`. Fixture source SHA-256 matched; no Katana process was launched.']
    (args.output/'summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__ == '__main__': main()
