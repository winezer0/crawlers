import unittest
from run_next_scenario_matrix import evaluate, key

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

if __name__ == "__main__":
    unittest.main()
