"""Offline behavior checks, using teaching facts; no claim of model or legal accuracy."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from modules.claim_context import validate_history, claim_targets, resolve_target
from modules.patent_context import context_for, handoff_issues
from modules.semantic_eval import evaluation_packet, validate_judgment, evaluate_records
from modules.semantic_eval_examples import judgment_example
from modules.skill_contracts import digest, make_record
from modules.novelty_models import DocumentReview, EvidenceRow, PatentDocument, SourceSection, stamp_review
from modules.harness import NoveltyHarness

ROOT=Path(__file__).parents[1]


def history():
    return json.loads((ROOT/"examples/claim_history.json").read_text(encoding="utf-8"))


class ClaimContextTests(unittest.TestCase):
    def test_parent_and_dependent_can_have_different_novelty_results(self):
        raw={"schema_version":1,"selected_version":"filed","versions":[{"id":"filed","date":"2024-01-01","stage":"教学文本",
            "claims":[{"id":"1","kind":"independent","text":"一种装置，包括A，A连接B。","depends_on":[],"limitations":["包括A","A连接B"]},
                      {"id":"2","kind":"dependent","text":"根据权利要求1的装置，还包括C。","depends_on":["1"],"limitations":["还包括C"]}]}]}
        doc=PatentDocument("US123","教学原文","https://patents.google.com/patent/US123","2023-01-01",
            (SourceSection("s1","一种装置，包括A，A连接B。"),),source_kind="synthetic_eval",complete=True)
        reviews={}
        for target in claim_targets(raw):
            features,claim_target,_=resolve_target(raw,target["id"])
            rows=tuple(EvidenceRow(f.id,"not_found" if "还包括C"==f.text else "disclosed","s1",
                       "" if "还包括C"==f.text else f.text,"one","教学事实，非专业复核","human") for f in features)
            review=DocumentReview(doc.id,rows,reviewed=True,same_scheme_confirmed=True,enabling_disclosure_confirmed=True,
                reviewer="synthetic-fixture",same_scheme_basis="同一教学方案",scheme_section_ids=("s1",),
                relevance="related",relevance_basis="教学相关",relevance_section_ids=("s1",))
            ctx=context_for(features,"2024-01-01",{"target":claim_target})
            reviews[target["id"]]=[stamp_review(review,features,doc,"2024-01-01",patent_context=ctx)]
        batch=NoveltyHarness(allow_synthetic=True).run_claims(raw,cutoff="2024-01-01",documents=[doc],reviews_by_target=reviews)
        labels={item["target"]["id"]:item["state"]["decision"].label for item in batch["results"]}
        self.assertEqual(labels,{"1":"potential_novelty_risk","1/2":"no_complete_disclosure_found"})

    def test_multiple_dependencies_are_separate_complete_paths(self):
        raw=history()
        self.assertEqual([t["id"] for t in claim_targets(raw)],["1","1/2","1/3","1/2/3"])
        short,target,_=resolve_target(raw,"1/3")
        long,_,_=resolve_target(raw,"1/2/3")
        self.assertFalse(any("无线" in f.text for f in short))
        self.assertTrue(any("无线" in f.text for f in long))
        self.assertEqual(target["path"],["1","3"])

    def test_cycle_missing_parent_and_ambiguous_multiple_reference_rejected(self):
        mutations=[lambda h:h["versions"][0]["claims"][1].update(depends_on=["absent"]),
                   lambda h:h["versions"][0]["claims"][1].update(depends_on=["2"]),
                   lambda h:h["versions"][0]["claims"][2].update(dependency_mode="all")]
        for mutate in mutations:
            raw=history();mutate(raw)
            with self.assertRaises(ValueError):validate_history(raw)

    def test_no_silent_target_selection_or_invented_quoted_limitation(self):
        with self.assertRaises(ValueError):resolve_target(history())
        raw=history();raw["versions"][0]["claims"][0]["limitations"].append("模型补造的结构")
        with self.assertRaises(ValueError):validate_history(raw)

    def test_amendment_basis_must_be_from_earlier_actual_source(self):
        raw=history()
        _,target,sources=resolve_target(raw,"1/2")
        self.assertEqual(target["amendments"][0]["before"]["limitations"],["控制器通过无线通信模块发送温度数据"])
        self.assertTrue(any(s["id"]=="filed-description" for s in sources))
        for mutation in (lambda h:h["sources"][0].update(version_id="amended"),
                         lambda h:h["versions"][1]["amendment_basis"][0].update(quote="不存在的原文")):
            changed=deepcopy(raw);mutation(changed)
            with self.assertRaises(ValueError):validate_history(changed)

    def test_same_wording_in_different_version_invalidates_old_review(self):
        raw=history();raw["selected_version"]="filed"
        old,_,_=resolve_target(raw,"1")
        raw["selected_version"]="amended"
        new,_,_=resolve_target(raw,"1")
        self.assertEqual([f.text for f in old],[f.text for f in new])
        self.assertNotEqual([f.id for f in old],[f.id for f in new])
        doc=PatentDocument("US123","teaching","https://patents.google.com/patent/US123","2023-01-01",
                           (SourceSection("s1","；".join(f.text for f in old)),),complete=True)
        review=stamp_review(DocumentReview(doc.id,tuple(EvidenceRow(f.id,"disclosed","s1",f.text,"one","教学支持", "human") for f in old),
            reviewed=True,same_scheme_confirmed=True,enabling_disclosure_confirmed=True,reviewer="teaching-not-expert",
            same_scheme_basis="同一教学方案",scheme_section_ids=("s1",),relevance="related",
            relevance_basis="教学相关",relevance_section_ids=("s1",)),old,doc,"2024-01-01")
        state=NoveltyHarness().run(claim_history=raw,claim_target="1",cutoff="2024-01-01",documents=[doc],reviews=[review])
        self.assertEqual(state["decision"].label,"insufficient_evidence")

    def test_six_contexts_and_lineage_survive_report_and_batch_execution(self):
        raw=history()
        result=NoveltyHarness().run_claims(raw,cutoff="2024-01-01",documents=[])
        self.assertEqual(len(result["results"]),4)
        for entry in result["results"]:
            state=entry["state"]
            records=state["skill_records"]
            self.assertEqual(len(records),6)
            self.assertEqual(handoff_issues(records),[])
            for record in records:
                ctx=record["inputs"]["patent_context"]
                self.assertEqual(ctx["cutoff"],"2024-01-01")
                self.assertEqual(ctx["target"]["id"],entry["target"]["id"])
            self.assertIn("权利要求及修改范围",state["markdown"])
            self.assertIn("claim_history",state["report_payload"])

    def test_claims_cannot_silently_reuse_other_target_features(self):
        raw=history();features,_,_=resolve_target(raw,"1")
        with self.assertRaises(ValueError):
            NoveltyHarness().run(claim_history=raw,claim_target="1/2",features=features,cutoff="2024-01-01",documents=[])

    def test_target_label_cannot_bypass_formal_history_input(self):
        features,target,_=resolve_target(history(),"1")
        with self.assertRaises(ValueError):
            NoveltyHarness().run(features=features,patent_context={"target":target},cutoff="2024-01-01",documents=[])


class ContextEvaluationTests(unittest.TestCase):
    def test_changed_definition_invalidates_review_even_when_words_unchanged(self):
        from tests.test_model_review import ModelReviewTests
        from modules.candidate_relevance import valid_body_review
        fixture=ModelReviewTests();fixture.setUp()
        ctx=context_for(fixture.features,"2024-01-01",{"definitions":[{"term":"压力传感器","meaning":"测量流体压力"}]})
        review=fixture.run_review(patent_context=ctx)
        self.assertTrue(valid_body_review(review,fixture.features,fixture.doc,"2024-01-01",patent_context=ctx))
        changed=deepcopy(ctx);changed["definitions"][0]["meaning"]="测量机械接触力"
        self.assertFalse(valid_body_review(review,fixture.features,fixture.doc,"2024-01-01",patent_context=changed))

    def test_declared_functional_limitation_becomes_mandatory(self):
        state=NoveltyHarness().run(feature_text="控制器仅在温度过高时启动泵",cutoff="2024-01-01",documents=[],
                                  patent_context={"semantic_focus":["functions_effects"]})
        record=state["skill_records"][0]
        packet=evaluation_packet(record)
        self.assertIn("functions_effects",packet["required_dimensions"])
        data=judgment_example(record)
        next(f for f in data["findings"] if f["dimension"]=="functions_effects")["status"]="not_applicable"
        with self.assertRaises(ValueError):validate_judgment(data,packet)
        data=judgment_example(record);data.pop("activated_dimensions")
        with self.assertRaises(ValueError):validate_judgment(data,packet)

    def test_mismatched_date_or_scope_cannot_be_rebound(self):
        ctx=context_for([],"2024-01-01")
        with self.assertRaises(ValueError):context_for([],"2024-01-02",ctx)
        ctx["scope"]["sources"]="arbitrary web"
        with self.assertRaises(ValueError):context_for([],"2024-01-01",ctx)

    def test_contextual_chain_gets_separate_semantic_handoff_call(self):
        from evals.legacy.fixtures import case_inputs, dataset
        state=NoveltyHarness(allow_synthetic=True).run(**case_inputs(dataset()["cases"][0]))
        calls=[]
        client=Mock(model="teaching-judge",url="https://example.test/v1")
        def respond(system,user,**kwargs):
            packet=json.loads(user);calls.append(packet)
            data=judgment_example(packet["record"])
            if packet.get("kind")=="cross_skill_handoff":
                finding=next(f for f in data["findings"] if f["dimension"]=="evaluation_expression")
                finding.update(status="violated",counterexample="教学反例：报告放大证据结论。",next_action="恢复预筛边界。")
                data["verdict"]="needs_revision"
            return json.dumps(data)
        client.complete.side_effect=respond
        report=evaluate_records(state["skill_records"],client,True)
        self.assertEqual(len(calls),7)
        self.assertEqual(report["handoff"]["verdict"],"needs_revision")
        self.assertEqual(report["verdict"],"needs_revision")
        self.assertTrue(all(r["verdict"]=="acceptable" for r in report["results"]))

    def test_changed_upstream_binding_is_detected_even_with_same_features(self):
        state=NoveltyHarness().run(feature_text="压力传感器",cutoff="2024-01-01",documents=[])
        records=deepcopy(state["skill_records"])
        old=records[0]
        records[0]=make_record(old["skill_id"],old["inputs"],dict(old["output"],unknowns=["新增未知"]),
                              sources=old["sources"],producer=old["producer"],note=old["note"])
        self.assertTrue(any(i["status"]=="violated" and "上游" in i["basis"] for i in handoff_issues(records)))

    def test_different_document_content_is_not_hidden_by_identical_ids(self):
        from evals.legacy.fixtures import case_inputs,dataset
        state=NoveltyHarness(allow_synthetic=True).run(**case_inputs(dataset()["cases"][0]))
        records=deepcopy(state["skill_records"])
        evidence=records[3]
        changed=deepcopy(evidence["inputs"])
        changed["documents"][0]["sections"][0]["text"]="另一个装置的原文"
        records[3]=make_record(evidence["skill_id"],changed,evidence["output"],sources=evidence["sources"])
        self.assertTrue(any("原文快照" in i["basis"] for i in handoff_issues(records)))


class ClaimUITests(unittest.TestCase):
    def test_imported_claim_target_and_changed_version_are_used_by_real_ui(self):
        from tests.test_app import AppFlowTests, labeled
        app=AppFlowTests().app()
        labeled(app.checkbox,"按独立／从属权利要求及修改版本比对").set_value(True).run(timeout=60)
        labeled(app.text_area,"或粘贴权利要求记录").set_value(json.dumps(history(),ensure_ascii=False)).run(timeout=60)
        labeled(app.selectbox,"当前权利要求及引用路径").set_value("1/2").run(timeout=60)
        from datetime import date
        app.date_input[0].set_value(date(2024,1,1))
        labeled(app.text_input,"Google Patents 单篇链接").set_value("https://patents.google.com/patent/US123/en")
        labeled(app.text_input,"文献标题").set_value("教学快照")
        labeled(app.text_input,"公开日期 YYYY-MM-DD").set_value("2023-01-01")
        labeled(app.text_area,"原文（空行分段，不要只粘贴搜索摘要）").set_value("一种温控装置。")
        app.run(timeout=60)
        labeled(app.button,"加入人工原文快照").click().run(timeout=60)
        self.assertEqual(len(app.exception),0)
        state=app.session_state["novelty_state"]
        self.assertEqual(state["patent_context"]["target"]["id"],"1/2")
        self.assertTrue(any("经加密" in f.text for f in state["features"]))
        labeled(app.selectbox,"采用的权利要求版本").set_value("filed").run(timeout=60)
        self.assertEqual(len(app.exception),0)
        self.assertFalse(any(item.value=="预筛结果" for item in app.subheader))


if __name__=="__main__":unittest.main()
