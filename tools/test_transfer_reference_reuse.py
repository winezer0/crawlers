import json
from pathlib import Path
import tempfile
import unittest

from run_local_site_regression import sha256_file
from run_runtime_proof_matrix import reuse_katana


class TransferReferenceTests(unittest.TestCase):
    def test_recorded_reference_reused_without_local_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.jsonl"
            output.write_text('{"request":{"endpoint":"http://localhost/"}}\n', encoding="utf-8")
            (output.parent / "oracle.json").write_text("[]", encoding="utf-8")
            previous = {"variants": {"alpha": {"katana-headless": {"run": {
                "output": str(output), "output_sha256": sha256_file(output), "valid": True}}}}}
            meta, rows = reuse_katana(previous, "alpha", "katana-headless")
            self.assertTrue(meta["cache_reused"])
            self.assertEqual(len(rows), 1)
            output.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed"):
                reuse_katana(previous, "alpha", "katana-headless")


if __name__ == "__main__":
    unittest.main()
