import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from modules.candidate_relevance import fetch_additional_documents
from modules.harness import NoveltyHarness
from modules.novelty_models import PATSNAP_SOURCE_SCOPE, patsnap_view_url
from modules.patent_network_state import PatentAccessPaused
from modules.patsnap_mcp import PatsnapMCP
from tests import test_app as app_helpers


ID = "19ea06ca-04d8-41a1-a1e4-1b2284ae57a2"
OTHER_ID = "aaaaaaaa-04d8-41a1-a1e4-1b2284ae57a2"
PN = "CN123456789A"
OTHER_PN = "CN123456780A"
CLAIM = "1. 一种装置，具有连接控制器的压力传感器。\n\n2. 根据权利要求1所述的装置。"
DESCRIPTION = "技术领域\n\n本装置通过压力传感器获取信号。\n控制器将信号发送给终端。"


def result(pn, patent_id):
    return {"patent_id": patent_id, "pn": pn, "title": "一种测压装置"}


def fixture():
    return {
        "search_patents": {"status": True, "error_code": 0, "data": {"result_count": 2,
            "total_search_result_count": 400, "results": [result(PN, ID), result(OTHER_PN, OTHER_ID)]}},
        "get_bibliography": {"status": True, "error_code": 0, "data": [{**result(PN, ID),
            "bibliographic_data": {"publication_reference": {"date": 20240315},
                "application_reference": {"date": 20231201}, "invention_title": [{"lang": "CN", "text": "一种测压装置"}]}}]},
        "get_claims": {"claim_text": CLAIM, "claim_length": len(CLAIM), "source": "zhihuiya_p018"},
        "get_description": {"description_text": DESCRIPTION, "description_length": len(DESCRIPTION), "source": "zhihuiya_p019"},
    }


def mock_client(responses=None):
    client = PatsnapMCP()
    data = fixture() if responses is None else responses
    client.call = Mock(side_effect=lambda tool, _: data[tool])
    return client


