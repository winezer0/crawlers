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
    def test_discovery_coverage_requires_exact_method_and_url_not_alias(self):
        urls = {'https://example.test/a', 'https://example.test/b'}
        non_get = {('POST', 'https://example.test/action')}
        base = {('GET', 'https://example.test/a'), ('GET', 'https://example.test/alias'),
                ('POST', 'https://example.test/action')}

        metrics = compare.method_aware_discovery_metrics(urls, non_get, base)

        self.assertEqual(metrics['valid_targets'], 3)
        self.assertEqual(metrics['discovered_targets'], 2)
        self.assertEqual(metrics['coverage'], 2 / 3)

    def test_observed_non_get_reference_is_a_method_aware_discovery_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            for mode in ('katana-static', 'katana-browser'):
                (site / mode).mkdir()
            post_url = 'https://example.test/api/action'
            (site / 'katana-browser' / 'results.jsonl').write_text(
                json.dumps({'request': {'method': 'POST', 'endpoint': post_url},
                            'response': {'status_code': 200}}) + '\n',
                encoding='utf-8')
            target_rows = {
                'katana-static': [],
                'katana-browser': [{'method': 'POST', 'url': post_url}],
            }

            targets = compare.observed_non_get_targets(site, ('katana-static', 'katana-browser'), target_rows)

        self.assertEqual(targets, {('POST', post_url)})

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

    def test_completion_audit_counts_fsu_filtered_requests_but_rejects_aliases(self):
        valid = {
            'https://example.test/a',
            'https://example.test/b',
            'https://example.test/c',
            'https://example.test/d',
        }
        completion = {'requests': [
            {'requested_url': 'https://example.test/a', 'final_url': 'https://example.test/a',
             'method': 'GET', 'http_status': 200, 'state': 'complete'},
            {'requested_url': 'https://example.test/b', 'final_url': 'https://example.test/elsewhere',
             'method': 'GET', 'http_status': 200, 'state': 'complete'},
            {'requested_url': 'https://example.test/c', 'final_url': 'https://example.test/c',
             'method': 'POST', 'http_status': 200, 'state': 'complete'},
            {'requested_url': 'https://example.test/d', 'final_url': 'https://example.test/d',
             'method': 'GET', 'http_status': 500, 'state': 'failed', 'error': 'status 500'},
        ]}

        metrics, dispatched, successful = compare.reference_execution_metrics(valid, [], completion)

        self.assertEqual(dispatched, {
            'https://example.test/a', 'https://example.test/b', 'https://example.test/d'})
        self.assertEqual(successful, {'https://example.test/a'})
        self.assertEqual(metrics['successful_reference_coverage'], 1 / 4)

    def test_legacy_completion_without_methods_falls_back_to_result_rows(self):
        valid = {'https://example.test/a', 'https://example.test/b'}
        legacy_completion = {'requests': [
            {'requested_url': 'https://example.test/a', 'http_status': 200, 'state': 'complete'},
        ]}
        rows = [
            {'url': 'https://example.test/a', 'method': 'POST', 'status': 200},
            {'url': 'https://example.test/b', 'method': 'GET', 'status': 200},
        ]

        metrics, _, successful = compare.reference_execution_metrics(valid, rows, legacy_completion)

        self.assertEqual(successful, {'https://example.test/b'})
        self.assertEqual(metrics['successful_reference_urls'], 1)

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
    def test_base_concurrency_and_rate_limit_are_applied_to_basecrawler(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / 'basecrawler.exe'
            binary.write_bytes(b'fixture')
            args = SimpleNamespace(
                output=root / 'output', base=binary, depth=2, base_concurrency=4, base_rate_limit=9, base_seconds_limit=20, base_max_pages_per_host=300,
                pages=20, rate=2, seconds=10, strategy='breadth-first', proxy='', resume=False,
            )
            target = SimpleNamespace(site_id='fixture', seed='https://example.test/', allowed_hosts=('example.test',))
            with patch.object(compare.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='', stderr='')) as run:
                record = compare.run_one(target, 'basecrawler', args)

            command = json.loads((args.output / 'fixture' / 'basecrawler' / 'command.json').read_text(encoding='utf-8'))['command']
            self.assertEqual(command[command.index('-c') + 1], '4')
            self.assertEqual(command[command.index('--rl') + 1], '9')
            self.assertEqual(command[command.index('--ct') + 1], '20s')
            self.assertEqual(command[command.index('--mdp') + 1], '300')
            self.assertEqual(record['concurrency'], 4)
            self.assertEqual(record['rate_limit'], 9)
            self.assertEqual(record['time_limit_seconds'], 20)
            self.assertEqual(record['max_pages_per_host'], 300)
            self.assertEqual(run.call_args.kwargs['timeout'], 80)


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
