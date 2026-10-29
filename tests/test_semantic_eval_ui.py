"""Live Streamlit test with simulated judge, no real model or network."""
import json
import unittest
from unittest.mock import patch
from tests import test_app as helpers
from modules.semantic_eval_examples import judgment_example


class SemanticEvalUITests(unittest.TestCase):
    app=helpers.AppFlowTests.app
    manual_snapshot=helpers.AppFlowTests.manual_snapshot

    def configured(self):
        app=self.manual_snapshot(self.app())
        helpers.labeled(app.sidebar.text_input,"模型 API Key").set_value("test-key").run(timeout=60)
        return app

    def test_judge_requires_separate_confirmation_and_actual_outputs(self):
        packets=[]
        def response(system,user,**kwargs):
            packet=json.loads(user)
            packets.append(packet)
            return json.dumps(judgment_example(packet["record"]),ensure_ascii=False)
        with patch("modules.llm_client.LLMClient.complete",side_effect=response) as model:
            app=self.configured()
            self.assertTrue(helpers.labeled(app.button,"逐技能运行语义 eval").disabled)
            model.assert_not_called()
            helpers.labeled(app.checkbox,"我确认将上述技能输入、输出和原文发送给独立语义裁判").set_value(True).run(timeout=60)
            helpers.labeled(app.button,"逐技能运行语义 eval").click().run(timeout=60)
            self.assertEqual(len(app.exception),0)
            report=app.session_state["last_generated_report"]
            records={r["skill_id"]:r for r in report["skill_records"]}
            result=report["semantic_evaluation"]
            self.assertEqual(result["execution"],"incomplete")
            self.assertIsNone(result["verdict"])
            self.assertEqual({p["record"]["skill_id"] for p in packets},set(records)-{"patent-evidence-alignment"})
            for packet in packets:
                self.assertEqual(packet["record"],records[packet["record"]["skill_id"]])
            helpers.labeled(app.sidebar.text_input,"模型名").set_value("changed-judge").run(timeout=60)
            self.assertNotIn("semantic_evaluation",app.session_state["last_generated_report"])
            self.assertFalse(helpers.labeled(app.checkbox,"我确认将上述技能输入、输出和原文发送给独立语义裁判").value)

    def test_failed_retry_does_not_reuse_previous_accepted_findings(self):
        def response(system,user,**kwargs):
            return json.dumps(judgment_example(json.loads(user)["record"]),ensure_ascii=False)
        with patch("modules.llm_client.LLMClient.complete",side_effect=response) as model:
            app=self.configured()
            helpers.labeled(app.checkbox,"我确认将上述技能输入、输出和原文发送给独立语义裁判").set_value(True).run(timeout=60)
            helpers.labeled(app.button,"逐技能运行语义 eval").click().run(timeout=60)
            model.side_effect=RuntimeError("simulated failure")
            helpers.labeled(app.button,"逐技能运行语义 eval").click().run(timeout=60)
            self.assertEqual(len(app.exception),0)
            result=app.session_state["last_generated_report"]["semantic_evaluation"]
            self.assertTrue(all(r["verdict"] is None for r in result["results"]))
            self.assertTrue(all(not r["findings"] for r in result["results"]))


if __name__=="__main__":unittest.main()
