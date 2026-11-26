import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import katana_enhanced_baseline as baseline


class EnhancedBaselineTests(unittest.TestCase):
    def test_discovery_profiles_preserve_queries_and_responses(self):
        for mode in baseline.MODES:
            with self.subTest(mode=mode):
                flags = baseline.discovery_flags(mode)
                for required in ("-jc", "-jsl", "-kf", "-aff", "-fx", "-pc", "-kb-endpoints", "-sr", "-ndef", "-duf"):
                    self.assertIn(required, flags)
                for forbidden in ("-iqp", "-fsu", "-dr", "-or", "-ob", "-kb-validate-secrets"):
                    self.assertNotIn(forbidden, flags)
                self.assertEqual("-hl" in flags, mode == "headless")
                self.assertEqual("-hh" in flags, mode == "hybrid")

    def test_difference_keeps_query_and_method(self):
        rows = [{"request": {"method": "GET", "endpoint": "http://localhost/api/page?page=2"},
                 "response": {"status_code": 200}},
                {"request": {"method": "POST", "endpoint": "http://localhost/api/page?page=2"},
                 "response": {"status_code": 200}}]
        actual = [{"url": "http://localhost/api/page?page=1", "status": 200}]
        missing = baseline.differences(rows, actual)
        self.assertEqual([item["identity"] for item in missing], [
            "GET http://localhost/api/page?page=2", "POST http://localhost/api/page?page=2"])

    def test_frozen_cache_does_not_repeat_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = root / "tool.exe"
            tool.write_bytes(b"tool")
            calls = []

            def run(command, *args, **kwargs):
                calls.append(command)
                output = Path(command[command.index("-o") + 1])
                output.write_text(json.dumps({"request": {"endpoint": "http://localhost/"},
                                              "response": {"status_code": 200}}) + "\n")
                (output.parent / "stdout.txt").write_text("completed")
                return 0, 1.0, None

            with patch.object(baseline, "CACHE", root / "cache"), patch.object(baseline, "KATANA", tool), \
                    patch.object(baseline, "fixture_fingerprint", return_value="source"), \
                    patch.object(baseline, "run_reference", side_effect=run):
                first, _ = baseline.freeze("local-next", "standard", "http://localhost", "build-a", tool)
                second, _ = baseline.freeze("local-next", "standard", "http://localhost", "build-a", tool)
                self.assertFalse(first["cache_reused"])
                self.assertTrue(second["cache_reused"])
                self.assertEqual(len(calls), 1)
                baseline.freeze("local-next", "standard", "http://localhost", "build-b", tool)
                self.assertEqual(len(calls), 2)
                Path(first["output"]).write_text("corrupted")
                with self.assertRaisesRegex(RuntimeError, "modified"):
                    baseline.freeze("local-next", "standard", "http://localhost", "build-a", tool)
                self.assertEqual(len(calls), 2)

    def test_recursive_path_detection_is_separate_from_scanner_filtering(self):
        self.assertTrue(baseline.cyclic_path("http://localhost/" + "static/" * 16))
        self.assertTrue(baseline.cyclic_path("http://localhost/" + "a/b/" * 16))
        self.assertFalse(baseline.cyclic_path("http://localhost/static/chunks/main.js"))

    def test_dispatch_ledger_prevents_false_missing_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            report.write_text(json.dumps({"completion": {"requests": [{
                "requested_url": "http://localhost/style.css", "http_status": 200, "method": "GET"}]}}))
            actual = baseline.with_scan_requests([], report)
            reference = [{"request": {"endpoint": "http://localhost/style.css"}, "response": {"status_code": 200}}]
            self.assertEqual(baseline.differences(reference, actual), [])


if __name__ == "__main__":
    unittest.main()
