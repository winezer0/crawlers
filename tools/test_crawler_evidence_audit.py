import unittest
from crawler_evidence_audit import audit_records, identity

class EvidenceAuditTests(unittest.TestCase):
    def test_static_response_does_not_prove_runtime(self):
        audit = audit_records(
            [{"url": "https://a.test/api", "source": "js-ast:goja", "evidence": "request-call"}],
            [{"requested_url": "https://a.test/api", "source": "js-ast:goja",
              "evidence": "request-call", "http_status": 200, "authored_headers": None}], [])
        target = audit["targets"][0]
        self.assertTrue(target["stages"]["http_success_observed"])
        self.assertFalse(target["stages"]["runtime_request_call_observed"])
        self.assertEqual(target["dispatch_bases"], ["static_request_sink"])

    def test_runtime_discovery_survives_static_dispatch(self):
        rows = [{"url": "https://a.test/api", "source": "js-ast:goja", "evidence": "request-call"},
                {"url": "https://a.test/api", "source": "js-exec:request",
                 "evidence": "executed-request-call", "headers": {"X-Mode": "one"}}]
        attempts = [{"requested_url": "https://a.test/api", "source": "js-ast:goja",
                     "evidence": "request-call", "http_status": 200, "authored_headers": None}]
        target = audit_records(rows, attempts, [])["targets"][0]
        self.assertEqual(len(target["discoveries"]), 2)
        self.assertTrue(target["static_dispatch_with_later_or_independent_runtime_evidence"])
        runtime = target["discoveries"][1]
        self.assertFalse(runtime["same_authored_context_success"])
        self.assertEqual(runtime["caused_dispatch"], "not_proven_without_event_order")

    def test_header_variants_and_legacy_unknown(self):
        edges = [{"url": "https://a.test/api", "source": "js-exec:request",
                  "evidence": "executed-request-call", "headers": {"X-Mode": v}}
                 for v in ["one", "two"]]
        attempts = [{"requested_url": "https://a.test/api", "http_status": 200,
                     "authored_headers": {"x-mode": "one"}}]
        target = audit_records(edges, attempts, [])["targets"][0]
        self.assertEqual([e["same_authored_context_success"] for e in target["discoveries"]], [True, False])
        legacy = audit_records(edges, [{"requested_url": "https://a.test/api", "http_status": 200}], [])["targets"][0]
        self.assertFalse(any(e["same_authored_context_success"] for e in legacy["discoveries"]))

    def test_identity_does_not_merge_origin_method_or_query(self):
        urls = ["https://a.test/api?x=a%2Bb", "https://a.test/api?x=a+b",
                "https://b.test/api?x=a%2Bb"]
        rows = [{"url": url, "method": "GET"} for url in urls]
        rows.append({"url": urls[0], "method": "POST"})
        self.assertEqual(len(audit_records(rows, [], [])["targets"]), 4)
        self.assertNotEqual(identity(urls[0]), identity(urls[1]))

    def test_response_extraction_and_pending_are_not_runtime(self):
        target = audit_records(
            [{"url": "/next", "source": "json"}], [], [],
            [{"url": "/next", "reason": "frontier_rejected_or_duplicate"}])["targets"][0]
        self.assertEqual(target["discoveries"][0]["stage"], "response_extraction")
        self.assertFalse(target["stages"]["attempt_recorded"])
        self.assertEqual(len(target["deferred"]), 1)

    def test_redirect_keeps_requested_identity(self):
        target = audit_records([], [{"requested_url": "/start", "final_url": "/end", "http_status": 200}],
                               [{"url": "/end", "requested_url": "/start", "status": 200}])["targets"][0]
        self.assertEqual(target["identity"], "GET /start")
        self.assertEqual(target["response_records"][0]["url"], "/end")

    def test_http_warning_is_not_assumed_to_be_invalid_url(self):
        audit = audit_records([], [{"requested_url": "/image", "http_status": 400,
                                    "source": "route:route-table"}], [])
        self.assertEqual(audit["counts"]["non_success_http_responses"], 1)
        self.assertEqual(audit["non_success_http_responses"][0]["identity"], "GET /image")
        self.assertNotIn("false_positive", audit["non_success_http_responses"][0])


    def test_conditional_candidate_is_not_a_runtime_invocation(self):
        target = audit_records(
            [{"url": "/api", "source": "js-ast:goja", "evidence": "request-call",
              "conditional": True, "confidence": "clue"}], [], [],
            [{"url": "/api", "reason": "conditional_execution_unresolved"}])["targets"][0]
        self.assertTrue(target["discoveries"][0]["conditional"])
        self.assertFalse(target["stages"]["runtime_request_call_observed"])
        self.assertFalse(target["stages"]["attempt_recorded"])
        self.assertEqual(target["deferred"][0]["reason"], "conditional_execution_unresolved")

if __name__ == "__main__":
    unittest.main()