class PatsnapTests(unittest.TestCase):
    def test_provider_runs_full_path_with_separate_sources_and_budgets(self):
        client = mock_client()
        with patch("modules.harness.PatsnapMCP", return_value=client):
            state = NoveltyHarness(max_documents=1, results_per_query=25).run(
                feature_text="压力传感器", query="TACD:(压力传感器)", cutoff="2026-11-09")
        self.assertEqual([call.args[0] for call in client.call.call_args_list],
                         ["search_patents", "get_bibliography", "get_claims", "get_description"])
        self.assertEqual(client.call.call_args_list[0].args[1], {"query": "TACD:(压力传感器)", "limit": 25, "offset": 0})
        self.assertEqual(client.call.call_args_list[2].args[1], {"patent_number": PN, "confirm_paid": True})
        self.assertEqual(client.call.call_args_list[3].args[1], {"patent_number": PN, "force": False})
        audit = state["search_audit"]
        self.assertEqual(audit["provider"], "patsnap")
        self.assertEqual(audit["queries"][0]["total_matches"], 400)
        self.assertEqual(audit["queries"][0]["returned_count"], 2)
        self.assertEqual([c["fetch_status"] for c in audit["candidates"]], ["fetched", "not_selected"])
        doc = state["documents"][0]
        self.assertEqual((doc.id, doc.publication_date, doc.filing_date), (PN, "2024-03-15", "2023-12-01"))
        self.assertEqual(doc.source_kind, "patsnap_mcp_snapshot")
        self.assertEqual(doc.sections[0].text, CLAIM)
        self.assertIn("P019", doc.sections[-1].source_location)
        self.assertTrue(state["hits"])
        self.assertEqual(state["patent_context"]["scope"]["sources"], PATSNAP_SOURCE_SCOPE)
        self.assertIn("检索平台：智慧芽", state["markdown"])
        self.assertIn("平台命中总数：400", state["markdown"])
        self.assertEqual(state["decision"].label, "insufficient_evidence")

    def test_wrong_publication_id_stops_before_paid_body_calls(self):
        data = fixture()
        data["get_bibliography"]["data"][0]["pn"] = OTHER_PN
        client = mock_client(data)
        with self.assertRaisesRegex(ValueError, "不匹配"):
            client.fetch({"url": patsnap_view_url(ID), "publication_id": PN})
        client.call.assert_called_once_with("get_bibliography", {"patent_number": PN})

    def test_quota_pause_preserves_plan_without_sending_next_query(self):
        client = PatsnapMCP()
        client._connected = True
        client._post = Mock(return_value={"structuredContent": {"status": False, "error_code": 67200005,
                            "message": "secret-like server details"}})
        state = NoveltyHarness(search_provider="patsnap", searcher=client.search).run(
            feature_text="压力传感器\n控制器", cutoff="2026-11-09")
        client._post.assert_called_once()
        records = state["search_audit"]["queries"]
        self.assertEqual(records[0]["sent_query"], 'TACD:("压力传感器")')
        self.assertIn("67200005", records[0]["error"])
        self.assertNotIn("secret-like", state["markdown"])
        self.assertTrue(all(r["status"] == "not_executed" for r in records[1:]))

    def test_paused_supplement_does_not_consume_remaining_attempts(self):
        candidates = [{"publication_id": pn, "url": patsnap_view_url(pid)}
                      for pn, pid in [(PN, ID), (OTHER_PN, OTHER_ID)]]
        fetch = Mock(side_effect=PatentAccessPaused("达到平台额度限制"))
        docs, audit = fetch_additional_documents([], [], {"provider": "patsnap", "candidates": candidates},
                                                 [PN, OTHER_PN], fetch)
        fetch.assert_called_once()
        self.assertFalse(docs)
        self.assertEqual(audit["retrieval_cost"]["additional_page_attempts"], 1)
        self.assertEqual(audit["candidates"][1]["fetch_status"], "not_executed")

    def test_transport_honors_mcp_session_and_never_follows_redirects(self):
        with TemporaryDirectory() as temp:
            cfg = Path(temp) / "mcp.json"
            cfg.write_text(json.dumps({"mcpServers": {"patent-platform": {
                "url": "https://patent.jiuan.com/mcp", "headers": {"Authorization": "Bearer example-placeholder"}
            }}}), encoding="utf-8")
            client = PatsnapMCP(cfg)

            def response(_url, *, json, headers, allow_redirects, **kwargs):
                self.assertFalse(allow_redirects)
                self.assertEqual(headers["Authorization"], "Bearer example-placeholder")
                method = json["method"]
                resp = Mock(status_code=200, headers={"Content-Type": "text/event-stream", "Mcp-Session-Id": "session"})
                resp.__enter__ = Mock(return_value=resp)
                resp.__exit__ = Mock(return_value=False)
                if method == "initialize":
                    resp.iter_lines.return_value = [b'data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26"}}', b""]
                elif method == "notifications/initialized":
                    resp.status_code = 202
                else:
                    self.assertEqual(headers["Mcp-Session-Id"], "session")
                    self.assertEqual(headers["MCP-Protocol-Version"], "2025-03-26")
                    resp.headers["Content-Type"] = "application/json"
                    resp.json.return_value = {"jsonrpc": "2.0", "id": 3, "result": {
                        "structuredContent": fixture()["search_patents"]}}
                return resp

            with patch("modules.patsnap_mcp.requests.post", side_effect=response) as post:
                self.assertEqual(client.search("TACD:(压力传感器)", 2)["total_matches"], 400)
                self.assertEqual(post.call_count, 3)
                original = cfg.read_text(encoding="utf-8")
                cfg.write_text(original.replace("patent.jiuan.com", "redirect.invalid"), encoding="utf-8")
                with self.assertRaises(PatentAccessPaused):
                    PatsnapMCP(cfg).search("TACD:(压力传感器)", 2)
                self.assertEqual(post.call_count, 3)
                cfg.write_text(original, encoding="utf-8")
                redirect = Mock(status_code=302, headers={"Location": "https://redirect.invalid"})
                redirect.__enter__ = Mock(return_value=redirect)
                redirect.__exit__ = Mock(return_value=False)
                post.side_effect = None
                post.return_value = redirect
                with self.assertRaisesRegex(PatentAccessPaused, "HTTP 302"):
                    PatsnapMCP(cfg).search("TACD:(压力传感器)", 2)
                self.assertEqual(post.call_count, 4)
                self.assertFalse(post.call_args.kwargs["allow_redirects"])


class PatsnapUITest(unittest.TestCase):
    def test_patsnap_is_the_only_online_provider_and_requires_opt_in(self):
        labeled = app_helpers.labeled
        client = mock_client()
        with patch("modules.harness.PatsnapMCP", return_value=client):
            app = app_helpers.AppFlowTests.app(self)
            self.assertFalse(any(widget.label == "检索平台" for widget in app.sidebar.selectbox))
            labeled(app.text_area, "确认待判断的技术要素（每行一个）").set_value("压力传感器")
            app.run(timeout=60)
            submit = "1 · 检索 智慧芽 并定位证据"
            consent = "我确认将检索词发送到智慧芽，并使用检索和正文接口额度"
            self.assertTrue(labeled(app.button, submit).disabled)
            client.call.assert_not_called()
            labeled(app.sidebar.checkbox, consent).set_value(True)
            labeled(app.sidebar.slider, "最多获取文献数").set_value(1)
            labeled(app.text_input, "智慧芽 检索词（可选）").set_value("TACD:(压力传感器)")
            app.run(timeout=60)
            labeled(app.button, submit).click().run(timeout=60)
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["novelty_state"]["search_audit"]["provider"], "patsnap")
            labeled(app.sidebar.checkbox, consent).set_value(False).run(timeout=60)
            self.assertTrue(labeled(app.button, "补充所选文献正文").disabled)


if __name__ == "__main__":
    unittest.main()
