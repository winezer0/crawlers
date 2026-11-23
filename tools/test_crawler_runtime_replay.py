import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import crawler_runtime_replay as replay


class RuntimeReplayTests(unittest.TestCase):
    def test_sources_require_evidence_and_proven_deployment_prefix(self):
        html = '''<script src="/deploy/static/a.js"></script>
        <code>/wrong/example.js</code>
        <script type="application/json">{"TURBOPACK":true,"runtimeModuleIds":[99]}</script>
        <script>(globalThis.TURBOPACK||(globalThis.TURBOPACK=[])).push({"otherChunks":["static/a.js",{"path":"static/b.js","moduleChunks":["static/not-a-request.js"]}],"runtimeModuleIds":[1]});</script>
        <script>self.__next_f.push([1,"actual flight bytes"]);</script>'''
        body, evidence, scripts = replay.frontend_evidence(html, "https://example.test/docs")
        self.assertEqual(scripts, 2)
        self.assertIn(b"actual flight bytes", body)
        self.assertEqual(set(evidence), {"https://example.test/deploy/static/a.js", "https://example.test/deploy/static/b.js"})

    def test_ambiguous_prefix_is_not_guessed(self):
        html = '''<script src="/one/static/a.js"></script><script src="/two/static/a.js"></script>
        <script>TURBOPACK.push({"otherChunks":["static/a.js","static/b.js"],"runtimeModuleIds":[1]});</script>'''
        _, evidence, _ = replay.frontend_evidence(html, "https://example.test/")
        self.assertEqual(len(evidence), 2)

    def test_origin_and_credentials_are_checked(self):
        for url in ("https://other.test/file.js", "https://user:secret@example.test/file.js", "file:///x"):
            with self.assertRaises(ValueError):
                replay.scoped_url(url, "https://example.test/", "https://example.test/")
        self.assertEqual(replay.scoped_url("./x.js#fragment", "https://example.test/assets/", "https://example.test/"), "https://example.test/assets/x.js")

    def test_offline_and_failed_dependencies_do_not_fetch(self):
        with tempfile.TemporaryDirectory() as temp:
            args = argparse.Namespace(dataset=Path(temp), document_url="https://example.test/", download=False, download_count=0, max_downloads=10)
            state = {"modules": {}}
            evidence = {"https://example.test/module.js": [{"kind": "html-script-src"}]}
            with patch.object(replay, "build_opener") as opener:
                self.assertEqual(replay.collect(args, state, evidence), 0)
                opener.return_value.open.assert_not_called()
                args.download = True
                state["modules"][next(iter(evidence))]["failure"] = {"message": "saved 404"}
                self.assertEqual(replay.collect(args, state, evidence), 0)
                opener.return_value.open.assert_not_called()

    def test_cache_integrity_is_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "module.js").write_bytes(b"changed")
            args = argparse.Namespace(dataset=root, document_url="https://example.test/", download=False)
            url = "https://example.test/module.js"
            state = {"modules": {url: {"file": "module.js", "sha256": replay.digest(b"original"), "evidence": []}}}
            with self.assertRaisesRegex(ValueError, "hash changed"):
                replay.collect(args, state, {url: []})

    def test_all_manifest_sources_are_verified_and_confined(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "module.js").write_bytes(b"original")
            state = {"modules": {"https://example.test/a.js": {"file": "module.js", "sha256": replay.digest(b"original")}}}
            self.assertEqual(len(replay.verified_modules(root, state)), 1)
            (root / "module.js").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash changed"):
                replay.verified_modules(root, state)
            state["modules"]["https://example.test/a.js"]["file"] = "../outside.js"
            with self.assertRaisesRegex(ValueError, "escapes dataset"):
                replay.verified_modules(root, state)


if __name__ == "__main__":
    unittest.main()
