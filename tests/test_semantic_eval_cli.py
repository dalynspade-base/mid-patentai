"""CLI opt-in, frozen-output order and per-skill semantic runs with a simulated judge."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from modules.semantic_eval_examples import teaching_records,judgment_example
from tools.run_semantic_eval import main


class SemanticEvalCLITests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).parent/'.artifacts'
        base.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.records=self.folder/'observations.json'
        self.records.write_text(json.dumps(teaching_records(),ensure_ascii=False),encoding='utf-8')
        self.output=self.folder/'evaluation'

    def args(self,*more):
        return ['semantic-eval','--records',str(self.records),'--output-dir',str(self.output),*more]

    def test_default_only_previews_and_never_claims_model_execution(self):
        with patch('sys.argv',self.args()),patch('modules.llm_client.LLMClient.complete') as model,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(),0)
        model.assert_not_called()
        plan=json.loads((self.output/'semantic_eval_plan.json').read_text(encoding='utf-8'))
        self.assertEqual(plan['execution'],'not_executed')
        self.assertIsNone(plan['verdict'])
        self.assertFalse((self.output/'semantic_eval.json').exists())

    def test_missing_confirmation_stops_before_output_or_call(self):
        with patch('sys.argv',self.args('--run-model')),patch('modules.llm_client.LLMClient.complete') as model,contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):main()
        model.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_actual_outputs_are_frozen_before_opening_references(self):
        with patch('sys.argv',self.args('--references',str(self.folder/'missing-reference.json'))):
            with self.assertRaises(FileNotFoundError):main()
        frozen=json.loads((self.output/'frozen_skill_outputs.json').read_text(encoding='utf-8'))
        self.assertEqual(frozen,teaching_records())

    def test_one_skill_can_complete_its_own_evaluation(self):
        def response(system,user,**kwargs):
            return json.dumps(judgment_example(json.loads(user)['record']),ensure_ascii=False)
        with patch('sys.argv',self.args('--skill','patent-synonym-expansion','--run-model','--confirm-external')),\
             patch.dict('os.environ',{'LLM_API_KEY':'test-key','LLM_BASE_URL':'https://example.test','LLM_MODEL':'test-model'}),\
             patch('modules.llm_client.LLMClient.complete',side_effect=response) as model,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(),0)
        model.assert_called_once()
        report=json.loads((self.output/'semantic_eval.json').read_text(encoding='utf-8'))
        self.assertEqual(report['execution'],'completed')
        self.assertEqual(report['scope'],['patent-synonym-expansion'])
        self.assertEqual(report['verdict'],'acceptable')


if __name__=='__main__':unittest.main()
