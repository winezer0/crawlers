import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


class AcceptanceAndControlTests(unittest.TestCase):
    def test_execution_coverage_requires_an_actual_successful_get(self):
        valid = {'https://example.test/a', 'https://example.test/b', 'https://example.test/c'}
        rows = [
            {'url': 'https://example.test/a', 'method': 'GET', 'status': 200},
            {'url': 'https://example.test/b', 'method': 'GET', 'error': 'max depth reached'},
            {'url': 'https://example.test/c', 'method': 'GET', 'status': 500},
            {'url': 'https://example.test/a', 'method': 'POST', 'status': 200},
        ]

        metrics, dispatched, successful = compare.reference_execution_metrics(valid, rows)

        self.assertEqual(dispatched, {'https://example.test/a', 'https://example.test/c'})
        self.assertEqual(successful, {'https://example.test/a'})
        self.assertEqual(metrics['dispatched_reference_urls'], 2)
        self.assertEqual(metrics['dispatched_reference_coverage'], 2 / 3)
        self.assertEqual(metrics['successful_reference_urls'], 1)
        self.assertEqual(metrics['successful_reference_coverage'], 1 / 3)

    def test_strict_acceptance_rejects_limited_crawl_even_with_full_coverage(self):
        runs = {
            'basecrawler': {'status': 'completed', 'crawl_completion': {'status': 'limited', 'complete': False}},
            'katana-static': {'status': 'completed'},
            'katana-browser': {'status': 'completed'},
        }
        execution = {'successful_reference_coverage': 1.0}

        failures = compare.strict_acceptance_failures(10, 1.0, [], runs, execution)

        self.assertEqual(failures, ['basecrawler_crawl_incomplete'])

    def test_control_url_override_is_site_scoped_and_same_origin(self):
        target = SimpleNamespace(site_id='testasp', seed='https://example.test/start')
        overrides = compare.parse_control_url_overrides(['testasp=https://example.test/__missing'])
        self.assertEqual(compare.control_url_for(target, overrides), 'https://example.test/__missing')
        self.assertEqual(compare.control_url_for(target, {}), 'https://example.test/__crawler_benchmark_missing_20261118__')
        with self.assertRaises(ValueError):
            compare.control_url_for(target, {'testasp': 'https://other.test/__missing'})
        with self.assertRaises(ValueError):
            compare.parse_control_url_overrides(['testasp=https://example.test/a', 'testasp=https://example.test/b'])


class RunnerProfileTests(unittest.TestCase):
    def test_base_concurrency_is_recorded_and_applied_only_to_basecrawler(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / 'basecrawler.exe'
            binary.write_bytes(b'fixture')
            args = SimpleNamespace(
                output=root / 'output', base=binary, depth=2, base_concurrency=4,
                pages=20, rate=2, seconds=10, strategy='breadth-first', proxy='', resume=False,
            )
            target = SimpleNamespace(site_id='fixture', seed='https://example.test/', allowed_hosts=('example.test',))
            with patch.object(compare.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='', stderr='')):
                record = compare.run_one(target, 'basecrawler', args)

            command = json.loads((args.output / 'fixture' / 'basecrawler' / 'command.json').read_text(encoding='utf-8'))['command']
            self.assertEqual(command[command.index('-c') + 1], '4')
            self.assertEqual(record['concurrency'], 4)


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
