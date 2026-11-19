import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import katana_effective_compare as compare


class ClassificationTests(unittest.TestCase):
    def row(self, **values):
        return dict(status=200, url='https://x/api/a', final_url='https://x/api/a', sha256='a', content_type='application/json', title='', **values)

    def test_html_fallback_is_not_an_api_success(self):
        row = self.row()
        row['content_type'] = 'text/html'
        self.assertEqual(compare.classify(row, {'sha256': 'a'}), 'html_fallback')

    def test_authentication_does_not_prove_nonexistence(self):
        row = self.row()
        row['status'] = 403
        self.assertEqual(compare.classify(row, {}), 'restricted_or_blocked')
        row['status'], row['final_url'] = 200, 'https://x/login'
        self.assertEqual(compare.classify(row, {}), 'authentication_redirect')

    def test_json_and_errors(self):
        self.assertEqual(compare.classify(self.row(), {}), 'valid_json')
        row = self.row()
        row['status'] = 404
        self.assertEqual(compare.classify(row, {}), 'not_found')
        row['status'] = 0
        self.assertEqual(compare.classify(row, {}), 'network_error')

    def test_failed_control_cannot_certify_html(self):
        row = self.row()
        row['content_type'] = 'text/html'
        self.assertEqual(compare.classify(row, {'status': 0, 'error': 'timeout'}), 'unconfirmed_control')
        self.assertEqual(compare.classify(row, {'status': 404, 'sha256': 'different'}), 'valid_page')


class CandidateEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.target = SimpleNamespace(seed="https://example.test/", allowed_hosts=("example.test",))

    def test_candidates_keep_all_sources_and_non_fetchable_confidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            results = [
                {"url": "https://example.test/api/items", "method": "GET", "source": "js-ast:goja",
                 "confidence": "clue", "evidence": "literal", "referer": "https://example.test/app.js"},
                {"url": "https://example.test/api/ghost", "method": "GET", "source": "js-ast:treesitter",
                 "confidence": "clue", "evidence": "literal", "referer": "https://example.test/app.js"},
            ]
            discoveries = [
                {"url": "https://example.test/api/items", "method": "GET", "source": "js-ast:goja",
                 "confidence": "clue", "evidence": "literal", "referer": "https://example.test/a"},
                {"url": "https://example.test/api/items", "method": "GET", "source": "a",
                 "confidence": "", "evidence": "", "referer": "https://example.test/"},
                {"url": "https://example.test/api/ghost", "method": "GET", "source": "js-ast:treesitter",
                 "confidence": "clue", "evidence": "literal", "referer": "https://example.test/app.js"},
            ]
            (folder / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in results), encoding="utf-8")
            (folder / "discoveries.jsonl").write_text("".join(json.dumps(row) + "\n" for row in discoveries), encoding="utf-8")

            rows = compare.candidates(folder, "basecrawler", self.target)
            by_url = {row["url"]: row for row in rows}
            items = by_url["https://example.test/api/items"]
            self.assertEqual(items["source"], "js-ast:goja")
            self.assertEqual(set(items["sources"]), {"js-ast:goja", "a"})
            self.assertEqual(len(items["source_records"]), 3)
            self.assertEqual({record["record_file"] for record in items["source_records"]},
                             {"results.jsonl", "discoveries.jsonl"})
            self.assertTrue(compare.candidate_is_crawlable(items))
            self.assertFalse(compare.eligible(by_url["https://example.test/api/ghost"], "basecrawler"))

    def test_base_only_source_sample_is_stable_bounded_and_excludes_reference(self):
        rows = []
        for index in range(8):
            rows.append({
                "url": f"https://example.test/api/{index}", "method": "GET", "source": "js-ast:goja",
                "source_records": [{"source": "js-ast:goja", "confidence": "certain"}],
            })
        reference = {"https://example.test/api/0"}

        first = compare.base_only_source_samples(rows, reference)
        second = compare.base_only_source_samples(rows, reference)
        self.assertEqual(first, second)
        self.assertEqual(len(first["js-ast:goja"]), compare.BASE_ONLY_SAMPLE_PER_SOURCE)
        self.assertNotIn("https://example.test/api/0", first["js-ast:goja"])

    def test_clue_only_candidate_is_not_sampled(self):
        rows = [{
            "url": "https://example.test/api/guess", "method": "GET", "source": "js-ast:goja",
            "confidence": "clue", "source_records": [{"source": "js-ast:goja", "confidence": "clue"}],
        }]
        self.assertEqual(compare.base_only_source_samples(rows, set()), {})


if __name__ == "__main__":
    unittest.main()
