"""Synthetic RSI behavior checks; no external model/search requests."""
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from modules.rsi_experiment import (digest, match_target, run_experiment, score_predictions,
                                    validate_gold, validate_inputs)
from modules.rsi_trial import ModelPatentTrial, ModelSkillProposer
from modules.novelty_models import PatentDocument, SourceSection
from tools.run_rsi import TeachingTrial, TeachingProposer, teaching_dataset


class RSIExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.formal = self.root/'formal-skills'
        base = self.formal/'patent-synonym-expansion/SKILL.md'
        base.parent.mkdir(parents=True)
        base.write_text('---\nname: patent-synonym-expansion\ndescription: test\n---\n\n保留对象、关系和限定。\n', encoding='utf-8')
        self.initial_bytes = base.read_bytes()
        self.inputs, self.gold = teaching_dataset()

    def run_demo(self, **kwargs):
        return run_experiment(self.inputs, self.gold, self.root/'run', kwargs.pop('predictor', TeachingTrial()),
            kwargs.pop('proposer', TeachingProposer()), formal_root=self.formal, mode='teaching_demo', **kwargs)

    def test_five_fresh_paired_rounds_and_production_unchanged(self):
        predictor = Mock(wraps=TeachingTrial())
        result = self.run_demo(predictor=predictor)
        self.assertEqual(predictor.call_count, 11)
        self.assertEqual(result['accepted_rounds'], [1, 2])
        self.assertEqual(result['selected_round'], 2)
        self.assertEqual(len(list((self.root/'run').glob('round-*/patent-rsi-trial/SKILL.md'))), 5)
        self.assertEqual((self.formal/'patent-synonym-expansion/SKILL.md').read_bytes(), self.initial_bytes)
        self.assertEqual(result['generalization'], 'not_measured_no_anchor')
        self.assertIn('预设响应', (self.root/'run/result.md').read_text(encoding='utf-8'))

    def test_rejected_additions_are_not_inherited(self):
        result = self.run_demo()
        best = Path(result['best_skill']).read_text(encoding='utf-8')
        self.assertIn('演示改进项 2', best)
        self.assertNotIn('演示改进项 3', best)
        fifth = (self.root/'run/round-05/patent-rsi-trial/SKILL.md').read_text(encoding='utf-8')
        self.assertIn('演示改进项 5', fifth)
        self.assertNotIn('演示改进项 4', fifth)

    def test_higher_total_cannot_hide_risk_regression(self):
        def predict(skill, case, settings):
            result = TeachingTrial()(skill, case, settings)
            result['risk_label'] = '高' if '演示改进项' not in skill.read_text(encoding='utf-8') else '低'
            return result
        result = self.run_demo(predictor=predict, rounds=1, weights=(0.8, 0.2))
        row = result['rounds'][0]
        self.assertGreater(row['candidate_score']['total_score'], row['control_score']['total_score'])
        self.assertFalse(row['accepted'])
        self.assertEqual(result['selected_round'], 0)

    def test_duplicate_retrieval_counts_once_and_risk_uses_exact_label(self):
        path = self.root/'skill.md'
        path.write_text('演示改进项', encoding='utf-8')
        prediction = TeachingTrial()(path, self.inputs['cases'][0], {})
        prediction['retrieved'].append(deepcopy(prediction['retrieved'][0]))
        prediction['risk_label'] = '高'
        score = score_predictions(self.inputs, self.gold, [prediction], (0.5, 0.5))
        self.assertEqual(score['cases'][0]['found'], 2)
        self.assertAlmostEqual(score['retrieval_score'], 200/3)
        self.assertEqual(score['risk_score'], 100)
        prediction['risk_label'] = '中'
        self.assertEqual(score_predictions(self.inputs, self.gold, [prediction], (0.5, 0.5))['risk_score'], 0)

    def test_titles_are_fallback_only_and_aliases_are_explicit(self):
        target = deepcopy(self.gold['cases'][0]['targets'][0])
        document = {'publication_id': 'WRONG', 'title': target['title']}
        self.assertFalse(match_target(document, target))
        target['publication_ids'] = []
        target['title_aliases'] = ['Explicit English Alias']
        self.assertTrue(match_target({'publication_id': 'OTHER', 'title': 'Ｅｘｐｌｉｃｉｔ English-Alias'}, target))

    def test_future_reference_and_future_observation_rejected(self):
        gold = deepcopy(self.gold)
        gold['cases'][0]['targets'][0]['publication_date'] = '2025-01-01'
        with self.assertRaises(ValueError):
            validate_gold(self.inputs, gold)
        path = self.root/'skill.md'
        path.write_text('', encoding='utf-8')
        prediction = TeachingTrial()(path, self.inputs['cases'][0], {})
        prediction['retrieved'][0]['publication_date'] = '2025-01-01'
        with self.assertRaises(ValueError):
            score_predictions(self.inputs, self.gold, [prediction], (0.5, 0.5))

    def test_original_patent_a_and_nonoverlapping_target_aliases_required(self):
        gold = deepcopy(self.gold)
        gold['cases'][0]['source_target_id'] = 'ABSENT'
        with self.assertRaises(ValueError): validate_gold(self.inputs, gold)
        gold = deepcopy(self.gold)
        gold['cases'][0]['targets'][1]['publication_ids'] = ['RSIDEMO001']
        with self.assertRaises(ValueError): validate_gold(self.inputs, gold)

    def test_proposals_are_sealed_before_any_trial_without_case_or_score_feedback(self):
        proposer = Mock(wraps=TeachingProposer())
        def predict(skill, case, settings):
            self.assertEqual(proposer.call_count, 5)
            self.assertNotIn('训练反馈', skill.read_text(encoding='utf-8'))
            return TeachingTrial()(skill, case, settings)

        self.run_demo(proposer=proposer, predictor=predict)
        for call in proposer.call_args_list:
            payload = call.args[0]
            self.assertEqual(set(payload), {'skill_id', 'current_skill', 'round', 'development_examples', 'earlier_proposals'})
        self.assertTrue((self.root/'run/sealed_proposals.json').is_file())

    def test_hardcoded_reference_identity_is_rejected_before_trial(self):
        proposer = lambda feedback: {'addition': '直接查找 RSIDEMO001。', 'rationale': '背答案'}
        with self.assertRaises(ValueError): self.run_demo(proposer=proposer)
        self.assertTrue((self.root/'run/failure.md').is_file())
        self.assertFalse((self.root/'run/result.json').exists())

    def test_file_edit_requests_and_executable_blocks_are_not_proposals(self):
        for proposal in ({'path':'../formal-skills/SKILL.md', 'addition':'x', 'rationale':'x'},
                         {'addition':'```python\nprint(1)\n```', 'rationale':'x'}):
            from modules.rsi_experiment import _validate_proposal
            with self.assertRaises(ValueError): _validate_proposal(proposal, self.gold, [])

    def test_output_cannot_overlap_formal_skill_tree(self):
        with self.assertRaises(ValueError):
            run_experiment(self.inputs, self.gold, self.formal/'new', TeachingTrial(), TeachingProposer(),
                           formal_root=self.formal, mode='teaching_demo')
        self.assertEqual((self.formal/'patent-synonym-expansion/SKILL.md').read_bytes(), self.initial_bytes)

    def test_failed_trial_has_no_complete_or_zero_filled_score(self):
        predictor = Mock(side_effect=RuntimeError('unavailable'))
        with self.assertRaises(RuntimeError): self.run_demo(predictor=predictor)
        failure = json.loads((self.root/'run/failure.json').read_text(encoding='utf-8'))
        self.assertEqual(failure['execution'], 'failed')
        self.assertFalse((self.root/'run/baseline/score.json').exists())

    def test_a_changed_frozen_skill_or_score_config_stops_execution(self):
        def mutate(skill, case, settings):
            config = self.root/'run/config.json'
            config.write_text('{}', encoding='utf-8')
            return TeachingTrial()(skill, case, settings)
        with self.assertRaises(ValueError): self.run_demo(predictor=mutate)

    def test_anchor_is_only_loaded_after_selection(self):
        proposer = Mock(wraps=TeachingProposer())
        def anchor():
            self.assertEqual(proposer.call_count, 5)
            inputs, gold = teaching_dataset()
            inputs['cases'][0].update(case_id='anchor-01', text='不同的虚构锚集修改方案。')
            gold['cases'][0]['case_id'] = 'anchor-01'
            return inputs, gold
        result = self.run_demo(proposer=proposer, anchor=anchor)
        self.assertIsNotNone(result['anchor'])
        self.assertEqual(proposer.call_count, 5)
        self.assertNotIn('锚集', json.dumps([c.args[0] for c in proposer.call_args_list], ensure_ascii=False))

    def test_template_is_not_runnable_live_benchmark(self):
        inputs = deepcopy(self.inputs)
        inputs['provenance'] = 'template_unfilled'
        with self.assertRaises(ValueError): validate_inputs(inputs)
        with self.assertRaises(ValueError):
            run_experiment(self.inputs, self.gold, self.root/'live', TeachingTrial(), TeachingProposer(),
                           formal_root=self.formal, mode='live')


class RSIAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.skill = Path(self.temp.name)/'SKILL.md'
        self.skill.write_text('临时策略指令', encoding='utf-8')
        self.settings = {'budget': {'max_queries': 2, 'max_documents': 2},
                         'risk_levels': ['低', '高', '待核实'], 'abstain_label': '待核实'}
        self.case = {'case_id': 'copy', 'cutoff': '2024-01-01', 'text': '控制器启动泵。'}

    def test_actual_retrieval_and_verified_citation_are_used(self):
        doc = PatentDocument('US123A', '来源', 'https://patents.google.com/patent/US123A', '2020-01-01',
                             (SourceSection('s1', '控制器启动泵。'),))
        client = Mock()
        client.complete.side_effect = [json.dumps({'queries':['控制器 泵']}), json.dumps({'risk_label':'高', 'basis':'原文对照',
            'citations':[{'publication_id':'US123A','section_id':'s1','quote':'控制器启动泵。'}]})]
        fetch = Mock(return_value=doc)
        trial = ModelPatentTrial(client, lambda *args, **kwargs: [{'url':doc.url}], fetch)
        result = trial(self.skill, self.case, self.settings)
        self.assertEqual(result['retrieved'][0]['publication_id'], 'US123A')
        self.assertEqual(result['risk_label'], '高')
        self.assertEqual(fetch.call_count, 1)
        self.assertNotIn('临时策略指令', client.complete.call_args_list[0].args[0])
        self.assertNotIn('targets', json.loads(client.complete.call_args_list[0].args[1]))

    def test_query_budget_is_enforced_before_search(self):
        client = Mock()
        client.complete.return_value = json.dumps({'queries':['a','b','c']})
        search = Mock()
        with self.assertRaises(ValueError): ModelPatentTrial(client, search)(self.skill, self.case, self.settings)
        search.assert_not_called()

    def test_many_bodies_are_reviewed_separately_then_aggregated(self):
        docs = [PatentDocument(f'US{i}A', f'来源{i}', f'https://patents.google.com/patent/US{i}A', '2020-01-01',
                 (SourceSection('s1', '控制器启动泵。'+'描述'*13000),)) for i in (123, 124)]
        judgments = [{'risk_label':'高', 'basis':'逐篇核对', 'citations':[
            {'publication_id':d.id, 'section_id':'s1', 'quote':'控制器启动泵。'}]} for d in docs]
        client = Mock()
        client.complete.side_effect = [json.dumps({'queries':['控制器 泵']}), *map(json.dumps, judgments), json.dumps(judgments[0])]
        trial = ModelPatentTrial(client, lambda *args, **kwargs: [{'url':d.url} for d in docs], Mock(side_effect=docs))
        result = trial(self.skill, self.case, self.settings)
        self.assertEqual(client.complete.call_count, 4)
        self.assertEqual(len(result['document_reviews']), 2)
        self.assertEqual(len(result['source_snapshots']), 2)
        self.assertEqual(result['risk_label'], '高')
        for call in client.complete.call_args_list[1:3]:
            self.assertEqual(len(json.loads(call.args[1])['document']['sections']), 1)

    def test_fabricated_risk_citation_is_rejected(self):
        doc = PatentDocument('US123A', '来源', 'https://patents.google.com/patent/US123A', '2020-01-01',
                             (SourceSection('s1', '控制器启动泵。'),))
        client = Mock()
        client.complete.side_effect = [json.dumps({'queries':['控制器 泵']}), json.dumps({'risk_label':'高', 'basis':'错误引用',
            'citations':[{'publication_id':doc.id,'section_id':'s1','quote':'原文不存在的内容'}]})]
        with self.assertRaises(ValueError):
            ModelPatentTrial(client, lambda *args, **kwargs:[{'url':doc.url}], lambda url:doc)(self.skill,self.case,self.settings)

    def test_search_failure_is_not_a_low_score(self):
        client = Mock()
        client.complete.return_value = json.dumps({'queries':['a']})
        search = Mock(side_effect=RuntimeError('failed'))
        with self.assertRaises(RuntimeError): ModelPatentTrial(client, search)(self.skill, self.case, self.settings)

    def test_optimizer_has_a_data_only_interface(self):
        client = Mock()
        client.complete.return_value = json.dumps({'addition':'保留完整条件。','rationale':'改进限定核查。'})
        self.assertEqual(ModelSkillProposer(client)({'feedback':{'retrieval_score':50}})['addition'], '保留完整条件。')
        self.assertNotIn('tools', client.complete.call_args.kwargs)
