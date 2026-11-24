import unittest

from crawler_browser_diagnostics import summarize, value


class DiagnosticTests(unittest.TestCase):
    def test_evaluation_exception_is_not_empty_state(self):
        self.assertEqual(value({'result': {'exceptionDetails': {'text': 'broken'}}}),
                         {'evaluation_error': {'text': 'broken'}})

    def test_protocol_error_is_preserved(self):
        self.assertEqual(value({'error': {'message': 'unsupported'}}),
                         {'evaluation_error': {'message': 'unsupported'}})

    def test_snapshot_json_is_decoded(self):
        self.assertEqual(value({'result': {'result': {'value': '{"anchors":3}'}}}), {'anchors': 3})

    def test_network_failure_and_exception_remain_distinct(self):
        result = summarize([
            {'method': 'Network.requestWillBeSent', 'elapsed_seconds': 1,
             'params': {'requestId': '1', 'request': {'method': 'GET', 'url': 'https://example.org/?_rsc=abc'}}},
            {'method': 'Network.loadingFailed', 'params': {'requestId': '1', 'errorText': 'timeout'}},
            {'method': 'Runtime.exceptionThrown', 'params': {'exceptionDetails': {'text': 'TypeError'}}},
        ])
        self.assertEqual(len(result['rsc_requests']), 1)
        self.assertEqual(result['network_failures'][0]['errorText'], 'timeout')
        self.assertEqual(result['exceptions'][0]['text'], 'TypeError')
        self.assertEqual(result['responses'], [])

    def test_duplicate_events_do_not_inflate_unique_urls(self):
        event = {'method': 'Network.requestWillBeSent', 'elapsed_seconds': 1,
                 'params': {'requestId': '1', 'request': {'method': 'GET', 'url': 'https://example.org/?_rsc=x'}}}
        result = summarize([event, event])
        self.assertEqual(len(result['rsc_requests']), 2)
        self.assertEqual(result['unique_rsc_urls'], ['https://example.org/?_rsc=x'])


if __name__ == '__main__':
    unittest.main()
