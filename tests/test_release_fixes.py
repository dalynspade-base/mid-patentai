"""1.0.1 regressions: lossless judge packets, provenance refresh and search failures."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import Mock

from modules.candidate_ui import rerun_with_evidence
from modules.google_patent_documents import manual_google_document
from modules.novelty_models import Feature
from modules.novelty_matching import SynonymLexicon
from modules.novelty_review import model_review_documents
from modules.semantic_eval import (
    MAX_EVAL_CHARACTERS, evaluate_records, evaluation_packet, source_texts, validate_judgment,
)
from modules.semantic_eval_examples import judgment_example
from modules.skill_contracts import serialized, validate_record
from modules.synonym_expansion import build_expansion_plan, candidate_id
from skills.precheck_skill import NoveltyHarness
from tests.test_model_review import review_response


def skill(state, name):
    return next(r for r in state["skill_records"] if r["skill_id"] == "patent-" + name)


def document(key="US123", tail=""):
    return manual_google_document(
        f"https://patents.google.com/patent/{key}/en", "构造测试快照", "2023-01-01",
        "装置包括压力传感器。" + tail, complete=True,
    )


def judge():
    client = Mock(model="test-model", url="https://example.test/v1/chat/completions")
    client.complete.side_effect = lambda system, user, **kwargs: json.dumps(
        judgment_example(json.loads(user)["record"]), ensure_ascii=False,
    )
    return client


class SemanticPacketRegressionTests(unittest.TestCase):
    def reviewed(self, docs):
        client = judge()
        client.complete.side_effect = lambda system, user, **kwargs: json.dumps(
            review_response(json.loads(user)["document"]["id"]), ensure_ascii=False,
        )
        features = [Feature("F1", "压力传感器")]
        reviews = model_review_documents(features, docs, "2024-01-01", client, True)
        return NoveltyHarness().run(features=features, cutoff="2024-01-01", documents=docs, reviews=reviews)

    def test_8000_character_evidence_no_longer_blocks_all_six_judges(self):
        state = self.reviewed([document(tail="技术限定不应截断。" * 1000)])
        record = skill(state, "evidence-alignment")
        original = deepcopy(record)
        packet = evaluation_packet(record)
        archived = dict(packet, sources=record["sources"] + [
            {"id": "skill_input", "text": serialized(record["inputs"])},
            {"id": "skill_output", "text": serialized(record["output"])},
        ])
        self.assertGreater(len(serialized(archived)), MAX_EVAL_CHARACTERS)
        self.assertLessEqual(len(serialized(packet)), MAX_EVAL_CHARACTERS)
        self.assertEqual(packet["record"], original)
        self.assertEqual(source_texts(packet)["US123:manual-1"], state["documents"][0].sections[0].text)
        client = judge()
        report = evaluate_records(state["skill_records"], client, True)
        self.assertEqual(report["execution"], "completed")
        self.assertEqual(client.complete.call_count, 6)
        self.assertEqual(record, original)

    def test_two_documents_preserve_every_source_and_old_quote_validation(self):
        state = self.reviewed([document(tail="甲" * 3500), document("US124", "乙" * 3500)])
        record = skill(state, "evidence-alignment")
        packet = evaluation_packet(record)
        sources = source_texts(packet)
        for source in record["sources"]:
            self.assertEqual(sources[source["id"]], source["text"])
        valid = judgment_example(record)
        validate_judgment(valid, packet)
        archived = dict(packet, sources=[{"id": k, "text": v} for k, v in sources.items()])
        self.assertEqual(validate_judgment(valid, archived), valid)
        bad = deepcopy(valid)
        bad["findings"][0]["citations"][0]["quote"] = "根本不存在的原文"
        with self.assertRaises(ValueError):
            validate_judgment(bad, packet)
        report = evaluate_records(state["skill_records"], judge(), True)
        self.assertEqual(report["execution"], "completed")


class SearchAndRefreshRegressionTests(unittest.TestCase):
    def setUp(self):
        self.features = [Feature("F1", "压力传感器连接控制器")]
        self.lexicon = SynonymLexicon()
        self.doc = document()
        candidate = {"feature_id": "F1", "source": "压力传感器", "term": "压力测量传感器",
                     "language": "zh", "category": "component", "relation": "equivalent",
                     "rationale": "测试中显式审核的候选"}
        generation = {"status": "ok", "candidates": [candidate], "model": "simulated"}
        self.plan = build_expansion_plan(self.features, self.lexicon, "engineering", ["控制器"],
                                         generation, [candidate_id(candidate)])
        self.search = Mock(return_value=[{"url": self.doc.url, "title": self.doc.title}])
        self.harness = NoveltyHarness(searcher=self.search, fetcher=Mock(return_value=self.doc),
                                      lexicon=self.lexicon)

    def searched(self):
        return self.harness.run(features=self.features, cutoff="2024-01-01",
                                expansion=self.plan, material_original="原材料：压力传感器连接控制器",
                                material_unknowns=["电源未确定"])

    def test_refresh_keeps_material_plan_approval_query_and_online_provenance(self):
        state = self.searched()
        original = deepcopy(state)
        count = self.search.call_count
        for changes in ({}, {"documents": state["documents"] + [document("US124")]}, {"reviews": []}):
            with self.subTest(changes=changes.keys()):
                updated = self.harness.refresh(state, **changes)
                for name in ("material-preparation", "synonym-expansion"):
                    self.assertEqual(skill(updated, name), skill(state, name))
                self.assertEqual(updated["expansion"], self.plan)
                self.assertEqual(updated["material_original"], state["material_original"])
                self.assertEqual(updated["material_unknowns"], ["电源未确定"])
                self.assertEqual(skill(updated, "candidate-search")["inputs"]["mode"], "online")
                self.assertEqual(skill(updated, "candidate-search")["inputs"]["expansion"],
                                 updated["search_audit"]["expansion"])
                for record in updated["skill_records"]:
                    validate_record(record)
        self.assertEqual(state, original)
        self.assertEqual(self.search.call_count, count)

    def test_candidate_helper_preserves_query_and_frozen_records(self):
        state = self.harness.run(features=self.features, cutoff="2024-01-01", query="custom query",
                                 expansion=self.plan, material_original="实际描述")
        updated = rerun_with_evidence(state, self.harness, "2024-01-01", {},
                                     audit=state["search_audit"])
        self.assertEqual(updated["query"], "custom query")
        self.assertEqual(skill(updated, "synonym-expansion"), skill(state, "synonym-expansion"))
        self.assertEqual(skill(updated, "material-preparation"), skill(state, "material-preparation"))
        self.assertEqual(skill(updated, "candidate-search")["inputs"]["query"], "custom query")
        with self.assertRaises(ValueError):
            rerun_with_evidence(state, self.harness, "2025-01-01", {}, audit=state["search_audit"])

    def test_existing_audit_expansion_is_recovered_without_silent_default(self):
        state = self.searched()
        updated = self.harness.run(features=self.features, cutoff="2024-01-01",
                                   documents=state["documents"], search_audit=state["search_audit"])
        self.assertEqual(skill(updated, "synonym-expansion"), skill(state, "synonym-expansion"))

    def test_all_queries_failed_is_failed_and_cannot_be_accepted_by_judge(self):
        harness = NoveltyHarness(searcher=Mock(side_effect=RuntimeError("simulated timeout")))
        state = harness.run(features=self.features, cutoff="2024-01-01")
        record = skill(state, "candidate-search")
        self.assertEqual(record["execution"], "failed")
        self.assertEqual(state["decision"].label, "insufficient_evidence")
        client = judge()
        report = evaluate_records([record], client, True, required_skills=[record["skill_id"]])
        client.complete.assert_not_called()
        self.assertEqual(report["execution"], "incomplete")
        self.assertIsNone(report["verdict"])

    def test_successful_empty_results_are_not_network_failure(self):
        state = NoveltyHarness(searcher=Mock(return_value=[])).run(
            features=self.features, cutoff="2024-01-01",
        )
        self.assertEqual(skill(state, "candidate-search")["execution"], "completed")
        self.assertEqual(state["decision"].label, "insufficient_evidence")

    def test_partial_query_or_unread_page_is_partial_not_completed(self):
        self.search.side_effect = lambda query, limit: (
            [] if query == self.features[0].text else (_ for _ in ()).throw(RuntimeError("timeout"))
        )
        state = self.searched()
        self.assertEqual(skill(state, "candidate-search")["execution"], "partial")
        self.search.side_effect = None
        self.search.return_value = [{"url": self.doc.url}, {"url": document("US124").url}]
        self.harness.max_documents = 1
        state = self.searched()
        self.assertEqual(skill(state, "candidate-search")["execution"], "partial")

    def test_page_failure_empty_snapshots_and_invalid_snapshot_are_distinct(self):
        self.harness.fetcher = Mock(side_effect=RuntimeError("body failed"))
        self.assertEqual(skill(self.searched(), "candidate-search")["execution"], "partial")
        state = self.harness.run(features=self.features, cutoff="2024-01-01", documents=[])
        self.assertEqual(skill(state, "candidate-search")["execution"], "not_executed")
        from skills.candidate_search_skill import search_execution
        self.assertEqual(search_execution({"mode": "provided_snapshots"},
                                         [replace(self.doc, error="missing body")]), "failed")


if __name__ == "__main__":
    unittest.main()
