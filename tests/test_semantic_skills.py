"""Behavior/contract tests use simulated judges, never claim model semantic accuracy."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from modules.skill_contracts import SKILLS,DIMENSIONS,make_record,validate_record,digest,serialized
from modules.semantic_eval import evaluation_packet,validate_judgment,evaluate_skill,evaluate_records
from modules.semantic_eval_examples import teaching_records,judgment_example,contract_demo
from modules.harness import NoveltyHarness
from modules.patent_tools import prepare_materials_with_model as prepare_with_model


def judge():
    client=Mock(model="test-independent-judge",url="https://example.test/v1/chat/completions")
    def respond(system,user,**kwargs):
        packet=json.loads(user)
        return json.dumps(judgment_example(packet["record"]),ensure_ascii=False)
    client.complete.side_effect=respond
    return client


class SemanticContractTests(unittest.TestCase):
    def setUp(self):
        self.records=teaching_records()
        self.record=self.records[0]
        self.packet=evaluation_packet(self.record)

    def test_records_bind_actual_input_output_and_sources(self):
        for field in ("inputs","output","sources"):
            record=deepcopy(self.record)
            record[field]={} if field!="sources" else []
            with self.assertRaises(ValueError): validate_record(record)

    def test_technical_values_preserved_but_operational_hints_removed(self):
        record=make_record(next(iter(SKILLS)),{"threshold":10,"score":5,"reading_hints":[{"score":.7,"text":"阈值10 kPa"}]},
                           {"threshold":10,"search_audit":{"lexical_hint":{"score":.4},"query":"10 kPa"}})
        self.assertEqual(record["inputs"]["threshold"],10)
        self.assertEqual(record["inputs"]["score"],5)
        self.assertNotIn("score",record["inputs"]["reading_hints"][0])
        self.assertNotIn("lexical_hint",record["output"]["search_audit"])
        self.assertEqual(validate_record(record),record)

    def test_eval_never_runs_without_own_consent(self):
        client=judge()
        with self.assertRaises(PermissionError): evaluate_records(self.records,client)
        client.complete.assert_not_called()

    def test_six_skills_each_have_all_six_dimensions_without_scores(self):
        client=judge()
        report=evaluate_records(self.records,client,True)
        self.assertEqual(client.complete.call_count,len(SKILLS))
        self.assertEqual(report["verdict"],"acceptable")
        self.assertEqual({r["skill_id"] for r in report["results"]},set(SKILLS))
        for result in report["results"]:
            self.assertEqual({r["dimension"] for r in result["findings"]},set(DIMENSIONS))
            self.assertNotIn("score",result)
            self.assertEqual(result["provenance"],"model_semantic_judge_not_human")

    def test_same_business_label_can_receive_revision_from_independent_judge(self):
        record=self.records[4]
        before=deepcopy(record)
        client=judge()
        client.complete.side_effect=None
        client.complete.return_value=json.dumps(judgment_example(record,violation=True))
        result=evaluate_skill(record,client,True)
        self.assertEqual(result["verdict"],"needs_revision")
        self.assertEqual(record,before)

    def test_partial_skill_not_evaluated_or_counted_as_acceptable(self):
        record=make_record(self.record["skill_id"],{}, {},execution="partial")
        client=judge()
        result=evaluate_skill(record,client,True)
        self.assertEqual(result["execution"],"not_executed")
        self.assertIsNone(result["verdict"])
        client.complete.assert_not_called()
        report=evaluate_records([record],client,True)
        self.assertEqual(report["execution"],"incomplete")
        self.assertIsNone(report["verdict"])

    def test_missing_skill_keeps_whole_chain_incomplete(self):
        report=evaluate_records(self.records[:-1],judge(),True)
        self.assertIsNone(report["verdict"])
        self.assertEqual(report["results"][-1]["execution"],"not_executed")

    def test_size_and_reference_preflight_happens_before_any_call(self):
        records=self.records[:-1]+[make_record(self.records[-1]["skill_id"],{"text":"字"*50001},{})]
        client=judge()
        with self.assertRaises(ValueError): evaluate_records(records,client,True)
        client.complete.assert_not_called()
        with self.assertRaises(ValueError):
            evaluate_records(self.records,client,True,{self.records[-1]["skill_id"]:{"record_hash":"stale"}})
        client.complete.assert_not_called()

    def test_reference_identity_and_binding_are_explicit(self):
        good={"record_hash":self.record["record_hash"],"provenance":"independent_human_reference","author":"test-fixture-reviewer","basis":"作者给定的测试参考，不是真实专家评估。"}
        self.assertEqual(evaluation_packet(self.record,good)["reference_status"],"independent_human_reference")
        for field,value in (("author",None),("provenance","model"),("record_hash","old")):
            bad=dict(good);bad[field]=value
            with self.assertRaises(ValueError):evaluation_packet(self.record,bad)

    def test_judge_failures_isolated_and_never_turn_into_passes(self):
        client=judge();client.complete.side_effect=RuntimeError("test failure")
        report=evaluate_records(self.records,client,True)
        self.assertTrue(all(r["execution"]=="failed" for r in report["results"]))
        self.assertIsNone(report["verdict"])
        self.assertNotIn("test failure",serialized(report))

    def test_judge_endpoint_does_not_persist_credentials(self):
        client=judge();client.url="https://account:password@example.test/v1/chat/completions?key=secret"
        result=evaluate_skill(self.record,client,True)
        self.assertEqual(result["judge"]["endpoint"],"https://example.test/v1/chat/completions")

    def test_validator_rejects_incomplete_fabricated_or_numeric_judgments(self):
        mutations=[lambda d:d["findings"].pop(),
            lambda d:d.update(score=.9),lambda d:d["findings"][0].update(score=.9),
            lambda d:d["findings"][0]["citations"][0].update(quote="不存在的引文"),
            lambda d:d["findings"][0].update(dimension=[]),
            lambda d:d["findings"][0].update(status={}),
            lambda d:d["findings"][0].update(status="not_applicable"),
            lambda d:d["findings"][0].update(citations=[d["findings"][0]["citations"][0]]),
            lambda d:d.update(verdict="needs_revision")]
        for mutate in mutations:
            data=judgment_example(self.record);mutate(data)
            with self.subTest(data=data):
                with self.assertRaises(ValueError):validate_judgment(data,self.packet)

    def test_uncertainty_requires_action_and_matches_final_verdict(self):
        data=judgment_example(self.record)
        data["findings"][0].update(status="undetermined",next_action="补充术语定义。")
        data["verdict"]="pending_adjudication"
        self.assertEqual(validate_judgment(data,self.packet)["verdict"],"pending_adjudication")
        data["findings"][0]["next_action"]=""
        with self.assertRaises(ValueError):validate_judgment(data,self.packet)

    def test_demo_is_explicitly_not_a_semantic_run(self):
        result=contract_demo()
        self.assertIsNone(result["verdict"])
        self.assertEqual(result["execution"],"not_executed")
        self.assertTrue(all(c["status"]=="passed" for c in result["checks"]))


class BusinessSkillTests(unittest.TestCase):
    def test_real_workflow_records_all_six_business_steps(self):
        search=Mock()
        state=NoveltyHarness(searcher=search).run(feature_text="控制器仅在温度过高时启动泵",cutoff="2024-01-01",documents=[])
        search.assert_not_called()
        self.assertEqual([r["skill_id"] for r in state["skill_records"]],list(SKILLS))
        self.assertEqual(state["decision"].label,"insufficient_evidence")
        evidence=next(r for r in state["skill_records"] if r["skill_id"]=="patent-evidence-alignment")
        self.assertEqual(evidence["execution"],"partial")
        for record in state["skill_records"]:validate_record(record)

    def test_model_material_preparation_validates_actual_quotes_and_conditions(self):
        client=judge()
        raw="控制器仅在温度过高时启动泵。"
        data={"features":[{"id":"F1","text":raw,"evidence":[{"source":"idea","location":"用户描述","quote":raw}]}],"unknowns":[]}
        client.complete.side_effect=None;client.complete.return_value=json.dumps(data)
        result,record=prepare_with_model({"idea":raw},client,True)
        self.assertEqual(result["features"][0]["text"],raw)
        self.assertEqual(record["execution"],"completed")
        data["features"][0]["evidence"][0]["quote"]="补造的结构"
        client.complete.return_value=json.dumps(data)
        with self.assertRaises(ValueError):prepare_with_model({"idea":raw},client,True)

    def test_changed_material_record_is_not_reused_for_new_features(self):
        record=make_record("patent-material-preparation",{}, {"features":[{"id":"F1","text":"压力传感器"}]})
        with self.assertRaises(ValueError):
            NoveltyHarness().run(feature_text="温度传感器",cutoff="2024-01-01",documents=[],material_skill_record=record)

    def test_report_record_does_not_include_itself_or_the_evaluation(self):
        from modules.patent_tools import build_report as report_payload
        state=NoveltyHarness().run(feature_text="控制器",cutoff="2024-01-01",documents=[])
        report=report_payload(state,"控制器装置","2024-01-01",[])
        delivery=report["skill_records"][-1]
        self.assertNotIn("skill_records",delivery["inputs"])
        self.assertNotIn("semantic_evaluation",delivery["inputs"])
        self.assertNotIn("六个业务技能执行记录",delivery["output"]["markdown"])

    def test_eight_discoverable_skills_and_frameworks_remain_code_only(self):
        root=Path(__file__).parents[1]
        names={p.parent.name for p in (root/".agents/skills").glob("*/SKILL.md")}
        self.assertEqual(names,set(SKILLS)|{"patent-precheck","patent-semantic-eval"})

    def test_report_refreshes_records_after_review_revocation(self):
        from evals.legacy.fixtures import case_inputs,dataset
        from modules.patent_tools import build_report as report_payload
        from modules.novelty_rules import assess_novelty
        from modules.skill_contracts import plain
        state=NoveltyHarness(allow_synthetic=True).run(**case_inputs(dataset()["cases"][0]))
        state["reviews"]=[]
        state["decision"]=assess_novelty(state["features"],state["documents"],[],"2024-01-01",allow_synthetic=True)
        payload=report_payload(state,"案例","2024-01-01",[])
        records={r["skill_id"]:r for r in payload["skill_records"]}
        self.assertEqual(records["patent-evidence-alignment"]["execution"],"partial")
        self.assertEqual(records["patent-evidence-alignment"]["output"]["reviews"],[])
        self.assertEqual(records["patent-novelty-assessment"]["output"],plain(state["decision"]))


if __name__=="__main__":unittest.main()
