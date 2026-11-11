import json
from unittest import TestCase
from unittest.mock import Mock

from modules.novelty_models import Feature
from modules.novelty_matching import SynonymLexicon
from modules.patsnap_mcp import PatsnapMCP
from modules.patsnap_query_plan import compile_groups, concept_plan, fuse_candidates, validate_anchors
from modules.synonym_expansion import build_expansion_plan, candidate_id, generate_llm_candidates
from modules.llm_client import LLMOutputTruncated


class ConceptQueryTests(TestCase):
    def setUp(self):
        self.features = [Feature('F1', '永磁同步电机经隔振联轴器驱动离心叶轮，控制器维持转速且不改变旋转方向。')]
        self.row = dict(feature_id='F1', source='永磁同步电机', term='permanent magnet synchronous motor',
                        language='en', category='component', relation='equivalent', rationale='合成译名，需审核')
        self.generated = {'status': 'ok', 'anchors': [
            {'feature_id': 'F1', 'term': '永磁同步电机'}, {'feature_id': 'F1', 'term': '离心叶轮'}],
            'candidates': [self.row]}

    def plan(self, approved=True, budget=8):
        expansion = build_expansion_plan(self.features, SynonymLexicon(), 'engineering', generated=self.generated,
                                         approved_ids=[candidate_id(self.row)] if approved else [])
        return concept_plan(self.features, expansion, budget=budget, before='2024-01-01')

    def test_alias_reaches_sent_query_and_keeps_independent_concept(self):
        plan = self.plan()
        expanded = next(q for q in plan['queries'] if candidate_id(self.row) in q['candidate_ids'])
        self.assertIn('("永磁同步电机" OR "permanent magnet synchronous motor") AND "离心叶轮"', expanded['query'])
        self.assertIn('PBD:[19000101 TO 20231231]', expanded['query'])
        self.assertEqual(plan['queries'][0]['candidate_ids'], [])
        self.assertEqual(self.features[0].text, '永磁同步电机经隔振联轴器驱动离心叶轮，控制器维持转速且不改变旋转方向。')

    def test_unreviewed_alias_cannot_reach_query(self):
        self.assertTrue(all(self.row['term'] not in q['query'] for q in self.plan(False)['queries']))

    def test_budget_records_unused_reviewed_alias(self):
        plan = self.plan(budget=1)
        self.assertEqual(len(plan['queries']), 1)
        self.assertIn(candidate_id(self.row), [r['candidate_id'] for r in plan['unused_approved_candidates']])
        self.assertTrue(plan['skipped'])

    def test_targeted_fallback_is_one_concept_not_all_parts_or(self):
        fallback = [q for q in self.plan()['queries'] if q['kind'] == 'focused_fallback']
        self.assertTrue(fallback)
        self.assertTrue(all(len(q['concept_groups']) == 1 for q in fallback))

    def test_long_claim_without_anchors_fails_explicitly(self):
        expansion = build_expansion_plan(self.features, SynonymLexicon(), 'engineering')
        with self.assertRaisesRegex(ValueError, 'anchors'):
            concept_plan(self.features, expansion, budget=8)

    def test_anchor_cannot_import_synonym_or_query_injection(self):
        for term in ('异步电机', '电机" OR "装置', '系统'):
            with self.assertRaises(ValueError):
                validate_anchors([dict(feature_id='F1', term=term)], self.features)

    def test_compiler_is_independent_of_reference_identities(self):
        query = compile_groups([['电驱动器', 'electric actuator'], ['膜片']], '2024-01-01')
        self.assertIn(' AND ', query)
        self.assertNotIn('CN', query)

    def test_round_robin_prevents_first_query_exhausting_body_budget(self):
        candidates = [dict(publication_id=str(i), observations=[dict(query_id='Q1', rank=i+1)]) for i in range(6)]
        candidates += [dict(publication_id='other', observations=[dict(query_id='Q2', rank=1)])]
        self.assertEqual([r['publication_id'] for r in fuse_candidates(candidates)][:2], ['0', 'other'])

    def test_legal_empty_gateway_result_is_not_infrastructure_failure(self):
        client = PatsnapMCP()
        client.call = Mock(return_value={'status': True, 'data': {'local_hit_count': 0}})
        self.assertEqual(client.search('TACD:("不存在的术语")', 5), {'results': [], 'total_matches': 0})
        client.call.return_value = {'status': True, 'data': {}}
        with self.assertRaises(RuntimeError): client.search('TACD:("术语")', 5)

    def test_truncation_retry_is_bounded_and_limit_is_recorded(self):
        client = Mock(model='fake', url='https://model.invalid')
        client.complete.side_effect = [LLMOutputTruncated('truncated'), json.dumps(self.generated)]
        result = generate_llm_candidates(self.features, SynonymLexicon(), 'engineering', [], client, True)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([c.kwargs['max_tokens'] for c in client.complete.call_args_list], [8000, 12000])
        self.assertEqual(result['cost']['llm_calls'], 2)

    def test_auth_or_quota_failure_is_not_retried_by_generator(self):
        client = Mock(model='fake', url='https://model.invalid')
        client.complete.side_effect = RuntimeError('API failed')
        result = generate_llm_candidates(self.features, SynonymLexicon(), 'engineering', [], client, True)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(client.complete.call_count, 1)

    def test_over_cap_output_is_bounded_with_explicit_dropped_positions(self):
        raw = [self.row | {'term': 'alternate motor ' + 'a' * (i + 1)} for i in range(40)]
        client = Mock(model='fake', url='https://model.invalid')
        client.complete.return_value = json.dumps(self.generated | {'candidates': raw})
        result = generate_llm_candidates(self.features, SynonymLexicon(), 'engineering', [], client, True)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['candidates']), 36)
        self.assertEqual(result['cost']['dropped_candidates'], 4)
        self.assertEqual(result['cost']['dropped_candidate_indices'], [36, 37, 38, 39])

    def test_malformed_discarded_candidate_is_not_hidden_by_capping(self):
        raw = [self.row | {'term': 'alternate motor ' + 'a' * (i + 1)} for i in range(40)]
        raw[-1] = self.row | {'language': 'invalid'}
        client = Mock(model='fake', url='https://model.invalid')
        client.complete.return_value = json.dumps(self.generated | {'candidates': raw})
        result = generate_llm_candidates(self.features, SynonymLexicon(), 'engineering', [], client, True)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['candidates'], [])
        self.assertEqual(client.complete.call_count, 3)
