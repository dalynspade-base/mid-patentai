import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from modules.retrieval_evaluation import evaluate_retrieval
from tools.run_fixed_corpus import corpus_documents


class RetrievalDiagnosticsTests(TestCase):
    def test_public_manual_claim_cannot_smuggle_expected_label_to_executor(self):
        from tests.test_rsi_handbook import fixture
        from modules.rsi_experiment import validate_inputs
        public, _, _ = fixture()
        validate_inputs(public)
        public['cases'][0]['assessment_targets'][0]['expected_label'] = 'high'
        with self.assertRaisesRegex(ValueError, 'reference fields are forbidden'):
            validate_inputs(public)

    def test_fixed_corpus_can_never_earn_retrieval_or_rsi_adoption(self):
        from tests.test_rsi_handbook import fixture, review_for
        from modules.rsi_experiment import score_predictions
        public, gold, prediction = fixture()
        prediction['evaluation_scope'] = 'evidence_only_no_retrieval_metric'
        review = review_for(public, gold, prediction)
        report = score_predictions(public, gold, [prediction], (.5, .5),
                                   reviews={public['cases'][0]['case_id']: review})
        self.assertIsNone(report['retrieval_score'])
        self.assertIsNone(report['total_score'])
        self.assertFalse(report['adoption_eligible'])
        self.assertEqual(report['components']['document_evidence'], 45)
        self.assertEqual(report['components']['claim_conclusion'], 20)

    def setUp(self):
        self.standard = {'documents': [
            {'publication_id': 'CN123A', 'relevance': 'R3', 'weight': 3},
            {'publication_id': 'CN456A', 'relevance': 'R1', 'weight': 1}]}
        self.audit = {'queries': [dict(id='Q1', status='ok', total_matches=900000,
                                      results=[{'publication_id': 'CN123A'}, {'publication_id': 'CN789A'}])],
                      'candidate_order': ['CN789A', 'CN123A'], 'fetched_order': ['CN789A'], 'eligible_fetched_order': []}

    def test_separates_total_hits_known_recall_and_unjudged(self):
        result = evaluate_retrieval(self.audit, self.standard)
        m = result['stages']['platform_returned_pool']
        self.assertEqual(m['known_recall_at_25'], .5)
        self.assertEqual(m['weighted_recall_at_25'], .75)
        self.assertEqual(m['unjudged_returned'], ['CN789A'])
        self.assertEqual(result['queries'][0]['platform_total_matches'], 900000)
        self.assertEqual(result['reference_losses'][0]['stage'], 'body_budget_or_fetch_failure')

    def test_missing_stage_is_unknown_not_zero(self):
        del self.audit['eligible_fetched_order']
        self.assertIsNone(evaluate_retrieval(self.audit, self.standard)['stages']['eligible_fetched_bodies'])

    def test_versions_match_only_with_explicit_verified_mapping(self):
        self.audit['queries'][0]['results'] = [{'publication_id': 'CN123B'}]
        self.assertEqual(evaluate_retrieval(self.audit, self.standard)['stages']['platform_returned_pool']['known_relevant_at_25'], 0)
        result = evaluate_retrieval(self.audit, self.standard, aliases={'CN123B': 'CN123A'})
        self.assertEqual(result['stages']['platform_returned_pool']['known_relevant_at_25'], 1)

    def test_fixed_corpus_refuses_answer_labels_and_future_evidence(self):
        row = dict(publication_id='CN123A', title='合成正文', url='https://example.invalid', publication_date='2023-01-01',
                   sections=[dict(id='C1', text='电驱动器连接膜片。')])
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / 'corpus.jsonl'
            p.write_text(json.dumps(row | {'claim_1': 'high'}), encoding='utf8')
            with self.assertRaisesRegex(ValueError, '答案字段'): corpus_documents(p, '2024-01-01')
            p.write_text(json.dumps(row), encoding='utf8')
            self.assertEqual(len(corpus_documents(p, '2024-01-01')), 1)
            with self.assertRaises(ValueError): corpus_documents(p, '2023-01-01')
