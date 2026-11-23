import json
import tempfile
import unittest
from pathlib import Path

from crawler_candidate_audit import audit, category


class CandidateAuditTests(unittest.TestCase):
    def test_real_link_preserved_alongside_text_clue(self):
        row = {"url": "https://example.test/docs", "source_records": [
            {"source": "next-flight", "confidence": "clue", "evidence": "rendered-text"},
            {"source": "a", "confidence": ""},
        ]}
        self.assertEqual(category(row, None), "page_candidate")

    def test_asset_clue_is_not_actionable_resource(self):
        row = {"url": "https://example.test/example.js", "confidence": "clue", "evidence": "rendered-text"}
        self.assertEqual(category(row, None), "clue_or_template")

    def test_get_evidence_does_not_confirm_post(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "basecrawler"
            run.mkdir()
            (run / "candidates.json").write_text(json.dumps([
                {"url": "https://example.test/api/search", "method": "POST", "confidence": "interface"},
                {"url": "https://example.test/old", "method": "GET"},
            ]), encoding="utf-8")
            (root / "verification.jsonl").write_text(
                json.dumps({"url": "https://example.test/api/search", "status": 200, "content_type": "application/json"})+"\n"+
                json.dumps({"url": "https://example.test/old", "status": 404})+"\n", encoding="utf-8")
            summary, rows = audit(run)
            self.assertEqual(summary["candidate_method_urls"], 2)
            self.assertEqual(rows[0]["verification"], "unverified")
            self.assertEqual(rows[1]["verification"], "http_404_not_parser_proof")


if __name__ == "__main__":
    unittest.main()
