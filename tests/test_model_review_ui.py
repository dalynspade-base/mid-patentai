"""真实 Streamlit 会话验证：模型复核替代人工逐格编辑，API 使用模拟响应。"""
import json
import unittest
from datetime import date
from unittest.mock import patch

from modules.google_patent_documents import manual_google_document
from tests import test_app as app_helpers
from tests.test_model_review import review_response


class ModelReviewUITests(unittest.TestCase):
    app = app_helpers.AppFlowTests.app
    manual_snapshot = app_helpers.AppFlowTests.manual_snapshot

    def ready(self, app=None):
        app = self.manual_snapshot(app or self.app(), "该装置包括压力传感器。", complete=True)
        app_helpers.labeled(app.sidebar.text_input, "模型 API Key").set_value("test-key").run(timeout=60)
        return app

    def submit(self, app):
        app_helpers.labeled(app.checkbox, "我确认将上述要素、原文和候选对照发送到此模型服务").set_value(True).run(timeout=60)
        app_helpers.labeled(app.button, "调用大模型逐项复核并预筛").click().run(timeout=60)
        self.assertEqual(len(app.exception), 0)
        return app

    def test_review_is_called_only_after_payload_confirmation(self):
        with patch("modules.llm_client.LLMClient.complete", return_value=json.dumps(review_response())) as model:
            app = self.ready()
            self.assertTrue(app_helpers.labeled(app.button, "调用大模型逐项复核并预筛").disabled)
            model.assert_not_called()
            self.submit(app)
            self.assertEqual(model.call_count, 1)
            state = app.session_state["novelty_state"]
            self.assertEqual(state["decision"].label, "potential_novelty_risk")
            self.assertEqual(state["reviews"][0].review_method, "model")
            self.assertFalse(state["suggestions"][0].reviewed)
            self.assertFalse(any("复核人姓名" in item.label for item in app.text_input))

    def test_rerender_retains_timestamp_and_model_change_invalidates_review(self):
        with patch("modules.llm_client.LLMClient.complete", return_value=json.dumps(review_response())) as model:
            app = self.submit(self.ready())
            timestamp = app.session_state["novelty_state"]["reviews"][0].reviewed_at
            app.run(timeout=60)
            self.assertEqual(app.session_state["novelty_state"]["reviews"][0].reviewed_at, timestamp)
            app_helpers.labeled(app.sidebar.text_input, "模型名").set_value("another-model").run(timeout=60)
            self.assertEqual(app.session_state["novelty_state"]["reviews"], [])
            self.assertEqual(app.session_state["last_generated_report"]["decision"]["label"], "insufficient_evidence")
            self.assertTrue(app_helpers.labeled(app.button, "调用大模型逐项复核并预筛").disabled)
            self.assertEqual(model.call_count, 1)

    def test_confirmed_payload_integrates_local_hints_and_all_original_sections(self):
        from modules.novelty_review import model_review_payload
        with patch("modules.llm_client.LLMClient.complete", return_value=json.dumps(review_response())) as model:
            app = self.ready()
            state = app.session_state["novelty_state"]
            preview = model_review_payload(state["features"], state["documents"][0], "2024-01-01",
                                           state["suggestions"][0], state["hits"],patent_context=state["patent_context"])
            self.submit(app)
            self.assertEqual(json.loads(model.call_args.args[1]), json.loads(json.dumps(preview)))
            self.assertEqual(preview["evidence_alignment"]["primary"], "model_semantics")
            self.assertTrue(preview["retrieval_hints"])
            self.assertEqual(model.call_count, 1)

    def test_old_prompt_review_requires_new_alignment_without_automatic_model_call(self):
        from dataclasses import replace
        from modules.novelty_models import stamp_review
        with patch("modules.llm_client.LLMClient.complete", return_value=json.dumps(review_response())) as model:
            app = self.submit(self.ready())
            state = app.session_state["novelty_state"]
            state["reviews"] = [stamp_review(replace(state["reviews"][0], review_prompt_version="novelty-review-v1"),
                state["features"], state["documents"][0], "2024-01-01")]
            app.run(timeout=60)
            self.assertEqual(app.session_state["novelty_state"]["reviews"], [])
            self.assertEqual(model.call_count, 1)

    def test_failed_retry_drops_old_review_and_does_not_export_stale_risk(self):
        with patch("modules.llm_client.LLMClient.complete", return_value=json.dumps(review_response())) as model:
            app = self.submit(self.ready())
            self.assertEqual(app.session_state["novelty_state"]["decision"].label, "potential_novelty_risk")
            model.side_effect = RuntimeError("模拟服务失败")
            app_helpers.labeled(app.button, "调用大模型逐项复核并预筛").click().run(timeout=60)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.session_state["novelty_state"]["reviews"], [])
            self.assertEqual(app.session_state["last_generated_report"]["decision"]["label"], "insufficient_evidence")
            self.assertTrue(any("模拟服务失败" in error.value for error in app.error))

    def test_document_selection_changes_only_the_next_request_and_retains_valid_evidence(self):
        with patch("modules.llm_client.LLMClient.complete", return_value=json.dumps(review_response())):
            app = self.submit(self.ready())
            app_helpers.labeled(app.multiselect, "待逐项复核的文献").set_value([]).run(timeout=60)
            self.assertEqual(len(app.session_state["novelty_state"]["reviews"]), 1)
            self.assertEqual(app.session_state["last_generated_report"]["decision"]["label"], "potential_novelty_risk")

    def test_invalid_quote_is_not_accepted_as_model_review(self):
        with patch("modules.llm_client.LLMClient.complete", return_value=json.dumps(review_response(quote="invented"))):
            app = self.submit(self.ready())
            self.assertEqual(app.session_state["novelty_state"]["reviews"], [])
            self.assertTrue(app.error)

    def test_online_journal_survives_manual_addition_and_model_review(self):
        doc = manual_google_document("https://patents.google.com/patent/US123/en", "fixture", "2023-01-01", "压力传感器")
        with patch("modules.harness.search_patents", return_value=[{"url": doc.url}]) as search, \
             patch("modules.harness.fetch_google_document", return_value=doc), \
             patch("modules.llm_client.LLMClient.complete", return_value=json.dumps(review_response())):
            app = self.app()
            app.date_input[0].set_value(date(2024, 1, 1))
            app_helpers.labeled(app.text_area, "发明描述").set_value("测试装置包含压力传感器。")
            app_helpers.labeled(app.text_area, "确认待判断的技术要素（每行一个）").set_value("压力传感器").run(timeout=60)
            app_helpers.labeled(app.button, "1 · 检索 Google Patents 并定位证据").click().run(timeout=60)
            journal = app.session_state["novelty_state"]["search_audit"]
            calls = search.call_count
            self.submit(self.ready(app))
            self.assertEqual(search.call_count, calls)
            self.assertEqual(app.session_state["novelty_state"]["search_audit"], journal)
            self.assertEqual(app.session_state["last_generated_report"]["search_audit"], journal)


if __name__ == "__main__":
    unittest.main()
