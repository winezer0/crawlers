import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import crawler_runtime_replay as replay


class RuntimeReplayTests(unittest.TestCase):
    def test_frozen_audit_never_closes_rsc_variants_or_navigation(self):
        audit = replay.audit_frozen_missing({"missing": ["https://example.test/a?_rsc=old", "https://example.test/b"]}, [
            {"Raw": "https://example.test/a?_rsc=new", "Method": "GET", "Evidence": "executed-request-call", "Headers": {"rsc": "1"}},
            {"Raw": "https://example.test/b", "Method": "GET", "Kind": "navigation", "Evidence": "executed-navigation-call"}])
        self.assertEqual(audit["targets"], 2)
        self.assertEqual(audit["related_variant_targets"], 1)
        self.assertEqual(audit["exact_url_calls"], 0)
        self.assertEqual(audit["closed_targets"], 0)
        self.assertEqual(audit["rows"][1]["state"], "not_observed")

    def test_anchor_discovery_is_separate_from_rsc_request_evidence(self):
        parsed = replay.Frontend("https://example.test/docs/")
        parsed.feed('<base href="/app/"><a href="start#heading">Start</a><a href="https://other.test/start">Outside</a>')
        audit = replay.audit_frozen_missing({"missing": ["https://example.test/app/start?_rsc=old"]}, [], parsed.links, "layout pending")
        row = audit["rows"][0]
        self.assertTrue(row["link_target_observed"])
        self.assertFalse(row["request_variant_observed"])
        self.assertEqual(row["runtime_blocker"], "layout pending")
        self.assertEqual(audit["closed_targets"], 0)

    def test_stylesheets_require_actual_links_and_same_origin(self):
        parsed = replay.Frontend("https://example.test/docs/")
        parsed.feed('''<base href="/assets/"><link rel="stylesheet" href="theme.css"><link rel="stylesheet" href="off.css" disabled><link rel="preload" href="preload.css"><link rel="stylesheet" href="https://other.test/x.css"><code>guessed.css</code>''')
        self.assertEqual(parsed.stylesheets, ["https://example.test/assets/theme.css"])

    def test_cached_stylesheets_are_separate_and_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            css = b".chosen{animation-name:observed}"
            (root / "theme.css").write_bytes(css)
            args = argparse.Namespace(dataset=root, document_url="https://example.test/", download=False)
            url = "https://example.test/theme.css"
            state = {"modules": {}, "stylesheets": {url: {"file": "theme.css", "sha256": replay.digest(css), "evidence": []}}}
            with patch.object(replay, "build_opener") as opener:
                self.assertEqual(replay.collect(args, state, {url: []}, "css"), 0)
                opener.return_value.open.assert_not_called()
            self.assertEqual(state["modules"], {})

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
