import unittest
from katana_effective_compare import classify


class ClassificationTests(unittest.TestCase):
    def row(self, **values):
        return dict(status=200, url='https://x/api/a', final_url='https://x/api/a', sha256='a', content_type='application/json', title='', **values)

    def test_html_fallback_is_not_an_api_success(self):
        row = self.row()
        row['content_type'] = 'text/html'
        self.assertEqual(classify(row, {'sha256': 'a'}), 'html_fallback')

    def test_authentication_does_not_prove_nonexistence(self):
        row = self.row()
        row['status'] = 403
        self.assertEqual(classify(row, {}), 'restricted_or_blocked')
        row['status'], row['final_url'] = 200, 'https://x/login'
        self.assertEqual(classify(row, {}), 'authentication_redirect')

    def test_json_and_errors(self):
        self.assertEqual(classify(self.row(), {}), 'valid_json')
        row = self.row()
        row['status'] = 404
        self.assertEqual(classify(row, {}), 'not_found')
        row['status'] = 0
        self.assertEqual(classify(row, {}), 'network_error')

    def test_failed_control_cannot_certify_html(self):
        row = self.row()
        row['content_type'] = 'text/html'
        self.assertEqual(classify(row, {'status': 0, 'error': 'timeout'}), 'unconfirmed_control')
        self.assertEqual(classify(row, {'status': 404, 'sha256': 'different'}), 'valid_page')


if __name__ == '__main__':
    unittest.main()
