import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import crawler_runtime_verify as verify


class RuntimeVerificationTests(unittest.TestCase):
    def test_only_actual_same_origin_get_calls_are_eligible(self):
        def call(url, **kwargs):
            return {"Raw": url, "Method": "GET", "Evidence": "executed-request-call", **kwargs}
        summary = {"captured_requests": [call("https://example.test/a", Kind="navigation"),
                   call("https://example.test/post", Method="POST"), call("https://example.test/actual")]}
        with patch.object(verify, "build_opener") as opener:
            rows = verify.verify(summary, "https://example.test/")
            opener.return_value.open.assert_not_called()
        self.assertEqual([r["url"] for r in rows], ["https://example.test/actual"])
        self.assertFalse(rows[0]["sent"])
        with self.assertRaises(ValueError):
            list(verify.captured_gets({"captured_requests": [call("https://outside.test/a")]}, "https://example.test/"))

    def test_actual_request_context_and_no_repeat_or_redirect(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("RSC")))
                self.send_response(302 if self.path == "/redirect" else 200)
                self.send_header("Content-Type", "text/x-component")
                self.send_header("Location", "/must-not-follow")
                self.end_headers()
                self.wfile.write(b'0:{"verified":true}\n')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            summary = {"captured_requests": [{"Raw": base + path, "Evidence": "executed-request-call", "Headers": {"RSC": "1"}} for path in ("/actual?_rsc=observed", "/redirect")]}
            rows = verify.verify(summary, base, send=True)
            again = verify.verify(summary, base, rows, send=True)
            self.assertEqual(requests, [("/actual?_rsc=observed", "1"), ("/redirect", "1")])
            self.assertTrue(rows[0]["valid_rsc_response"])
            self.assertFalse(rows[1]["valid_rsc_response"])
            self.assertFalse(rows[0]["coverage_accepted"])
            self.assertEqual(len(again), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
