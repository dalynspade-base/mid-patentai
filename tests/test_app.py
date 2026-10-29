"""真实 Streamlit 运行测试：首屏、人工快照、外发确认与输入变更。"""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


def labeled(elements, label):
    return next(item for item in elements if item.label == label)


class AppFlowTests(unittest.TestCase):
    def app(self):
        return AppTest.from_file(str(Path(__file__).parents[1] / "app.py")).run(timeout=60)

    def manual_snapshot(self, app, body="该装置不包括压力传感器。", complete=False):
        labeled(app.text_area, "发明描述").set_value("测试装置包含压力传感器。")
        labeled(app.text_area, "确认待判断的技术要素（每行一个）").set_value("压力传感器")
        app.date_input[0].set_value(date(2024, 1, 1))
        labeled(app.text_input, "Google Patents 单篇链接").set_value("https://patents.google.com/patent/US123/en")
        labeled(app.text_input, "文献标题").set_value("教学测试快照")
        labeled(app.text_input, "公开日期 YYYY-MM-DD").set_value("2023-01-01")
        labeled(app.text_area, "原文（空行分段，不要只粘贴搜索摘要）").set_value(body)
        labeled(app.checkbox, "我确认已粘贴待比对的完整相关权利要求和说明书，保留了上下文限定").set_value(complete)
        app.run(timeout=60)  # 浏览器输入发生后先重绘，按钮才会从 disabled 变为可点击。
        labeled(app.button, "加入人工原文快照").click().run(timeout=60)
        return app

    def test_first_screen_has_no_network_or_paid_model_call(self):
        with patch("skills.precheck_skill.search_patents") as search, patch("modules.llm_client.LLMClient.complete") as model:
            app = self.app()
            self.assertEqual(len(app.exception), 0)
            search.assert_not_called()
            model.assert_not_called()
            self.assertTrue(labeled(app.button, "1 · 检索 Google Patents 并定位证据").disabled)

    def test_manual_evidence_does_not_auto_judge_or_send_model(self):
        with patch("skills.precheck_skill.search_patents") as search, patch("modules.llm_client.LLMClient.complete") as model:
            app = self.manual_snapshot(self.app())
            self.assertEqual(len(app.exception), 0)
            state = app.session_state["novelty_state"]
            self.assertEqual(state["decision"].label, "insufficient_evidence")
            self.assertEqual(state["suggestions"][0].rows[0].status, "unknown")
            self.assertTrue(labeled(app.button, "调用大模型逐项复核并预筛").disabled)
            search.assert_not_called()
            model.assert_not_called()

    def test_changed_features_invalidate_old_report(self):
        app = self.manual_snapshot(self.app())
        labeled(app.text_area, "确认待判断的技术要素（每行一个）").set_value("温度传感器").run(timeout=60)
        self.assertEqual(len(app.exception), 0)
        self.assertFalse(any(element.value == "预筛结果" for element in app.subheader))

    def test_offline_basic_eval_button_works_without_model(self):
        app = self.app()
        labeled(app.button, "检查语义 eval 契约（离线）").click().run(timeout=60)
        self.assertEqual(len(app.exception), 0)
        result=app.session_state["basic_eval_report"]
        self.assertEqual(result["execution"],"not_executed")
        self.assertIsNone(result["verdict"])
        self.assertTrue(all(row["status"]=="passed" for row in result["checks"]))



if __name__ == "__main__":
    unittest.main()
