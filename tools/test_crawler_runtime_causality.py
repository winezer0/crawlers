import json
import unittest

from crawler_runtime_causality import compare


class RuntimeCausalityTests(unittest.TestCase):
    def test_only_direct_read_target_gets_layout_failure(self):
        after = {"runtime_diagnostics": [{"kind": "layout_dependency", "message": json.dumps({
            "reason": "flex", "category": "layout_algorithm", "geometry_read": {"target": {"href": "/a"}}
        })}, {"kind": "layout_dependency", "message": json.dumps({
            "reason": "text", "category": "font_or_text_input", "geometry_read": {"target": {"tag": "span"}}
        })}]}
        result = compare({"missing": ["https://example.test/a?_rsc=x", "https://example.test/b?_rsc=x"]},
                         [], {}, after, '<a href="/a">A</a><a href="/b">B</a>', "https://example.test/docs")
        a, b = result["rows"]
        self.assertEqual(a["diagnosis"], "direct_geometry_dependency_observed")
        self.assertEqual(b["diagnosis"], "no_target_observer_or_call_evidence")
        self.assertEqual(len(result["after_unassociated_layout_failures"]), 1)
        self.assertTrue(b["link_targets"])
        self.assertEqual(a["causal_claim"], "not_proven")

    def test_variant_headers_do_not_close_reference_or_prove_trigger(self):
        url = "https://example.test/a?_rsc=old"
        records = [{"request": {"method": "GET", "endpoint": url, "headers": {
            "Rsc": "1", "Next-Router-Prefetch": "1", "X-Deployment-Id": "old", "Cookie": "secret"}},
                    "response": {"status_code": 200}}]
        call = {"Raw": "https://example.test/a?_rsc=new", "Method": "GET", "Evidence": "executed-request-call",
                "Headers": {"rsc": "1", "next-router-prefetch": "1", "x-deployment-id": "new"}}
        result = compare({"missing": [url]}, records, {}, {"captured_requests": [call]}, "", "https://example.test/docs")
        row = result["rows"][0]
        self.assertEqual(result["new_related_call_targets"], 1)
        self.assertFalse(row["after_exact_call"])
        self.assertFalse(row["coverage_accepted"])
        self.assertFalse(row["response_confirmed"])
        self.assertEqual(row["reference_records"][0]["trigger_evidence"], "prefetch_header")
        self.assertNotIn("cookie", row["reference_records"][0]["headers"])
        self.assertEqual(row["after_call_variants"][0]["reference_context_differences"][0]["x-deployment-id"],
                         {"reference": "old", "replay": "new"})
        self.assertTrue(result["snapshot_deployment_mismatch"])
        self.assertFalse(row["reference_records"][0]["response_body_recorded"])

    def test_navigation_post_and_different_query_are_not_get_calls(self):
        url = "https://example.test/a?q=1&_rsc=x"
        base = {"Raw": url, "Method": "GET", "Evidence": "executed-request-call"}
        after = {"captured_requests": [dict(base, Kind="navigation"), dict(base, Method="POST"),
                                      dict(base, Raw="https://example.test/a?q=2&_rsc=x")]}
        result = compare({"missing": [url]}, [], {}, after, "", "https://example.test/docs")
        self.assertEqual(result["rows"][0]["after_related_call_count"], 0)

    def test_registered_observer_does_not_prove_callback_or_request(self):
        event = {"action": "observe", "sequence": 1, "target": {"href": "/a"}, "rootMargin": "0px", "thresholds": [0]}
        after = {"runtime_diagnostics": [{"kind": "geometry_observation", "message": json.dumps(event)}]}
        result = compare({"missing": ["https://example.test/a"]}, [], {}, after, "", "https://example.test/docs")
        self.assertEqual(result["rows"][0]["diagnosis"], "observer_registered_request_not_observed")
        self.assertFalse(result["rows"][0]["after_exact_call"])


if __name__ == "__main__":
    unittest.main()
