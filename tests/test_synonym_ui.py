"""同义词候选外发、审核和状态失效的真实Streamlit运行回归。"""
import json
import unittest
from unittest.mock import patch

from tests import test_app as helpers
from tests.test_synonym_expansion import proposed
from modules.synonym_expansion import candidate_id


class SynonymUITests(unittest.TestCase):
    app = helpers.AppFlowTests.app
    manual_snapshot = helpers.AppFlowTests.manual_snapshot

    def configured(self):
        app = self.manual_snapshot(self.app())
        helpers.labeled(app.sidebar.text_input, "模型 API Key").set_value("test-key").run(timeout=60)
        return app

    def generate(self, app):
        helpers.labeled(app.checkbox, "我确认将技术要素、保护术语和词典发送到模型生成扩展候选").set_value(True).run(timeout=60)
        helpers.labeled(app.button, "生成中英文同义词候选").click().run(timeout=60)
        self.assertEqual(len(app.exception), 0)

    def test_generation_requires_its_own_confirmation_and_new_terms_need_review(self):
        value = proposed("压力传感器", "压力探测器")
        with patch("modules.llm_client.LLMClient.complete", return_value=json.dumps({"candidates": [value]})) as llm, \
             patch("modules.harness.search_patents", return_value=[]) as search:
            app = self.configured()
            self.assertTrue(helpers.labeled(app.button, "生成中英文同义词候选").disabled)
            llm.assert_not_called()
            self.generate(app)
            self.assertEqual(llm.call_count, 1)
            helpers.labeled(app.button, "1 · 检索 Google Patents 并定位证据").click().run(timeout=60)
            plan = app.session_state["novelty_state"]["search_audit"]["expansion"]
            self.assertEqual(next(c for c in plan["candidates"] if c["origin"] == "llm")["status"], "pending")
            self.assertFalse(any("压力探测器" in call.args[0] for call in search.call_args_list))
            helpers.labeled(app.multiselect, "审核后允许用于查询的新增术语").set_value([candidate_id(value)]).run(timeout=60)
            self.assertFalse(helpers.labeled(app.checkbox, "我已核对所选新增术语在当前要素中的技术含义和限定").value)
            helpers.labeled(app.checkbox, "我已核对所选新增术语在当前要素中的技术含义和限定").set_value(True).run(timeout=60)
            search.reset_mock()
            helpers.labeled(app.button, "1 · 检索 Google Patents 并定位证据").click().run(timeout=60)
            self.assertTrue(any("压力探测器" in call.args[0] for call in search.call_args_list))
            self.assertEqual(llm.call_count, 1)
            self.assertEqual(app.session_state["novelty_state"].get("reviews", []), [])

    def test_profile_change_invalidates_candidates_and_uses_theoretical_budget(self):
        with patch("modules.llm_client.LLMClient.complete", return_value='{"candidates":[]}') as llm, \
             patch("modules.harness.search_patents", return_value=[]):
            app = self.configured()
            self.generate(app)
            helpers.labeled(app.selectbox, "专利检索类型").set_value("theoretical").run(timeout=60)
            self.assertTrue(helpers.labeled(app.button, "生成中英文同义词候选").disabled)
            helpers.labeled(app.button, "1 · 检索 Google Patents 并定位证据").click().run(timeout=60)
            audit = app.session_state["novelty_state"]["search_audit"]
            self.assertEqual(audit["query_budget"], 8)
            self.assertEqual(audit["expansion"]["generation"]["status"], "not_requested")
            self.assertEqual(llm.call_count, 1)

    def test_example_eval_never_calls_network_or_model(self):
        with patch("modules.llm_client.LLMClient.complete") as llm, patch("modules.harness.search_patents") as search:
            app = self.app()
            helpers.labeled(app.button, "查看术语扩展语义 eval 规范（离线）").click().run(timeout=60)
            self.assertEqual(len(app.exception), 0)
            report = app.session_state["synonym-eval-result"]
            self.assertEqual(report["execution"], "not_executed")
            self.assertIsNone(report["verdict"])
            self.assertEqual(len(report["dimensions"]),6)
            llm.assert_not_called()
            search.assert_not_called()

    def test_failed_regeneration_does_not_reuse_previously_approved_alias(self):
        value = proposed("压力传感器", "压力探测器")
        with patch("modules.llm_client.LLMClient.complete", return_value=json.dumps({"candidates": [value]})) as llm, \
             patch("modules.harness.search_patents", return_value=[]) as search:
            app = self.configured()
            self.generate(app)
            helpers.labeled(app.multiselect, "审核后允许用于查询的新增术语").set_value([candidate_id(value)]).run(timeout=60)
            helpers.labeled(app.checkbox, "我已核对所选新增术语在当前要素中的技术含义和限定").set_value(True).run(timeout=60)
            llm.side_effect = RuntimeError("模拟生成失败")
            helpers.labeled(app.button, "生成中英文同义词候选").click().run(timeout=60)
            self.assertEqual(len(app.exception), 0)
            helpers.labeled(app.button, "1 · 检索 Google Patents 并定位证据").click().run(timeout=60)
            self.assertFalse(any("压力探测器" in call.args[0] for call in search.call_args_list))
            self.assertEqual(app.session_state["novelty_state"]["search_audit"]["expansion"]["generation"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
