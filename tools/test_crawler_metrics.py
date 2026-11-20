import unittest
from crawler_metrics import equivalent_rsc, mode_metrics, source_execution_metrics
from katana_effective_compare import classify


class ProtocolMetricsTests(unittest.TestCase):
    def row(self, token='a', next_url='/docs'):
        return dict(url='https://example.test/learn?x=1&_rsc='+token, status=200,
                    content_type='text/x-component', sha256='same',
                    request_headers={'Rsc':'1','Next-Url':next_url})

    def test_rsc_token_equivalence_requires_protocol_and_response(self):
        self.assertTrue(equivalent_rsc(self.row('a'),self.row('b')))
        self.assertFalse(equivalent_rsc(self.row(),self.row(next_url='/other')))
        b=self.row('b'); b['sha256']='different'
        self.assertFalse(equivalent_rsc(self.row(),b))
        b=self.row('b'); b['url']=b['url'].replace('x=1','x=2')
        self.assertFalse(equivalent_rsc(self.row(),b))

    def test_plain_get_html_cannot_verify_rsc(self):
        row=self.row(); row['content_type']='text/html'
        self.assertEqual(classify(row,{}),'protocol_fallback')
        row.pop('request_headers')
        self.assertEqual(classify(row,{}),'protocol_context_missing')
        self.assertEqual(classify(self.row(),{}),'valid_rsc')

    def test_depth_boundary_is_discovery_not_attempt(self):
        rows=[{'request':{'endpoint':'https://example.test/b','method':'GET'},'error':'max depth reached'},
              {'request':{'endpoint':'https://example.test/a','method':'GET'},'response':{'status_code':200}}]
        candidates=[{'url':'https://example.test/'+x,'method':'GET'} for x in ('a','b')]
        m=mode_metrics('katana-static',candidates,rows,{'seconds':1.2,'status':'completed'})
        self.assertEqual(m['discovered_method_urls'],2)
        self.assertEqual(m['observed_attempted_method_urls'],1)
        self.assertEqual(m['observed_successful_method_urls'],1)
        self.assertIsNone(m['requests_attempted'])
        self.assertIsNone(m['analysis_failures'])

    def test_base_success_excludes_truncated_and_error_responses(self):
        rows = [
            {'url': 'https://example.test/ok', 'method': 'GET', 'status': 200},
            {'url': 'https://example.test/truncated', 'method': 'GET', 'status': 200, 'truncated': True},
            {'url': 'https://example.test/error', 'method': 'GET', 'status': 200, 'error': 'analysis failed'},
            {'url': 'https://example.test/not-found', 'method': 'GET', 'status': 404},
            {'url': 'https://example.test/boundary', 'method': 'GET', 'error': 'max depth reached'},
        ]
        candidates = [{'url': row['url'], 'method': 'GET'} for row in rows]

        metrics = mode_metrics('basecrawler', candidates, rows, {
            'seconds': 1.0, 'status': 'completed',
            'crawl_completion': {'requests_attempted': 4, 'unvisited': []},
        })

        self.assertEqual(metrics['observed_attempted_method_urls'], 4)
        self.assertEqual(metrics['observed_successful_method_urls'], 1)

    def test_base_metrics_use_request_audit_before_fsu_filtered_rows(self):
        completion = {'requests': [
            {'requested_url': 'https://example.test/page', 'final_url': 'https://example.test/page',
             'method': 'GET', 'http_status': 200, 'state': 'complete'},
            {'requested_url': 'https://example.test/redirect', 'final_url': 'https://example.test/page',
             'method': 'GET', 'http_status': 200, 'state': 'complete'},
            {'requested_url': 'https://example.test/update', 'final_url': 'https://example.test/update',
             'method': 'POST', 'http_status': 200, 'state': 'complete'},
        ]}
        candidates = [{'url': 'https://example.test/page', 'method': 'GET'}]

        metrics = mode_metrics('basecrawler', candidates, [], {
            'seconds': 1.0, 'status': 'completed', 'crawl_completion': completion,
        })

        self.assertEqual(metrics['observed_attempted_method_urls'], 3)
        self.assertEqual(metrics['observed_successful_method_urls'], 2)

    def test_source_execution_metrics_separate_discovery_attempt_success_and_unvisited(self):
        candidates = [
            {'url': 'https://example.test/ref', 'method': 'GET', 'sources': ['a', 'llms.txt'],
             'source_records': [{'source': 'a'}, {'source': 'llms.txt'}]},
            {'url': 'https://example.test/clue', 'method': 'GET', 'sources': ['js-ast:goja'],
             'source_records': [{'source': 'js-ast:goja', 'confidence': 'clue'}]},
            {'url': 'https://example.test/pending', 'method': 'GET', 'sources': ['llms.txt'],
             'source_records': [{'source': 'llms.txt'}]},
        ]
        completion = {
            'requests': [
                {'requested_url': 'https://example.test/ref', 'final_url': 'https://example.test/ref',
                 'method': 'GET', 'source': 'a', 'http_status': 200, 'state': 'complete'},
                {'requested_url': 'https://example.test/error', 'method': 'GET', 'source': 'script',
                 'http_status': 500, 'state': 'failed', 'error': 'status 500'},
                {'requested_url': 'https://example.test/redirect', 'final_url': 'https://example.test/final',
                 'method': 'GET', 'source': 'link', 'http_status': 200, 'state': 'complete'},
            ],
            'unvisited': [
                {'url': 'https://example.test/pending', 'method': 'GET', 'source': 'llms.txt', 'reason': 'timeout'},
            ],
        }

        metrics = source_execution_metrics(candidates, completion, {'https://example.test/ref'})

        self.assertEqual(metrics['a']['reference_targets_discovered'], 1)
        self.assertEqual(metrics['a']['scheduled_method_urls'], 1)
        self.assertEqual(metrics['a']['scheduled_reference_targets'], 1)
        self.assertEqual(metrics['a']['reference_targets_attempted'], 1)
        self.assertEqual(metrics['a']['reference_targets_successful'], 1)
        self.assertEqual(metrics['llms.txt']['reference_targets_discovered'], 1)
        self.assertEqual(metrics['llms.txt']['scheduled_method_urls'], 1)
        self.assertEqual(metrics['llms.txt']['dispatches'], 0)
        self.assertEqual(metrics['llms.txt']['unvisited_by_reason'], {'timeout': 1})
        self.assertEqual(metrics['js-ast:goja']['crawlable_method_urls'], 0)
        self.assertEqual(metrics['script']['dispatches'], 1)
        self.assertEqual(metrics['script']['successful_http_responses'], 0)
        self.assertEqual(metrics['link']['successful_http_responses'], 1)
        self.assertEqual(metrics['link']['exact_successful_method_urls'], 0)


if __name__=='__main__': unittest.main()
