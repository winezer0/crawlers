import json
import os
import subprocess
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import Request, urlopen

from crawler_runtime_inputs import response_inputs, sha
from crawler_runtime_replay import ROOT, scoped_url


class RuntimeInputTests(unittest.TestCase):
    def make_manifest(self, root):
        (root / "body").write_bytes(b'{"parts":["from","response"]}')
        item = {"method": "GET", "url": "https://example.test/input", "request_headers": {"X-Proof": "actual"},
                "status": 200, "headers": {"content-type": "application/json"}, "file": "body",
                "sha256": sha((root / "body").read_bytes())}
        data = {"document_url": "https://example.test/page", "frontend_sha256": "snapshot", "responses": [item]}
        path = root / "manifest.json"
        path.write_text(json.dumps(data))
        return path, data

    def test_response_inputs_reject_wrong_snapshot_hash_scope_and_identity(self):
        for scenario in ("valid", "snapshot", "hash", "outside", "truncated", "post", "duplicate", "headers", "limit"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path, data = self.make_manifest(root)
                item = data["responses"][0]
                if scenario == "snapshot": data["frontend_sha256"] = "other"
                if scenario == "hash": item["sha256"] = "wrong"
                if scenario == "outside": item["file"] = "../body"
                if scenario == "truncated": item["truncated"] = True
                if scenario == "post": item["method"] = "POST"
                if scenario == "duplicate": data["responses"].append(dict(item, request_headers={"x-proof": "actual"}))
                if scenario == "headers": item["request_headers"]["x-proof"] = "other"
                path.write_text(json.dumps(data))
                def load():
                    return response_inputs(path, data["document_url"], "snapshot", root, scoped_url, 1 if scenario == "limit" else 4096, 4096)
                if scenario != "valid":
                    with self.assertRaises(ValueError): load()
                else:
                    records, proof = load()
                    self.assertEqual(Path(records[0]["file"]).read_bytes(), (root / "body").read_bytes())
                    self.assertEqual(proof["responses"], 1)
                    self.assertEqual(records[0]["request_headers"], {"X-Proof": "actual"})

    def test_actual_fixture_click_and_response_chain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, data = self.make_manifest(root)
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path != "/input" or self.headers.get("x-proof") != "actual":
                        self.send_error(400)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"parts":["from","response"]}')

                def log_message(self, *args):
                    pass

            server = HTTPServer(("127.0.0.1", 0), Handler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:" + str(server.server_port)
            try:
                with urlopen(Request(base + "/input", headers={"x-proof": "actual"}), timeout=5) as response:
                    body = response.read()
                    data["responses"][0].update(url=base + "/input", status=response.status, headers=dict(response.headers), sha256=sha(body))
                    (root / "body").write_bytes(body)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
            data["document_url"] = base + "/page"
            manifest.write_text(json.dumps(data))
            records, _ = response_inputs(manifest, data["document_url"], "snapshot", root, scoped_url, 4096, 4096)
            saved = root / "responses.json"
            saved.write_text(json.dumps(records))
            html = root / "page.html"
            html.write_text('<button id="run">Run</button>')
            source = root / "entry.js"
            source.write_text('export const routes=[];document.getElementById("run").onclick=()=>fetch("/input",{headers:{"x-proof":"actual"}}).then(r=>r.json()).then(data=>fetch("/"+data.parts.join("-")));')
            executable = root / "runtime.exe"
            subprocess.run(["go", "test", "-c", "-o", str(executable), "./internal/routeexec"], cwd=ROOT / "basecrawler", check=True, capture_output=True, timeout=120)
            for scenario, expected in (("disabled", 0), ("missing", 1), ("wrong-header", 1), ("actual", 2)):
                with self.subTest(scenario=scenario):
                    env = {k: v for k, v in os.environ.items() if not k.startswith("ROUTE_EXEC_")}
                    report = root / (scenario + ".json")
                    if scenario == "wrong-header":
                        saved.write_text(json.dumps([dict(records[0], request_headers={"x-proof": "wrong"})]))
                    else:
                        saved.write_text(json.dumps(records))
                    env.update(ROUTE_EXEC_FIXTURE=str(source), ROUTE_EXEC_DOCUMENT_HTML=str(html), ROUTE_EXEC_DOCUMENT_URL=data["document_url"], ROUTE_EXEC_REPORT=str(report), ROUTE_EXEC_EXPLORE_INTERACTIONS="0" if scenario == "disabled" else "1", ROUTE_EXEC_RESPONSE_MANIFEST=str(saved) if scenario != "missing" else "")
                    subprocess.run([str(executable), "-test.run", "^TestDownloadedRuntimeFixture$"], env=env, capture_output=True, timeout=30)
                    result = json.loads(report.read_text())
                    self.assertEqual(result["captured_http_calls"], expected)
                    self.assertEqual(result["network_requests"], 0)
                    if scenario == "actual":
                        self.assertEqual(result["requests"][-1]["Raw"], base + "/from-response")
                    if scenario in ("missing", "wrong-header"):
                        self.assertEqual(len(result["response_dependencies"]), 1)

            # Exercise the Python CLI as well as the Go fixture: the server has
            # already stopped, so all continuation must use the saved response.
            dataset = root / "dataset"
            dataset.mkdir()
            entry = b'TURBOPACK.push(["entry.js",1,c=>{document.getElementById("run").onclick=()=>fetch("/input",{headers:{"x-proof":"actual"}}).then(r=>r.json()).then(data=>fetch("/"+data.parts.join("-")));}]);'
            (dataset / "entry.js").write_bytes(entry)
            page = b'<button id="run">Run</button><script src="entry.js"></script><script>globalThis.TURBOPACK=[];TURBOPACK.push({otherChunks:["./entry.js"],runtimeModuleIds:[1]});</script>'
            html.write_bytes(page)
            data["frontend_sha256"] = sha(page)
            manifest.write_text(json.dumps(data))
            (dataset / "dataset.json").write_text(json.dumps({"modules": {base + "/entry.js": {"file": "entry.js", "sha256": sha(entry), "bytes": len(entry), "evidence": []}}}))
            run = subprocess.run([sys.executable, str(ROOT / "tools/crawler_runtime_replay.py"), "--html", str(html), "--document-url", data["document_url"], "--dataset", str(dataset), "--with-dom", "--response-manifest", str(manifest)], capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            result = json.loads((dataset / "summary.json").read_text())
            self.assertEqual(result["captured_http_calls"], 2)
            self.assertEqual(result["network_requests"], 0)
            self.assertTrue(result["explore_interactions"])
            self.assertEqual(result["response_inputs"]["responses"], 1)


if __name__ == "__main__":
    unittest.main()
