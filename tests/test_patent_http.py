"""Constructed transient failures verify retries, budgets and honest failure."""
import os
import unittest
from unittest.mock import Mock, patch

import requests
from modules.google_patent_documents import fetch_google_document
from modules.patent_http import get_patent
from modules.patent_search import search_patents
from tests.test_google_patents_transport import BODY, URL, response


class PatentHTTPTests(unittest.TestCase):
    def setUp(self):
        sleeper = patch('modules.patent_http.time.sleep')
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)
        entry = patch.dict(os.environ, {'GOOGLE_PATENTS_ENTRY_URL': 'https://patents.google.com/'})
        entry.start()
        self.addCleanup(entry.stop)

    def test_503_search_recovers_without_changing_query(self):
        get = Mock(side_effect=[response(503), response(body='{"result":{"patent":{"publication_number":"CN123A","title":"fixture"}}}')])
        rows = search_patents('rubidium 齿', get=get)
        self.assertEqual(rows[0]['publication_number'], 'CN123A')
        self.assertEqual(get.call_args_list[0], get.call_args_list[1])
        self.sleep.assert_called_once_with(5)

    def test_503_body_recovers_with_all_text_and_same_url(self):
        long_body = BODY.replace('fixture description', '完整正文。' * 30000)
        get = Mock(side_effect=[response(503), response(502), response(body=long_body)])
        doc = fetch_google_document(URL, get=get)
        self.assertEqual(sum(len(s.text) for s in doc.sections), 150016)
        self.assertEqual(doc.url, URL)
        self.assertEqual(get.call_args_list[0], get.call_args_list[2])
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [5, 15])

    def test_exhausted_503_never_becomes_empty_success(self):
        get = Mock(side_effect=[response(503), response(503), response(503)])
        with self.assertRaises(RuntimeError):
            search_patents('fixture', get=get)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_404_is_not_retried(self):
        get = Mock(return_value=response(404))
        with self.assertRaises(RuntimeError):
            fetch_google_document(URL, get=get)
        get.assert_called_once()
        self.sleep.assert_not_called()

    def test_connection_timeout_recovers(self):
        good = response()
        get = Mock(side_effect=[requests.Timeout('fixture'), good])
        self.assertIs(get_patent(get, URL), good)
        self.assertEqual(get.call_count, 2)

    def test_connection_timeout_exhausts_at_three(self):
        get = Mock(side_effect=requests.ConnectionError('fixture'))
        with self.assertRaises(requests.ConnectionError):
            get_patent(get, URL)
        self.assertEqual(get.call_count, 3)

    def test_certificate_errors_are_not_retried(self):
        get = Mock(side_effect=requests.exceptions.SSLError('fixture'))
        with self.assertRaises(requests.exceptions.SSLError):
            get_patent(get, URL)
        get.assert_called_once()
        self.sleep.assert_not_called()

    def test_retry_after_is_observed(self):
        limited = response(429)
        limited.headers['Retry-After'] = '8'
        get = Mock(side_effect=[limited, response()])
        get_patent(get, URL)
        self.sleep.assert_called_once_with(8)

    def test_long_retry_after_stops_instead_of_retrying_too_early(self):
        limited = response(429)
        limited.headers['Retry-After'] = '120'
        get = Mock(return_value=limited)
        self.assertIs(get_patent(get, URL), limited)
        get.assert_called_once()
        self.sleep.assert_not_called()

    def test_html_200_is_not_mistaken_for_a_search_result(self):
        get = Mock(return_value=response(body='<html>please try later</html>'))
        with self.assertRaises(RuntimeError):
            search_patents('fixture', get=get)
        get.assert_called_once()


if __name__ == '__main__':
    unittest.main()
