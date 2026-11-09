"""Offline failure/restart tests; no Google or model requests."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from modules.google_patent_documents import fetch_google_document
from modules.novelty_matching import SynonymLexicon
from modules.novelty_models import Feature
from modules.novelty_search import retrieve_with_journal
from modules.patent_http import get_patent
from modules.patent_network_state import PatentAccessPaused, PatentNetworkState, save_verified_response
from modules.patent_search import search_patents
from modules.rsi_trial import ModelPatentTrial
from tests.test_google_patents_transport import response, URL, BODY

BLOCK = '<html>Your computer or network may be sending automated queries.</html>'


class NetworkStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000.0
        self.waits = []
        self.state = self.new_state()

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds

    def new_state(self):
        return PatentNetworkState(Path(self.temp.name)/'network.sqlite3', clock=lambda: self.now, sleep=self.sleep)

    def test_block_stops_after_one_request_and_survives_restart(self):
        get = Mock(return_value=response(503, body=BLOCK))
        with self.assertRaises(PatentAccessPaused):
            get_patent(get, URL, state=self.state)
        get.assert_called_once()
        with self.assertRaises(PatentAccessPaused):
            get_patent(get, URL, state=self.new_state())
        get.assert_called_once()
        self.assertEqual(self.waits, [])
        self.state.resume()
        get.return_value = response(body=BODY)
        self.assertEqual(get_patent(get, URL, state=self.state).status_code, 200)

    def test_200_challenge_is_not_cached_as_a_document(self):
        get = Mock(return_value=response(200, body=BLOCK))
        with self.assertRaises(PatentAccessPaused):
            get_patent(get, URL, state=self.state)
        with self.state.connection() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM responses').fetchone()[0], 0)

    def test_patent_discussing_recaptcha_is_not_an_access_challenge(self):
        get = Mock(return_value=response(body=BODY + '<p>A component named g-recaptcha.</p>'))
        self.assertEqual(get_patent(get, URL, state=self.state).status_code, 200)
        self.assertFalse(self.state.status()['paused'])

    def test_instances_share_pacing_and_verified_cache(self):
        get = Mock(return_value=response(body=BODY))
        first = get_patent(get, URL, state=self.state)
        save_verified_response(first)
        cached = get_patent(get, URL, state=self.new_state())
        self.assertEqual(cached.content, first.content)
        self.assertEqual(cached.patent_origin['fetched_at'], first.patent_origin['fetched_at'])
        get.assert_called_once()
        get_patent(get, URL.replace('/zh', '/en'), state=self.new_state())
        self.assertEqual(get.call_count, 2)
        self.assertEqual(self.waits, [5])

    def test_cache_tampering_fails_without_network(self):
        get = Mock(return_value=response(body=BODY))
        save_verified_response(get_patent(get, URL, state=self.state))
        with self.state.connection() as con:
            con.execute("UPDATE responses SET body=X'00'")
        with self.assertRaisesRegex(ValueError, '缓存校验失败'):
            get_patent(get, URL, state=self.state)
        get.assert_called_once()

    def test_parser_preserves_snapshot_hash_and_retrieval_time_on_replay(self):
        get = Mock(return_value=response(body=BODY))
        def managed(getter, url, **kwargs):
            return get_patent(getter, url, state=self.state, **kwargs)
        with patch('modules.google_patents_transport.get_patent', side_effect=managed):
            first = fetch_google_document(URL, get=get)
            self.now += 50
            second = fetch_google_document(URL, get=get)
        self.assertEqual(first.snapshot_hash, second.snapshot_hash)
        self.assertEqual(first.retrieved_at, second.retrieved_at)
        get.assert_called_once()

    def test_search_cache_is_bound_to_full_query_and_requested_count(self):
        payload = '{"results":[{"patent":{"publication_number":"CN123A","title":"fixture"}}]}'
        get = Mock(return_value=response(body=payload))
        def managed(getter, url, **kwargs):
            result = get_patent(getter, url, state=self.state, **kwargs)
            # A real response URL includes /xhr/query, unlike the shared fixture.
            result.url = url
            return result
        with patch('modules.google_patents_transport.get_patent', side_effect=managed):
            first = search_patents('same exact query', 1, get=get)
            second = search_patents('same exact query', 1, get=get)
            search_patents('same exact query', 2, get=get)
        self.assertEqual(first, second)
        self.assertEqual(get.call_count, 2)

    def test_batch_retains_first_success_and_does_not_send_remaining_queries(self):
        search = Mock(side_effect=[[], PatentAccessPaused('blocked')])
        fetch = Mock()
        _, audit = retrieve_with_journal([Feature('F1','第一对象'), Feature('F2','第二对象'), Feature('F3','第三对象')],
            '2024-01-01', '', SynonymLexicon([]), search, fetch, 2)
        self.assertEqual(search.call_count, 2)
        self.assertEqual([r['status'] for r in audit['queries']], ['ok', 'error', 'not_executed'])
        fetch.assert_not_called()

    def test_interrupted_trial_reuses_plan_but_completed_trial_plans_afresh(self):
        skill = Path(self.temp.name)/'SKILL.md'
        skill.write_text('keep original constraints', encoding='utf-8')
        client = Mock(model='fixture-model', url='https://example.test')
        client.complete.return_value = '{"queries":["frozen query"]}'
        search = Mock(side_effect=[PatentAccessPaused('blocked'), [], []])
        trial = ModelPatentTrial(client, searcher=search, fetcher=Mock())
        trial.network_state = self.state
        case = {'case_id':'fixture', 'cutoff':'2024-01-01'}
        settings = {'budget':{'max_queries':1,'max_documents':1},'abstain_label':'unknown'}
        with self.assertRaises(PatentAccessPaused):
            trial(skill, case, settings)
        resumed = ModelPatentTrial(client, searcher=search, fetcher=Mock())
        resumed.network_state = self.new_state()
        self.assertEqual(resumed(skill, case, settings)['queries'], ['frozen query'])
        client.complete.assert_called_once()
        resumed(skill, case, settings)
        self.assertEqual(client.complete.call_count, 2)

    def test_expired_search_result_is_refetched(self):
        url = 'https://patents.google.com/xhr/query'
        get = Mock(return_value=response(body='{"results":[]}'))
        get.return_value.url = url
        save_verified_response(get_patent(get, url, state=self.state, params={'q':'query'}))
        self.now += 3601
        get_patent(get, url, state=self.new_state(), params={'q':'query'})
        self.assertEqual(get.call_count, 2)


if __name__ == '__main__':
    unittest.main()
