"""在线检索注入固定返回，验证模型候选判断、补正文与已有正文复核的保留。"""
import json
import unittest
from datetime import date
from unittest.mock import Mock, patch

from modules.google_patent_documents import manual_google_document
from tests import test_app as helpers
from tests.test_candidate_relevance import metadata_response
from tests.test_model_review import review_response


class CandidateUITests(unittest.TestCase):
    app = helpers.AppFlowTests.app

    def setUp(self):
        self.docs = {key: manual_google_document(f"https://patents.google.com/patent/{key}/en", "模拟来源", "2023-01-01",
            "装置包括压力传感器。", complete=True) for key in ("US123", "US124", "US125")}
        def search(query, limit):
            if query == "压力传感器":
                return [{"url": self.docs["US123"].url, "title": "压力传感器"},
                        {"url": self.docs["US125"].url, "title": "压力传感器术语出现在引用目录"}]
            return [{"url": self.docs["US124"].url, "title": "压力感测器"}]
        self.search = Mock(side_effect=search)
        self.fetch = Mock(side_effect=lambda url: self.docs[url.split("/patent/")[1].split("/")[0]])
        self.patches = [patch("modules.harness.search_patents", self.search),
            patch("modules.harness.fetch_google_document", self.fetch),
            patch("modules.candidate_ui.fetch_google_document", self.fetch)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def searched(self):
        app = self.app()
        helpers.labeled(app.text_area, "确认待判断的技术要素（每行一个）").set_value("压力传感器")
        helpers.labeled(app.sidebar.slider, "最多获取文献数").set_value(1)
        helpers.labeled(app.sidebar.text_input, "模型 API Key").set_value("test-key")
        app.date_input[0].set_value(date(2024, 1, 1))
        app.run(timeout=60)
        helpers.labeled(app.button, "1 · 检索 Google Patents 并定位证据").click().run(timeout=60)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual([d.id for d in app.session_state["novelty_state"]["documents"]], ["US123"])
        return app

    def supplement(self, app):
        helpers.labeled(app.multiselect, "选择要补充正文的候选").set_value(["US124"]).run(timeout=60)
        helpers.labeled(app.button, "补充所选文献正文").click().run(timeout=60)
        self.assertEqual(len(app.exception), 0)

    def body_review(self, app, key):
        helpers.labeled(app.multiselect, "待逐项复核的文献").set_value([key]).run(timeout=60)
        helpers.labeled(app.checkbox, "我确认将上述要素、原文和候选对照发送到此模型服务").set_value(True).run(timeout=60)
        helpers.labeled(app.button, "调用大模型逐项复核并预筛").click().run(timeout=60)
        self.assertEqual(len(app.exception), 0)

    def test_metadata_judgment_needs_consent_then_supplement_does_not_auto_review_body(self):
        with patch("modules.llm_client.LLMClient.complete") as model:
            app = self.searched()
            self.assertTrue(helpers.labeled(app.button, "模型判断候选相关性并重新排序").disabled)
            model.assert_not_called()
            helpers.labeled(app.multiselect, "发送给模型判断相关性的候选").set_value(["US124"]).run(timeout=60)
            candidate = next(c for c in app.session_state["novelty_state"]["search_audit"]["candidates"] if c["publication_id"] == "US124")
            response = metadata_response("US124", quote="压力感测器")
            response["feature_checks"][0]["context_id"] = candidate["observations"][0]["id"]
            model.return_value = json.dumps({"candidates": [response]})
            helpers.labeled(app.checkbox, "我确认将技术要素和选定候选的标题摘要发送到模型判断相关性").set_value(True).run(timeout=60)
            query_count = self.search.call_count
            helpers.labeled(app.button, "模型判断候选相关性并重新排序").click().run(timeout=60)
            self.assertEqual(len(app.exception), 0)
            state = app.session_state["novelty_state"]
            self.assertEqual(state["search_audit"]["candidates"][0]["publication_id"], "US124")
            self.assertEqual(state["retrieval_increment"]["new_metadata_related_ids"], ["US124"])
            self.assertEqual(state["retrieval_increment"]["observed_new_body_related_ids"], [])
            self.supplement(app)
            state = app.session_state["novelty_state"]
            self.assertEqual({d.id for d in state["documents"]}, {"US123", "US124"})
            self.assertEqual(state.get("reviews", []), [])
            self.assertEqual(model.call_count, 1)
            self.assertEqual(self.search.call_count, query_count)

    def test_new_body_review_merges_with_existing_valid_risk_instead_of_discarding_it(self):
        with patch("modules.llm_client.LLMClient.complete", return_value=json.dumps(review_response())) as model:
            app = self.searched()
            self.body_review(app, "US123")
            first = app.session_state["novelty_state"]["reviews"][0]
            self.assertEqual(app.session_state["novelty_state"]["decision"].label, "potential_novelty_risk")
            self.supplement(app)
            self.assertEqual(app.session_state["novelty_state"]["reviews"], [first])
            self.assertEqual(app.session_state["novelty_state"]["decision"].label, "potential_novelty_risk")
            model.return_value = json.dumps(review_response("US124", status="unknown"))
            self.body_review(app, "US124")
            state = app.session_state["novelty_state"]
            self.assertEqual({r.document_id for r in state["reviews"]}, {"US123", "US124"})
            self.assertEqual(next(r for r in state["reviews"] if r.document_id == "US123").reviewed_at, first.reviewed_at)
            self.assertEqual(state["decision"].document_ids, ["US123"])
            self.assertEqual(state["retrieval_increment"]["attributable_new_body_related_ids"], ["US124"])

    def test_failed_metadata_call_falls_back_to_rules_without_losing_sources(self):
        with patch("modules.llm_client.LLMClient.complete", side_effect=RuntimeError("模拟失败")):
            app = self.searched()
            helpers.labeled(app.multiselect, "发送给模型判断相关性的候选").set_value(["US124"]).run(timeout=60)
            helpers.labeled(app.checkbox, "我确认将技术要素和选定候选的标题摘要发送到模型判断相关性").set_value(True).run(timeout=60)
            helpers.labeled(app.button, "模型判断候选相关性并重新排序").click().run(timeout=60)
            self.assertEqual(len(app.exception), 0)
            state = app.session_state["novelty_state"]
            self.assertEqual(state["search_audit"]["ranking"]["mode"], "rules")
            self.assertEqual(state["search_audit"]["ranking"]["status"], "error")
            self.assertEqual([d.id for d in state["documents"]], ["US123"])
            self.assertTrue(any("候选模型判断失败" in value for value in state["decision"].warnings))


if __name__ == "__main__":
    unittest.main()
