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
