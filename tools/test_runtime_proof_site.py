import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from runtime_proof_site import Site, token
from run_runtime_proof_matrix import browser_collection


class RuntimeProofTests(unittest.TestCase):
    def test_browser_proof_requires_headers_both_bodies_and_page(self):
        origin = "http://127.0.0.1:18891"
        label = "POST " + origin + "/actions/proof/click"
        row = {"url": origin + "/actions/proof/click", "method": "POST", "status": 200,
               "task_id": origin + "/alpha", "request_headers": {"X-Runtime-Token": [token("alpha")]},
               "request_body": json.dumps({"variant": "alpha", "proof": token("alpha")}), "body": "accepted"}
        self.assertTrue(browser_collection([row], {label: "click"}, "alpha", origin)["verified"])
        row["body"] = ""
        self.assertFalse(browser_collection([row], {label: "click"}, "alpha", origin)["verified"])

    def test_only_matching_runtime_context_succeeds(self):
        server = Site(0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(origin + "/state?variant=alpha") as response:
                state = json.load(response)
            target = origin + "/actions/" + state["token"] + "/click"
            body = json.dumps({"action": "click", "variant": "alpha", "proof": state["token"]}).encode()
            with self.assertRaises(HTTPError) as failure:
                urlopen(Request(target, data=body, headers={"Content-Type": "application/json"}))
            self.assertEqual(failure.exception.code, 422)
            failure.exception.close()
            with urlopen(Request(target, data=body, headers={"Content-Type": "application/json", "X-Runtime-Token": state["token"]})) as response:
                self.assertEqual(json.load(response), {"accepted": True})
            self.assertNotEqual(token("alpha"), token("beta"))
            with urlopen(origin + "/alpha") as response:
                self.assertNotIn(state["token"], response.read().decode())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
