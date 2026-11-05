"""Legacy-entry routing checks with constructed HTTP responses, no network."""
import json
import os
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

import requests

from modules.google_patents_transport import CANONICAL_ENTRY, LEGACY_ENTRY, ENTRY_VARIABLE
from modules.google_patent_documents import fetch_google_document
from modules.patent_search import search_patents

URL = CANONICAL_ENTRY + 'patent/CN118943878A/zh'
BODY = ('<meta name="DC.title" content="fixture">'
        '<div class="claim" num="1">1. fixture claim</div>'
        '<section itemprop="description"><div class="description-paragraph">'
        'fixture description</div></section>')


def response(status=200, *, location='', body='{}'):
    result = requests.Response()
    result.status_code = status
    result.url = URL
    result.encoding = 'utf-8'
    result._content = body.encode('utf-8')
    result._content_consumed = True
    if location:
        result.headers['Location'] = location
    return result


class GooglePatentsTransportTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.dict(os.environ, {ENTRY_VARIABLE: LEGACY_ENTRY})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.sleeper = patch('modules.patent_http.time.sleep')
        self.sleeper.start()
        self.addCleanup(self.sleeper.stop)

    def test_search_uses_legacy_then_full_original_query(self):
        query = 'x' * 399 + '齿'
        payload = {'results': [{'patent': {'publication_number': 'CN118943878A', 'title': 'fixture'}}]}
        get = Mock(side_effect=[response(302, location=CANONICAL_ENTRY + '?oq=ignored'),
                                response(body=json.dumps(payload))])
        rows = search_patents(' ' + query + ' ', 1, get=get)
        first, second = get.call_args_list
        self.assertEqual(first.args[0], LEGACY_ENTRY)
        self.assertEqual(first.kwargs['params'], {'q': query})
        self.assertEqual(second.args[0], CANONICAL_ENTRY + 'xhr/query')
        self.assertEqual(parse_qs(second.kwargs['params']['url']), {'q': [query], 'num': ['1']})
        self.assertTrue(all(c.kwargs['allow_redirects'] is False for c in get.call_args_list))
        self.assertEqual(rows[0]['url'], CANONICAL_ENTRY + 'patent/CN118943878A/en')

    def test_document_uses_legacy_and_retains_requested_language(self):
        get = Mock(side_effect=[response(301, location=CANONICAL_ENTRY + 'patent/CN118943878A'),
                                response(body=BODY)])
        doc = fetch_google_document(URL, get=get)
        self.assertEqual([c.args[0] for c in get.call_args_list],
                         [LEGACY_ENTRY + 'CN118943878A', URL])
        self.assertEqual(doc.id, 'CN118943878A')
        self.assertEqual(doc.url, URL)
        self.assertTrue(doc.parsed_complete)
        self.assertTrue(all(c.kwargs['allow_redirects'] is False for c in get.call_args_list))

    def test_default_route_still_requests_canonical_directly(self):
        os.environ.pop(ENTRY_VARIABLE)
        get = Mock(return_value=response())
        search_patents('fixture', get=get)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], CANONICAL_ENTRY + 'xhr/query')
        get.reset_mock()
        get.return_value = response(body=BODY)
        self.assertEqual(fetch_google_document(URL, get=get).url, URL)
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0], URL)

    def test_search_rejects_unexpected_redirect_before_second_request(self):
        for location in ['https://example.com/', 'http://patents.google.com/',
                         'https://patents.google.com:444/',
                         'https://user@patents.google.com/', CANONICAL_ENTRY + 'patent/US123']:
            with self.subTest(location=location):
                get = Mock(return_value=response(302, location=location))
                with self.assertRaises(RuntimeError):
                    search_patents('fixture', get=get)
                get.assert_called_once()

    def test_document_rejects_changed_publication(self):
        get = Mock(return_value=response(301, location=CANONICAL_ENTRY + 'patent/CN999A'))
        with self.assertRaisesRegex(RuntimeError, '公开号不一致'):
            fetch_google_document(URL, get=get)
        get.assert_called_once()

    def test_document_rejects_offsite_and_query_redirects(self):
        for location in ['https://example.com/patent/CN118943878A', URL + '?other=1']:
            with self.subTest(location=location):
                get = Mock(return_value=response(301, location=location))
                with self.assertRaises(RuntimeError):
                    fetch_google_document(URL, get=get)
                get.assert_called_once()

    def test_legacy_503_does_not_fall_back(self):
        for operation, argument in [(search_patents, 'fixture'), (fetch_google_document, URL)]:
            with self.subTest(operation=operation.__name__):
                get = Mock(return_value=response(503))
                with self.assertRaises(RuntimeError):
                    operation(argument, get=get)
                self.assertEqual(get.call_count, 3)
                self.assertEqual(len({c.args[0] for c in get.call_args_list}), 1)

    def test_final_503_remains_failure(self):
        get = Mock(side_effect=[response(302, location=CANONICAL_ENTRY),
                                response(503), response(503), response(503)])
        with self.assertRaises(RuntimeError):
            search_patents('fixture', get=get)
        self.assertEqual(get.call_count, 4)

    def test_unexpected_second_document_redirect_is_rejected(self):
        get = Mock(side_effect=[response(301, location=URL), response(302, location=URL)])
        with self.assertRaisesRegex(RuntimeError, '重定向'):
            fetch_google_document(URL, get=get)
        self.assertEqual(get.call_count, 2)

    def test_legacy_200_does_not_silently_switch_entry(self):
        get = Mock(return_value=response())
        with self.assertRaisesRegex(RuntimeError, '未返回预期'):
            search_patents('fixture', get=get)
        get.assert_called_once()

    def test_invalid_entry_does_not_send_network_request(self):
        os.environ[ENTRY_VARIABLE] = 'https://example.com/'
        get = Mock()
        with self.assertRaises((ValueError, RuntimeError)):
            search_patents('fixture', get=get)
        get.assert_not_called()

    def test_overlong_query_is_rejected_before_legacy_request(self):
        get = Mock()
        with self.assertRaisesRegex(ValueError, '400'):
            search_patents('x' * 401, get=get)
        get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
