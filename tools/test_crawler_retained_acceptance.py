import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crawler_retained_acceptance import identity, route, score, frozen_local, contract, audit_sources
from run_local_site_regression import read_json_lines
from run_local_crawler_matrix import run_katana_baseline


class RetainedAcceptanceTests(unittest.TestCase):
    def test_legacy_matrix_missing_cache_never_rescans_or_mutates_reference(self):
        with tempfile.TemporaryDirectory() as directory, patch("run_local_crawler_matrix.KATANA_BASELINES", Path(directory)), patch("run_local_crawler_matrix.fixture_fingerprint", return_value="hash"), patch("run_local_crawler_matrix.run_logged") as run:
            metadata, rows = run_katana_baseline("local-react", "standard", "http://127.0.0.1:18732", "binary-hash", Path(directory))
            run.assert_not_called()
            self.assertEqual(metadata["exit_code"], -1)
            self.assertEqual(rows, [])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_source_audit_keeps_runtime_variants_unconfirmed_and_checks_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen, dataset, output = root / "reference", root / "dataset", root / "output"
            (frozen / "katana-browser").mkdir(parents=True)
            dataset.mkdir()
            output.mkdir()
            reference = "https://nextjs.org/docs/guide?_rsc=frozen"
            (frozen / "effective.json").write_text(json.dumps({"missing": [reference]}))
            (frozen / "katana-browser/results.jsonl").write_text(json.dumps({"request": {"endpoint": reference}, "response": {"status_code": 200}}) + "\n")
            (dataset / "frontend.html").write_text('<a href="/docs/guide">Guide</a>')
            body = b'fetch("/docs/guide"+"?_rsc="+value)'
            (dataset / "app.js").write_bytes(body)
            manifest = {"modules": {"https://nextjs.org/app.js": {"file": "app.js", "sha256": hashlib.sha256(body).hexdigest()}}}
            (dataset / "dataset.json").write_text(json.dumps(manifest))
            replay = root / "replay.json"
            replay.write_text(json.dumps({"captured_requests": []}))
            with patch("crawler_retained_acceptance.FROZEN", frozen), patch("crawler_retained_acceptance.DATASET", dataset), patch("crawler_retained_acceptance.REPLAY", replay):
                result = audit_sources(output, [{"url": "https://nextjs.org/docs/guide?_rsc=current", "status": 200}])
                self.assertEqual((result["page_links"], result["exact_literals"]), (1, 0))
                self.assertFalse(result["rows"][0]["exact_frozen_response_coverage"])
                self.assertEqual(len(result["rows"][0]["browser_related_requests"]), 1)
                (dataset / "app.js").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    audit_sources(output)

    def test_jsonl_preserves_unicode_separators_in_response_bodies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traffic.jsonl"
            row = {"url": "https://a.test/app.js", "body": "a\u2028b\u2029c", "status": 200}
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertEqual(read_json_lines(path), [row])

    def test_contract_keeps_authored_interaction_parameters(self):
        targets = contract("local-react", "http://127.0.0.1:18732")
        self.assertIn("GET http://127.0.0.1:18732/api/react-click?source=button", targets)
        self.assertIn("GET http://127.0.0.1:18732/api/react-page?page=2", targets)
        self.assertNotIn("GET http://127.0.0.1:18732/api/react-click", targets)

    def test_identity_preserves_query_path_and_method(self):
        urls = ["https://a.test/x?a=1", "https://a.test/x?a=2", "https://a.test/x/",
                "https://a.test/X", "https://a.test/x%2Fy", "https://b.test/x?a=1"]
        self.assertEqual(len({identity(url) for url in urls}), len(urls))
        self.assertNotEqual(identity(urls[0]), identity(urls[0], "POST"))
        self.assertEqual(identity(urls[0] + "#fragment"), identity(urls[0]))

    def test_rsc_route_relation_is_not_exact_coverage(self):
        expected = {identity("https://a.test/x?_rsc=old"): "runtime"}
        actual = [{"url": "https://a.test/x?_rsc=new", "status": 200}]
        self.assertEqual(route(actual[0]["url"]), route("https://a.test/x?_rsc=old"))
        self.assertEqual(score(expected, actual, 1)["successful"], 0)

    def test_discovery_error_and_response_are_distinct(self):
        urls = ["https://a.test/api/a", "https://a.test/api/b", "https://a.test/api/c"]
        rows = [{"url": urls[0]}, {"url": urls[1], "error": "timeout"},
                {"request": {"endpoint": urls[2]}, "response": {"status_code": 204}}]
        result = score({identity(u): "runtime" for u in urls}, rows, 2)
        self.assertEqual((result["found"], result["attempted"], result["successful"]), (3, 2, 1))

    def test_extra_asset_is_not_false_positive_and_api_remains_unclassified(self):
        result = score({}, [{"url": "https://a.test/main.js", "status": 200},
                            {"url": "https://a.test/api/extra", "status": 200}], 1)
        self.assertEqual(result["http_failure_urls"], [])
        self.assertEqual(result["unclassified_api_requests"], ["GET https://a.test/api/extra"])

    def test_missing_reference_never_scans(self):
        with tempfile.TemporaryDirectory() as directory, patch("crawler_retained_acceptance.KATANA_BASELINES", Path(directory)):
            metadata, rows = frozen_local("site", "standard", "hash", {})
            self.assertFalse(metadata["available"])
            self.assertFalse(metadata["reference_rescanned"])
            self.assertEqual(rows, [])

    def test_frozen_selection_prefers_same_fixture_then_largest_valid_coverage(self):
        with tempfile.TemporaryDirectory() as directory, patch("crawler_retained_acceptance.KATANA_BASELINES", Path(directory)):
            for name, fingerprint, urls in [("old", "old", ["a", "b"]), ("current", "current", ["a"]),
                                            ("best", "current", ["a", "b"])]:
                path = Path(directory) / name / "site" / "standard"
                path.mkdir(parents=True)
                (path / "metadata.json").write_text(json.dumps({"fixture_fingerprint": fingerprint, "exit_code": 0}))
                (path / "output.jsonl").write_text("\n".join(json.dumps({"url": "https://a.test/" + u, "status": 200}) for u in urls))
            metadata, rows = frozen_local("site", "standard", "current", {identity("https://a.test/" + u): "runtime" for u in ["a", "b"]})
            self.assertTrue(metadata["fixture_matches"])
            self.assertIn("best", metadata["path"])
            self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
