"""Large real-source-shaped inputs must be sent intact, not silently shortened."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock

from modules.rsi_limits import MAX_INPUT_CHARACTERS, check_input_size
from modules.rsi_trial import ModelPatentTrial
from modules.rsi_rubric_review import ModelRubricAssessor
from modules.novelty_models import PatentDocument,SourceSection
from tests.test_rsi_evidence import fixture as evidence_fixture
from tests.test_rsi_handbook import fixture as handbook_fixture,review_for


class RSIInputLimitTests(unittest.TestCase):
    def test_no_local_unicode_character_ceiling(self):
        self.assertIsNone(MAX_INPUT_CHARACTERS)
        check_input_size('字'*1000000,'complete input')

    def test_large_evidence_document_is_delivered_intact(self):
        inputs,gold,prediction,doc=evidence_fixture()
        large=PatentDocument(doc.id,doc.title,doc.url,doc.publication_date,
            (SourceSection('claim-1','控制器连接泵。反馈电路调节泵。'+'原文'*80000),),complete=True)
        client=Mock();client.complete.side_effect=[json.dumps({'queries':['控制器 泵']}),
            *[json.dumps(j,ensure_ascii=False) for j in prediction['evidence_judgments']]]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'SKILL.md';path.write_text('完整核对。',encoding='utf-8')
            ModelPatentTrial(client,lambda *a,**k:[{'url':doc.url}],lambda _:large)(
                path,inputs['cases'][0],{'budget':{'max_queries':4,'max_documents':20},
                    'risk_levels':inputs['risk_levels'],'abstain_label':inputs['abstain_label']})
        delivered=client.complete.call_args_list[1].args[1]
        self.assertGreater(len(delivered),100000)
        self.assertEqual(json.loads(delivered)['document']['sections'][0]['text'],large.sections[0].text)

    def test_provider_context_failure_propagates_after_full_document_delivery(self):
        inputs,gold,prediction,doc=evidence_fixture()
        huge=PatentDocument(doc.id,doc.title,doc.url,doc.publication_date,
                            (SourceSection('claim-1','字'*250000),),complete=True)
        client=Mock();client.complete.side_effect=[json.dumps({'queries':['控制器']}),
                                                 RuntimeError('provider context window exceeded')]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'SKILL.md';path.write_text('完整核对。',encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError,'provider context window exceeded'):
                ModelPatentTrial(client,lambda *a,**k:[{'url':doc.url}],lambda _:huge)(
                    path,inputs['cases'][0],{'budget':{'max_queries':4,'max_documents':20},
                    'risk_levels':inputs['risk_levels'],'abstain_label':inputs['abstain_label']})
        self.assertEqual(client.complete.call_count,2)
        delivered=json.loads(client.complete.call_args.args[1])
        self.assertEqual(delivered['document']['sections'][0]['text'],huge.sections[0].text)

    def test_large_handbook_rubric_receives_intact_source(self):
        inputs,gold,prediction=handbook_fixture()
        prediction['source_snapshots'][0]['sections'][0]['text']+='正文'*80000
        decisions=review_for(inputs,gold,prediction)
        client=Mock();client.model='test';client.url='https://example.invalid/chat/completions'
        client.complete.side_effect=[json.dumps({k:d[k] for k in ('criteria','claim_evidence')}) for d in decisions['documents']]+[
            json.dumps({k:c[k] for k in ('passed','basis')}) for c in decisions['claims']]
        with tempfile.TemporaryDirectory() as tmp:
            ModelRubricAssessor(client)(inputs['cases'][0],gold['cases'][0],prediction,Path(tmp))
        packet=client.complete.call_args_list[0].args[1]
        self.assertGreater(len(packet),100000)
        self.assertEqual(json.loads(packet)['actual_source']['sections'][0]['text'],prediction['source_snapshots'][0]['sections'][0]['text'])


if __name__=='__main__':unittest.main()
