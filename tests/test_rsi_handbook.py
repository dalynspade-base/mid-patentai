"""Dual-material grading checks using explicit synthetic reference data and model mocks."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from modules.novelty_models import PatentDocument, SourceSection
from modules.rsi_datasets import handbook_dataset, pr8_dataset, extract_claim_texts
from modules.rsi_experiment import run_experiment, score_predictions, validate_gold
from modules.rsi_manual_scoring import CRITERIA, digest, score_manual
from modules.rsi_rubric_review import ModelRubricAssessor
from modules.rsi_trial import ModelPatentTrial


def fixture():
    docs = []
    snapshots = []
    outputs = []
    for i, (pid, relevance, labels) in enumerate([
        ('US123A','R3',('high','partial')), ('US456A','R1',('context','context'))], 1):
        docs.append({'id':f'D{i:02}', 'publication_id':pid, 'publication_date':'2020-01-01',
            'relevance':relevance, 'weight':int(relevance[1]), 'in_recall_denominator':True,
            'title':'合成对照'+str(i), 'claim_1':labels[0], 'claim_2':labels[1],
            'overlap':'控制器连接泵', 'gap':'未见逐参数双向试探', 'evidence_location':'权1',
            'grading_observation':'分别核对装置和方法'})
        doc = PatentDocument(pid,'合成对照'+str(i),'https://patents.google.com/patent/'+pid,'2020-01-01',
            (SourceSection('claim-1','控制器连接泵，反馈电路调节泵。'),), complete=True)
        snapshots.append(doc)
        citation = {'publication_id':pid, 'section_id':'claim-1', 'quote':'控制器连接泵'}
        outputs.append({'publication_id':pid, 'overlap':'控制器连接泵', 'gap':'未见双向试探',
                        'citations':[citation], 'claim_labels':{'1':labels[0],'2':labels[1]},
                        'claim_citations':{'1':[citation],'2':[citation]}})
    standard = {'version':'1.2','dataset_id':'handbook-synthetic','assessment_only':True,'cutoff':'2024-01-01',
        'target':{'claims':[1,2]},'counts':{'public_relevant':2,'weight_sum':4,'by_relevance':{'R3':1,'R1':1}},
        'labels':{'high':'H','partial':'P','context':'C','insufficient_evidence':'U'},
        'gold_claims':{'1':{'result':'high','document':'US123A'},'2':{'result':'no_single_complete_match_in_dataset'}},
        'documents':docs, 'scoring':{'weights':{'retrieval':35,'document_evidence':45,'claim_conclusion':20},
            'retrieval':{'weighted_recall_at':25,'weighted_recall_fraction':.65,'ndcg_at':10,'ndcg_fraction':.35,'ndcg_gain':'2^relevance_grade-1'},
            'document_evidence':{'max_per_document':4,'criteria':[*CRITERIA,'correct_claim_labels'],
                                 'risk_labels':{'points_per_claim':.5,'require_matching_evidence':True}}}}
    inputs,gold = handbook_dataset(standard,'模拟装置与方法。',{'1':'一种控制装置。','2':'一种逐参数试探方法。'})
    inputs['provenance'] = 'teaching_demo'
    prediction = {'case_id':inputs['cases'][0]['case_id'],'execution':'completed','risk_label':'insufficient_evidence',
        'retrieved':[{'publication_id':d.id,'title':d.title,'url':d.url,'publication_date':d.publication_date,'snapshot_hash':d.snapshot_hash} for d in snapshots],
        'source_snapshots':[d.to_dict() for d in snapshots],'handbook_documents':outputs,
        'claim_conclusions':[{'target_id':str(c),'result':standard['gold_claims'][str(c)]['result'],
            'basis':'结合当前原文核对的结论','key_gaps':['未见双向试探'] if c==2 else [],
            'citations':[outputs[0]['citations'][0]]} for c in (1,2)]}
    return inputs,gold,prediction


def review_for(inputs,gold,prediction,method='human_rubric_review'):
    def flag(): return {'passed':True,'basis':'合成测试预设的独立复核，不是真实专家验收。'}
    expected = {d['publication_id'] for d in gold['cases'][0]['assessment']['standard']['documents']}
    return {'method':method,'reviewer':'synthetic-test-fixture','prediction_hash':digest(prediction),
        'standard_hash':digest(gold['cases'][0]['assessment']['standard']),'case_hash':digest(inputs['cases'][0]),
        'documents':[{'publication_id':pid,'criteria':{k:flag() for k in CRITERIA},
                      'claim_evidence':{c:flag() for c in ('1','2')}} for pid in dict.fromkeys(d['publication_id'] for d in prediction['retrieved']) if pid in expected],
        'claims':[{'target_id':c,**flag()} for c in ('1','2')]}


class HandbookScoringTests(unittest.TestCase):
    def setUp(self): self.inputs,self.gold,self.prediction = fixture()

    def score(self,review=True):
        reviews = {self.inputs['cases'][0]['case_id']:review_for(self.inputs,self.gold,self.prediction)} if review else None
        return score_predictions(self.inputs,self.gold,[self.prediction],(.5,.5),reviews=reviews)

    def test_perfect_outputs_use_original_35_45_20(self):
        result = self.score()
        self.assertEqual(result['components'],{'retrieval':35,'document_evidence':45,'claim_conclusion':20})
        self.assertEqual(result['total_score'],100)
        self.assertTrue(result['adoption_eligible'])

    def test_no_review_does_not_invent_semantic_points(self):
        result = self.score(False)
        self.assertEqual(result['components']['retrieval'],35)
        self.assertIsNone(result['total_score'])
        self.assertFalse(result['adoption_eligible'])

    def test_executor_self_reported_review_and_scores_are_ignored(self):
        self.prediction['rubric_review'] = review_for(self.inputs,self.gold,self.prediction)
        self.prediction['score'] = 100
        self.assertIsNone(self.score(False)['total_score'])

    def test_stale_prediction_review_rejected(self):
        review = review_for(self.inputs,self.gold,self.prediction)
        self.prediction['handbook_documents'][0]['gap'] = '改动'
        with self.assertRaises(ValueError):
            score_manual(self.inputs['cases'][0],self.gold['cases'][0],self.prediction,review)

    def test_stale_standard_review_rejected(self):
        review = review_for(self.inputs,self.gold,self.prediction)
        review['standard_hash'] = 'stale'
        with self.assertRaises(ValueError): score_manual(self.inputs['cases'][0],self.gold['cases'][0],self.prediction,review)

    def test_false_high_keeps_retrieval_credit_but_loses_claim_and_conclusion_points(self):
        self.prediction['handbook_documents'][0]['claim_labels']['2'] = 'high'
        result = self.score()
        self.assertEqual(result['components']['retrieval'],35)
        self.assertLess(result['components']['document_evidence'],45)
        self.assertEqual(result['components']['claim_conclusion'],10)
        self.assertEqual(result['cases'][0]['errors']['false_high'],1)

    def test_missing_retrieval_is_zero_document_evidence_against_complete_denominator(self):
        self.prediction['retrieved'].pop()
        self.prediction['handbook_documents'].pop()
        result = self.score()
        self.assertAlmostEqual(result['components']['document_evidence'],45*3/4)
        self.assertEqual(result['cases'][0]['retrieval_metrics']['weighted_recall_at_25'],.75)
        self.assertEqual(result['cases'][0]['errors']['retrieval_misses'],1)

    def test_wrong_rank_reduces_ndcg(self):
        self.prediction['retrieved'].reverse()
        result = self.score()
        self.assertEqual(result['cases'][0]['retrieval_metrics']['weighted_recall_at_25'],1)
        self.assertLess(result['components']['retrieval'],35)

    def test_duplicate_retrieval_does_not_inflate_ranking_or_points(self):
        self.prediction['retrieved'].insert(1,deepcopy(self.prediction['retrieved'][0]))
        self.assertEqual(self.score()['total_score'],100)

    def test_forged_citation_loses_locator_and_label_points_and_blocks_adoption(self):
        self.prediction['handbook_documents'][0]['claim_citations']['1'][0]['quote'] = '伪造'
        result = self.score()
        self.assertLess(result['components']['document_evidence'],45)
        self.assertFalse(result['adoption_eligible'])

    def test_label_without_matching_semantic_evidence_does_not_get_half_point(self):
        review = review_for(self.inputs,self.gold,self.prediction)
        review['documents'][0]['claim_evidence']['1']['passed'] = False
        result = score_manual(self.inputs['cases'][0],self.gold['cases'][0],self.prediction,review)
        self.assertAlmostEqual(result['components']['document_evidence'],45-11.25*.5*3/4)

    def test_numeric_rubric_points_cannot_substitute_for_boolean_decisions(self):
        review = review_for(self.inputs,self.gold,self.prediction)
        review['documents'][0]['criteria']['specific_overlap']['passed'] = 1
        with self.assertRaises(ValueError): score_manual(self.inputs['cases'][0],self.gold['cases'][0],self.prediction,review)

    def test_pending_new_document_has_no_invented_gold_label(self):
        extra = deepcopy(self.prediction['retrieved'][0]);extra['publication_id']='US999A'
        self.prediction['retrieved'].append(extra)
        row = self.score()['cases'][0]
        self.assertEqual(row['pending_reference_documents'],['US999A'])
        self.assertFalse(row['adoption_eligible'])

    def test_missing_claim_label_is_unjudged_not_false_high(self):
        self.prediction['handbook_documents'][0]['claim_labels'].pop('2')
        row = self.score()['cases'][0]
        self.assertEqual(row['errors']['unjudged'],1)
        self.assertEqual(row['errors']['false_high'],0)
        self.assertFalse(row['adoption_eligible'])

    def test_false_high_denominator_uses_actual_assessed_non_high_pairs(self):
        self.prediction['retrieved'].pop(); self.prediction['handbook_documents'].pop()
        rate=self.score()['cases'][0]['false_high_rate']
        self.assertEqual(rate['denominator'],1)
        self.assertEqual(rate['full_reference_denominator'],3)

    def test_private_standard_never_appears_in_public_input(self):
        text = json.dumps(self.inputs)
        for key in ['gold_claims','grading_observation','evidence_location','weight_sum','source_publication_id']:
            self.assertNotIn(key,text)

    def test_different_profiles_cannot_be_averaged(self):
        from tests.test_rsi_evidence import fixture as evidence_fixture
        inputs,gold,prediction,_=evidence_fixture()
        self.inputs['cases'].extend(inputs['cases']);self.gold['cases'].extend(gold['cases'])
        with self.assertRaises(ValueError): validate_gold(self.inputs,self.gold)


class DatasetAdapterTests(unittest.TestCase):
    def test_pr8_only_prepares_train_and_keeps_anchor_out(self):
        root=Path(__file__).resolve().parents[1]/'evals/testsets'
        inputs,gold,provenance=pr8_dataset(root)
        self.assertEqual(provenance['anchor_documents_archived_only'],10)
        self.assertEqual(provenance['train_documents'],16)
        self.assertEqual(len(gold['cases'][0]['targets']),5)
        self.assertNotIn('usage',json.dumps(inputs))
        ids={r['document_id'] for r in gold['cases'][0]['assessment']['references']}
        self.assertIn('CN108559503A',ids)
        self.assertNotIn('CN107325812A',ids)

    def test_complete_claims_can_be_extracted_without_original_markdown(self):
        text='摘要\n权利要求书\n2\n1. 一种装置，保留完整限定。\n权利要求书\n3\n2. 一种方法，保留停止条件。\n说明书\n4\n说明正文'
        claims=extract_claim_texts(text)
        self.assertIn('停止条件',claims['2'])
        self.assertNotIn('说明正文',claims['2'])
        self.assertNotIn('权利要求书',claims['1'])

    def test_ambiguous_claim_extraction_rejected(self):
        with self.assertRaises(ValueError): extract_claim_texts('只有标题没有权项')


class HandbookExecutionTests(unittest.TestCase):
    def test_executor_uses_live_search_and_current_skill_without_standard_answers(self):
        inputs,gold,prediction=fixture()
        docs=[PatentDocument.from_dict(d) for d in prediction['source_snapshots']]
        with tempfile.TemporaryDirectory() as tmp:
            skill=Path(tmp)/'SKILL.md';skill.write_text('本轮实际临时指令',encoding='utf-8')
            client=Mock();client.complete.side_effect=[json.dumps({'queries':['控制器 泵']}),
                *[json.dumps(d,ensure_ascii=False) for d in prediction['handbook_documents']],
                *[json.dumps(c,ensure_ascii=False) for c in prediction['claim_conclusions']]]
            trial=ModelPatentTrial(client,lambda *a,**k:[{'url':d.url} for d in docs],Mock(side_effect=docs))
            result=trial(skill,inputs['cases'][0],{'risk_levels':inputs['risk_levels'],'abstain_label':'insufficient_evidence',
                        'budget':{'max_queries':4,'max_documents':25}})
            self.assertEqual(len(result['handbook_documents']),2)
            for call in client.complete.call_args_list:
                self.assertIn('本轮实际临时指令',call.args[0])
                for key in ['gold_claims','grading_observation','evidence_location','weight_sum']:
                    self.assertNotIn(key,call.args[1])

    def test_independent_assessor_records_raw_calls_and_binds_output(self):
        inputs,gold,prediction=fixture();predefined=review_for(inputs,gold,prediction)
        client=Mock();client.model='synthetic';client.url='https://example.invalid/chat/completions'
        client.complete.side_effect=[json.dumps({k:d[k] for k in ('criteria','claim_evidence')}) for d in predefined['documents']]+[
            json.dumps({k:c[k] for k in ('passed','basis')}) for c in predefined['claims']]
        with tempfile.TemporaryDirectory() as tmp:
            review=ModelRubricAssessor(client)(inputs['cases'][0],gold['cases'][0],prediction,Path(tmp))
            self.assertEqual(len(list(Path(tmp).glob('rubric-review/*.raw.txt'))),4)
            self.assertEqual(review['prediction_hash'],digest(prediction))
            score=score_manual(inputs['cases'][0],gold['cases'][0],prediction,review)
            self.assertEqual(score['total_score'],100)

    def test_semantic_component_regression_cannot_be_hidden_by_higher_total(self):
        inputs,gold,prediction=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);formal=root/'formal/patent-precheck/SKILL.md';formal.parent.mkdir(parents=True)
            formal.write_text('---\nname: test\n---\n固定指令',encoding='utf-8')
            calls=[]
            def predict(skill,case,settings):
                result=deepcopy(prediction)
                result['candidate']='本轮改进' in skill.read_text(encoding='utf-8')
                if not result['candidate']:
                    result['claim_conclusions'][0]['result']='insufficient_evidence'
                return result
            def assess(case,reference,output,folder):
                calls.append(output['candidate'])
                review=review_for(inputs,gold,output)
                if output['candidate']:review['documents'][0]['criteria']['specific_overlap']['passed']=False
                return review
            result=run_experiment(inputs,gold,root/'run',predict,
                lambda _: {'addition':'本轮改进：核对条件。','rationale':'演示'},formal_root=formal.parents[1],
                rounds=1,mode='teaching_demo',assessor=assess)
            row=result['rounds'][0]
            self.assertGreater(row['candidate_score']['total_score'],row['control_score']['total_score'])
            self.assertFalse(row['accepted'])
            self.assertEqual(len(calls),3)
            self.assertTrue((root/'run/baseline/trial/rubric_reviews.json').exists())


if __name__=='__main__':unittest.main()
