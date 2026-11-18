import unittest

from crawler_gap_analysis import explain


class GapEvidenceTests(unittest.TestCase):
    def test_unfetched_parent_beyond_depth_is_not_called_parser_failure(self):
        rows = explain([{'source': 'https://x/parent', 'tag': 'link'}], {},
                       {'https://x/parent': [{'depth': 3}]}, 2, {'url_limit'})
        self.assertEqual(rows[0]['finding'], 'parent_discovered_beyond_depth')

    def test_failed_parent_requires_request_diagnosis(self):
        rows = explain([{'source': 'https://x/parent'}],
                       {'https://x/parent': [{'depth': 1, 'status': 503}]}, {}, 2, set())
        self.assertEqual(rows[0]['finding'], 'parent_request_failed')

    def test_runtime_referer_does_not_prove_parser_failure(self):
        rows = explain([{'headers': {'Referer': 'https://x/page'}}],
                       {'https://x/page': [{'depth': 0, 'status': 200}]}, {}, 2, set())
        self.assertEqual(rows[0]['finding'], 'parent_fetched_requires_parser_or_runtime_reproduction')

    def test_no_source_stays_unknown(self):
        self.assertEqual(explain([{}], {}, {}, 2, set())[0]['finding'],
                         'reference_has_no_saved_source_requires_reproduction')


if __name__ == '__main__':
    unittest.main()
