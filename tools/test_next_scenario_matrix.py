import unittest
from run_next_scenario_matrix import compare_extra_targets, evaluate, key

class ScenarioScoringTests(unittest.TestCase):
    def test_query_and_method_must_match(self):
        case = {"targets": ["/api/matrix/result?token=a%2Bb"]}
        rows = [{"url": "http://localhost/api/matrix/result?token=a+b", "status": 200},
                {"url": "http://localhost/api/matrix/result?token=a%2Bb", "method": "POST", "status": 200}]
        result = evaluate(case, rows, [])
        self.assertEqual(result["sent_successfully"], [])
        self.assertFalse(result["passed"])

    def test_discovery_is_not_sending(self):
        result = evaluate({"targets": ["/api/matrix/result"]}, [],
                          [{"url": "http://localhost/api/matrix/result"}])
        self.assertEqual(len(result["found"]), 1)
        self.assertEqual(len(result["missing_success"]), 1)

    def test_negative_request_fails_even_if_target_succeeds(self):
        rows = [{"url": "/api/matrix/result", "status": 200},
                {"url": "/api/matrix/decoy-not-a-request", "status": 404}]
        result = evaluate({"targets": ["/api/matrix/result"]}, rows, [])
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["negative_requested"]), 1)

    def test_duplicates_and_order_independent_queries(self):
        url = "/api/matrix/result?a=1&b=2"
        result = evaluate({"targets": [url]}, [{"url": url, "status": 200}] * 2, [])
        self.assertEqual(result["successful_target_response_counts"][key(url)], 2)
        self.assertEqual(key(url), key("/api/matrix/result?b=2&a=1"))


    def test_context_suite_counts_extra_api_in_its_own_namespace(self):
        result = evaluate({"targets": ["/api/context-regression/alpha"],
                           "api_prefix": "/api/context-regression/"},
                          [{"url": "/api/context-regression/alpha", "status": 200},
                           {"url": "/api/context-regression/beta", "status": 200}], [])
        self.assertFalse(result["passed"])
        self.assertEqual(result["unclassified_api_requests"], ["GET /api/context-regression/beta"])


    def test_prior_comparison_preserves_pending_and_response_causes(self):
        previous = {"results": [{"id": "case", "unclassified_api_requests": ["GET /api/branch", "GET /api/followup"],
                                "extra_api_attribution": [
                                    {"identity": "GET http://old/api/branch", "dispatch_bases": ["static_request_sink"]},
                                    {"identity": "GET http://old/api/followup", "dispatch_bases": ["response_extraction"],
                                     "parents": ["http://old/api/branch"]}]}]}
        target = {"url": "http://new/api/branch", "method": "GET", "attempts": [],
                  "discoveries": [{"conditional": True}],
                  "deferred": [{"reason": "conditional_execution_unresolved"}]}
        comparison = compare_extra_targets(previous, [{"id": "case"}], {"case": {"targets": [target]}})
        self.assertEqual(comparison["prior_extra_count"], 2)
        self.assertEqual(comparison["remaining_requests"], 0)
        self.assertEqual([row["disposition"] for row in comparison["items"]],
                         ["retained_until_execution", "unselected_parent_response_not_requested"])

    def test_prior_comparison_does_not_hide_remaining_requests(self):
        previous = {"results": [{"id": "case", "unclassified_api_requests": ["GET /api/branch"]}]}
        target = {"url": "http://new/api/branch", "method": "GET", "attempts": [{"http_status": 200}]}
        comparison = compare_extra_targets(previous, [{"id": "case"}], {"case": {"targets": [target]}})
        self.assertEqual(comparison["remaining_requests"], 1)
        self.assertEqual(comparison["items"][0]["disposition"], "still_requested")

if __name__ == "__main__":
    unittest.main()
