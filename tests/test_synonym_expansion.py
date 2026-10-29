"""四层同义词扩展的契约与边界回归；不调用真实模型或网络。"""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from modules.llm_client import LLMClient
from modules.novelty_matching import SynonymLexicon
from modules.novelty_models import Feature
from modules.novelty_search import query_plan, retrieve_with_journal, search_warnings
from modules.synonym_expansion import build_expansion_plan, candidate_id, generate_llm_candidates


def proposed(source="夹头", term="筒夹", **changes):
    return {"feature_id": "F1", "source": source, "term": term, "language": "zh", "category": "component",
            "relation": "equivalent", "rationale": "模拟：此处为同一夹持部件的领域表达", **changes}


class SynonymExpansionTests(unittest.TestCase):
    def setUp(self):
        self.features = [Feature("F1", "夹头连接驱动轴")]
        self.lexicon = SynonymLexicon([["夹头", "夹持头", "chuck"], ["驱动轴", "传动轴", "drive shaft"]])

    def plan(self, candidates=None, approved=None, **kwargs):
        return build_expansion_plan(kwargs.pop("features", self.features), kwargs.pop("lexicon", self.lexicon),
            kwargs.pop("profile", "engineering"), kwargs.pop("protected_terms", []),
            {"status": "ok", "candidates": candidates or [], "cost": {"llm_calls": 0}}, approved, **kwargs)

    def test_engineering_combines_two_components_and_theory_avoids_combinations(self):
        engineering = self.plan()
        theory = self.plan(profile="theoretical")
        self.assertTrue(any(v["kind"] == "engineering_combination" for v in engineering["variants"]))
        self.assertFalse(any(v["kind"] == "engineering_combination" for v in theory["variants"]))
        self.assertGreater(engineering["policy"]["query_budget"], theory["policy"]["query_budget"])
        self.assertGreater(len(engineering["variants"]), len(theory["variants"]))

    def test_new_alias_is_pending_until_explicitly_reviewed(self):
        candidate = proposed()
        plan = self.plan([candidate])
        self.assertEqual(next(c for c in plan["candidates"] if c["origin"] == "llm")["status"], "pending")
        self.assertFalse(any("筒夹" in v["query"] for v in plan["variants"]))
        plan = self.plan([candidate], [candidate_id(candidate)])
        self.assertTrue(any("筒夹" in v["query"] for v in plan["variants"]))
        self.assertNotIn("筒夹", self.lexicon.aliases)

    def test_broader_related_narrower_and_unknown_terms_are_rejected_even_if_approved(self):
        for relation in ("broader", "related", "narrower", "unknown"):
            candidate = proposed(relation=relation)
            row = self.plan([candidate], [candidate_id(candidate)])["candidates"][-1]
            self.assertEqual(row["status"], "rejected")

    def test_proper_name_is_preserved_even_when_dictionary_contains_a_translation(self):
        features = [Feature("F1", "Kalman 滤波器")]
        lexicon = SynonymLexicon([["Kalman", "卡尔曼"]])
        plan = self.plan(features=features, lexicon=lexicon, profile="theoretical", protected_terms=["Kalman"])
        self.assertEqual(plan["variants"], [])
        self.assertEqual(plan["candidates"][0]["status"], "rejected")

    def test_theory_does_not_generalize_named_method_into_a_component_or_function(self):
        candidate = proposed()
        self.assertEqual(self.plan([candidate], [candidate_id(candidate)], profile="theoretical")["candidates"][-1]["status"], "rejected")

    def test_english_translation_and_known_acronym_are_dictionary_verified(self):
        features = [Feature("F1", "使用离散余弦变换")]
        lexicon = SynonymLexicon([["离散余弦变换", "DCT", "discrete cosine transform"]])
        plan = self.plan(features=features, lexicon=lexicon, profile="theoretical")
        self.assertIn("使用DCT", [v["query"] for v in plan["variants"]])

    def test_all_original_queries_precede_variants_and_preserve_units_and_negation(self):
        features = [Feature("F1", "Pressure   Sensor不串联控制器，功率1 mW"), Feature("F2", "功率1 MW")]
        lexicon = SynonymLexicon()
        plan = build_expansion_plan(features, lexicon)
        queries = query_plan(features, "", lexicon, plan)["queries"]
        self.assertEqual([q["query"] for q in queries[:2]], [f.text for f in features])
        self.assertTrue(any("压力传感器" in q["query"] for q in queries))
        for q in queries[2:]:
            self.assertIn("不", q["query"])
            self.assertIn("1 mW", q["query"])

    def test_rule_blocks_numeric_unit_negation_and_connection_drift(self):
        for source, term in [("10 kPa", "100 kPa"), ("1 mW", "1 MW"), ("不包括", "包括"), ("串联", "并联")]:
            candidate = proposed(source, term)
            row = self.plan([candidate], [candidate_id(candidate)], features=[Feature("F1", source + "夹头")])["candidates"][-1]
            self.assertEqual(row["status"], "rejected")

    def test_complete_word_boundary_avoids_motor_matching_motorized(self):
        lexicon = SynonymLexicon([["motor", "电动机"]])
        plan = self.plan(features=[Feature("F1", "motorized actuator")], lexicon=lexicon)
        self.assertEqual(plan["variants"], [])

    def test_unknown_sources_and_query_operators_are_rejected(self):
        for candidate in (proposed("不存在"), proposed(term='夹头 OR 电动机'), proposed(term='x\\1'), proposed(term='x\ny')):
            row = self.plan([candidate], [candidate_id(candidate)])["candidates"][-1]
            self.assertEqual(row["status"], "rejected")

    def test_whole_relation_rewrites_and_port_direction_changes_are_rejected(self):
        for source, term in (("夹头连接驱动轴", "驱动轴连接夹头"), ("输入端", "输出端"), ("入口", "出口")):
            candidate = proposed(source, term)
            row = self.plan([candidate], [candidate_id(candidate)], features=[Feature("F1", source)])["candidates"][-1]
            self.assertEqual(row["status"], "rejected")

    def test_malformed_extra_feature_and_excess_candidates_are_rejected(self):
        for candidates in ([proposed(feature_id="F2")], [proposed(language="bad")], [proposed(rationale=None)], [proposed()] * 37):
            with self.assertRaises(ValueError):
                self.plan(candidates)

    def test_old_plan_or_tampered_queries_cannot_change_current_feature(self):
        plan = self.plan()
        with self.assertRaises(ValueError):
            query_plan([Feature("F1", "另一项技术")], "", self.lexicon, plan)
        plan["variants"] = [{"feature_id": "F1", "query": "已被篡改"}]
        actual = query_plan(self.features, "", self.lexicon, plan)
        self.assertFalse(any(q["query"] == "已被篡改" for q in actual["queries"]))

    def test_custom_query_keeps_user_scope_and_does_not_append_llm_terms(self):
        plan = self.plan([proposed()], [candidate_id(proposed())])
        records = query_plan(self.features, "用户指定词", self.lexicon, plan)["queries"]
        self.assertEqual([q["query"] for q in records], ["用户指定词"])
        self.assertEqual(records[0]["feature_ids"], [])

    def test_profile_budget_and_expansion_provenance_survive_actual_search_journal(self):
        features = [Feature(f"F{i}", f"夹头连接驱动轴{i}") for i in range(1, 7)]
        plan = self.plan(features=features)
        search = Mock(return_value=[])
        docs, journal = retrieve_with_journal(features, "2024-01-01", "", self.lexicon, search, Mock(), 1, plan)
        self.assertEqual(search.call_count, 16)
        self.assertEqual(journal["retrieval_cost"]["search_calls"], 16)
        self.assertEqual(journal["expansion"]["profile"], "engineering")
        self.assertTrue(journal["skipped"])
        self.assertTrue(search_warnings(journal, features, "2024-01-01"))

    def test_llm_needs_consent_and_cost_is_explicit(self):
        client = LLMClient("test-key", "https://example.test", "test-model")
        client.complete = Mock(return_value=json.dumps({"candidates": [proposed()]}))
        with self.assertRaises(PermissionError):
            generate_llm_candidates(self.features, self.lexicon, "engineering", [], client, False)
        client.complete.assert_not_called()
        result = generate_llm_candidates(self.features, self.lexicon, "engineering", [], client, True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["cost"]["llm_calls"], 1)
        self.assertEqual(result["cost"]["input_characters"], sum(len(x) for x in client.complete.call_args.args[:2]))
        self.assertIsNone(result["cost"]["usage_tokens"])
        self.assertNotIn("test-key", json.dumps(result))

    def test_failed_llm_records_failure_and_uses_only_dictionary(self):
        client = LLMClient("test-key", "https://example.test", "test-model")
        client.complete = Mock(side_effect=RuntimeError("模拟失败"))
        result = generate_llm_candidates(self.features, self.lexicon, "engineering", [], client, True)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["candidates"], [])
        plan = build_expansion_plan(self.features, self.lexicon, "engineering", generated=result)
        self.assertTrue(all(c["origin"] == "dictionary" for c in plan["candidates"]))

    def test_real_adapter_records_token_usage_and_retry_count_without_credentials(self):
        busy = Mock(status_code=429)
        ok = Mock(status_code=200)
        ok.json.return_value = {"choices": [{"message": {"content": '{"candidates":[]}'}, "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110, "private_field": "hidden"}}
        client = LLMClient("test-key", "https://example.test", "test-model", post=Mock(side_effect=[busy, ok]), sleep=Mock())
        result = generate_llm_candidates(self.features, self.lexicon, "engineering", [], client, True)
        self.assertEqual(result["cost"]["http_attempts"], 2)
        self.assertEqual(result["cost"]["usage_tokens"]["total_tokens"], 110)
        self.assertNotIn("private_field", json.dumps(result))




if __name__ == "__main__":
    unittest.main()
