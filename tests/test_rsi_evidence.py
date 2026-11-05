"""Real-search/evidence integration regressions; all model and search calls are mocked."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from modules.novelty_models import PatentDocument, SourceSection
from modules.rsi_experiment import SCHEMA, run_experiment, score_predictions, validate_gold, validate_inputs
from modules.rsi_trial import ModelPatentTrial
from modules.rsi_scoring import finite_weights


def fixture():
    targets = [{'id': 'C1', 'title': '装置权项', 'features': [
        {'id': 'F1', 'text': '控制器连接泵。'}, {'id': 'F2', 'text': '反馈电路调节泵。'}]},
        {'id': 'C2', 'title': '方法权项', 'features': [
            {'id': 'F1', 'text': '控制器连接泵。'}, {'id': 'F2', 'text': '双向试探后缩小步长。'}]}]
    case = {'case_id': 'case-01', 'text': '模拟控制装置和调节方法。', 'cutoff': '2024-01-01', 'assessment_targets': targets}
    inputs = {'schema': SCHEMA, 'dataset_id': 'synthetic-evidence', 'provenance': 'teaching_demo',
              'risk_levels': ['H', 'P', 'C', 'U'], 'abstain_label': 'U', 'cases': [case]}
    document = PatentDocument('US123A', '合成对照', 'https://patents.google.com/patent/US123A', '2020-01-01',
        (SourceSection('claim-1', '控制器连接泵。反馈电路调节泵。'),), complete=True)
    labels = []
    judgments = []
    for target in targets:
        full = target['id'] == 'C1'
        statuses = {'F1': 'disclosed', 'F2': 'disclosed' if full else 'not_found'}
        label = {'relevance': 'related', 'prior_art_at_cutoff': True,
                 'coverage': 'full_disclosure' if full else 'partial_coverage', 'feature_status': statuses,
                 'evidence_sections': {fid: ['claim-1'] for fid, s in statuses.items() if s == 'disclosed'}}
        labels.append({'document_id': document.id, 'target_id': target['id'], 'label': label})
        judgments.append({'document_id': document.id, 'target_id': target['id'], 'status': 'completed',
            'relevance': 'related', 'relevance_basis': '共享控制装置', 'relevance_section_ids': ['claim-1'],
            'prior_art': True, 'date_basis': '2020早于2024', 'coverage': label['coverage'], 'evidence_scope_complete': True,
            'rows': [{'feature_id': fid, 'status': s, 'basis': '逐项核对',
                      'evidence': [{'section_id': 'claim-1', 'quote': '控制器连接泵。' if fid == 'F1' else '反馈电路调节泵。'}]
                      if s == 'disclosed' else []} for fid, s in statuses.items()], 'key_gaps': [] if full else ['未见双向试探']})
    reference = {'case_id': case['case_id'], 'risk_label': 'H', 'source_target_id': 'A',
        'targets': [{'target_id': 'A', 'title': document.title, 'publication_ids': [document.id],
                     'title_aliases': [], 'publication_date': document.publication_date}],
        'assessment': {'profile': 'evidence-v2', 'references': labels}}
    gold = {'schema': SCHEMA, 'dataset_id': inputs['dataset_id'], 'cases': [reference]}
    prediction = {'case_id': case['case_id'], 'execution': 'completed', 'risk_label': 'U',
        'retrieved': [{'publication_id': document.id, 'title': document.title, 'url': document.url,
                       'publication_date': document.publication_date, 'snapshot_hash': document.snapshot_hash}],
        'evidence_judgments': judgments, 'source_snapshots': [document.to_dict()]}
    return inputs, gold, prediction, document


class EvidenceScoringTests(unittest.TestCase):
    def setUp(self):
        self.inputs, self.gold, self.prediction, self.document = fixture()

    def score(self):
        return score_predictions(self.inputs, self.gold, [self.prediction], (0.5, 0.5))

    def test_two_claims_keep_distinct_labels_and_full_credit(self):
        validate_gold(validate_inputs(self.inputs), self.gold)
        score = self.score()
        self.assertEqual(score['retrieval_score'], 100)
        self.assertEqual(score['risk_score'], 100)
        self.assertTrue(score['adoption_eligible'])
        self.assertEqual([p['judgment_correct'] for p in score['cases'][0]['assessment_pools']], [1, 1])

    def test_risk_label_cannot_override_per_claim_evidence(self):
        self.prediction['risk_label'] = 'H'
        bad = self.prediction['evidence_judgments'][1]
        bad['rows'][1].update(status='disclosed', evidence=[{'section_id': 'claim-1', 'quote': '反馈电路调节泵。'}])
        bad['coverage'] = 'full_disclosure'
        result = self.score()
        self.assertLess(result['risk_score'], 100)
        self.assertEqual(result['cases'][0]['assessment_pools'][1]['full_disclosure_false_positives'], 1)

    def test_fabricated_quote_is_unjudged_and_blocks_adoption(self):
        self.prediction['evidence_judgments'][0]['rows'][0]['evidence'][0]['quote'] = '伪造引文'
        result = self.score()
        self.assertFalse(result['adoption_eligible'])
        self.assertEqual(result['cases'][0]['unjudged'], 1)
        self.assertIsNone(result['risk_score'])
        self.assertIsNone(result['total_score'])

    def test_missing_target_judgment_is_not_low_risk(self):
        self.prediction['evidence_judgments'].pop()
        result = self.score()
        self.assertFalse(result['adoption_eligible'])
        self.assertEqual(result['cases'][0]['unjudged'], 1)

    def test_duplicate_document_target_judgment_blocks_adoption(self):
        self.prediction['evidence_judgments'].append(deepcopy(self.prediction['evidence_judgments'][0]))
        self.assertFalse(self.score()['adoption_eligible'])

    def test_retrieval_duplicates_do_not_inflate_score(self):
        self.prediction['retrieved'] *= 2
        self.assertEqual(self.score()['retrieval_score'], 100)

    def test_missing_source_snapshot_blocks_adoption(self):
        self.prediction['source_snapshots'] = []
        self.assertFalse(self.score()['adoption_eligible'])

    def test_mismatched_snapshot_hash_blocks_adoption(self):
        self.prediction['retrieved'][0]['snapshot_hash'] = 'wrong'
        self.assertFalse(self.score()['adoption_eligible'])

    def test_snapshot_date_contradiction_is_unjudged(self):
        self.prediction['evidence_judgments'][0]['prior_art'] = False
        self.assertFalse(self.score()['adoption_eligible'])

    def test_unlabelled_search_result_is_pending_not_an_invented_false_positive(self):
        self.prediction['retrieved'].append({'publication_id': 'US999A', 'title': '新文献',
            'url': 'https://patents.google.com/patent/US999A', 'publication_date': '2020-01-01'})
        row = self.score()['cases'][0]
        self.assertFalse(row['adoption_eligible'])
        self.assertEqual(row['pending_reference_documents'], ['US999A'])
        self.assertEqual(row['retrieval_metrics']['fp'], 0)

    def test_retrieval_miss_reduces_recall_without_fabricated_judgments(self):
        ref = self.gold['cases'][0]
        extra = deepcopy(ref['targets'][0]); extra.update(target_id='B', publication_ids=['US456A'], title='第二文献')
        ref['targets'].append(extra)
        for item in deepcopy(ref['assessment']['references']):
            item['document_id'] = 'US456A'; ref['assessment']['references'].append(item)
        score = self.score()
        self.assertEqual(score['retrieval_score'], 50)
        self.assertTrue(score['adoption_eligible'])

    def test_empty_actual_search_is_zero_credit_not_an_invented_correct_abstention(self):
        self.prediction.update(retrieved=[], evidence_judgments=[], source_snapshots=[])
        score = self.score()
        self.assertEqual(score['risk_score'], 0)
        self.assertEqual(score['retrieval_score'], 0)

    def test_reference_requires_all_features_and_all_claims(self):
        self.gold['cases'][0]['assessment']['references'].pop()
        with self.assertRaises(ValueError): validate_gold(self.inputs, self.gold)

    def test_private_reference_fields_rejected_from_public_targets(self):
        self.inputs['cases'][0]['assessment_targets'][0]['label'] = 'high'
        with self.assertRaises(ValueError): validate_gold(self.inputs, self.gold)

    def test_nonfinite_weights_rejected(self):
        for weights in [(float('nan'), .5), (float('inf'), 0), (True, 0)]:
            with self.assertRaises(ValueError): finite_weights(weights)

    def test_false_negative_and_partial_local_confusion_reported(self):
        for judgment in self.prediction['evidence_judgments']:
            for row in judgment['rows']: row.update(status='not_found', evidence=[])
            judgment['coverage'] = 'local_association'
        pools = self.score()['cases'][0]['assessment_pools']
        self.assertEqual(pools[0]['full_disclosure_false_negatives'], 1)
        self.assertEqual(pools[1]['coverage_confusion'], 1)

    def test_evidence_position_must_match_independent_reference(self):
        self.gold['cases'][0]['assessment']['references'][0]['label']['evidence_sections']['F1'] = ['claim-7']
        self.assertEqual(self.score()['cases'][0]['assessment_pools'][0]['evidence_errors'], 1)


class EvidenceTrialTests(unittest.TestCase):
    def test_actual_search_and_each_claim_receive_current_skill_but_no_gold(self):
        inputs, gold, prediction, doc = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp)/'SKILL.md'; skill.write_text('本轮特定改进指令', encoding='utf-8')
            client = Mock(); client.complete.side_effect = [json.dumps({'queries': ['控制器 泵']}),
                *[json.dumps(j, ensure_ascii=False) for j in prediction['evidence_judgments']]]
            search = Mock(return_value=[{'url': doc.url}])
            trial = ModelPatentTrial(client, search, Mock(return_value=doc))
            result = trial(skill, inputs['cases'][0], {'risk_levels': inputs['risk_levels'], 'abstain_label': 'U',
                                                     'budget': {'max_queries': 4, 'max_documents': 20}})
            self.assertEqual(client.complete.call_count, 3)
            self.assertEqual(len(result['evidence_judgments']), 2)
            self.assertEqual(search.call_count, 1)
            for call in client.complete.call_args_list:
                self.assertIn('本轮特定改进指令', call.args[0])
                self.assertNotIn('feature_status', call.args[1])
                self.assertNotIn('evidence_sections', call.args[1])
            self.assertEqual(score_predictions(inputs, gold, [result], (.5, .5))['risk_score'], 100)

    def test_five_fresh_pairs_and_final_only_anchor_preserved(self):
        inputs, gold, prediction, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); formal = root/'formal/patent-precheck/SKILL.md'; formal.parent.mkdir(parents=True)
            formal.write_text('---\nname: test\n---\n固定基线。', encoding='utf-8')
            calls = []; proposals = []
            def predict(skill, case, settings):
                calls.append((skill, case['case_id']))
                out = deepcopy(prediction); out['case_id'] = case['case_id']; return out
            def propose(feedback):
                proposals.append(feedback)
                return {'addition': '保留条件并逐项定位证据。', 'rationale': '提高定位完整性。'}
            def anchor():
                self.assertEqual(len(proposals), 5)
                new_inputs, new_gold = deepcopy(inputs), deepcopy(gold)
                new_inputs['cases'][0].update(case_id='anchor-02', text='独立的合成锚案。')
                new_gold['cases'][0]['case_id'] = 'anchor-02'
                return new_inputs, new_gold
            result = run_experiment(inputs, gold, root/'run', predict, propose, formal_root=formal.parents[1],
                                    mode='teaching_demo', anchor=anchor)
            self.assertEqual(len(calls), 13)
            self.assertEqual(result['accepted_rounds'], [])
            self.assertEqual([c[1] for c in calls[-2:]], ['anchor-02', 'anchor-02'])
            self.assertTrue(result['formal_skills_unchanged'])
            self.assertNotIn('evidence_sections', json.dumps(proposals))

    def test_invalid_evidence_cannot_be_adopted_even_with_higher_total(self):
        inputs, gold, prediction, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); formal = root/'formal/patent-precheck/SKILL.md'; formal.parent.mkdir(parents=True)
            formal.write_text('---\nname: test\n---\n固定基线。', encoding='utf-8')
            def predict(skill, case, settings):
                out = deepcopy(prediction)
                if '改进' in skill.read_text(encoding='utf-8'):
                    out['evidence_judgments'].pop()
                return out
            result = run_experiment(inputs, gold, root/'run', predict,
                lambda _: {'addition': '改进证据定位。', 'rationale': '测试'}, formal_root=formal.parents[1],
                mode='teaching_demo', rounds=1)
            self.assertFalse(result['rounds'][0]['accepted'])
            self.assertFalse(result['rounds'][0]['candidate_score']['adoption_eligible'])


if __name__ == '__main__':
    unittest.main()
