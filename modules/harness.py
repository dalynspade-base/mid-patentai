"""Bounded patent harness: task state, ordered tool calls, records and stopping points."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Callable, TypedDict


from modules.google_patent_documents import fetch_google_document
from modules.evidence_alignment import locate_evidence
from modules.json_output import parse_object
from modules.novelty_matching import SynonymLexicon
from modules.novelty_models import (
    DocumentReview, Feature, NoveltyDecision, PatentDocument,
    check_cutoff,
)
from modules.candidate_relevance import incremental_summary
from modules.patent_search import search_patents
from modules.skill_contracts import upsert_record
from modules.patent_context import context_for, bind_material, handoff_issues
from modules import patent_tools as operations
from modules.skill_library import skill_prompt
from modules.claim_context import resolve_target, claim_targets, validate_history


class NoveltyState(TypedDict, total=False):
    task: str
    feature_text: str
    features: list[Feature]
    query: str
    cutoff: str
    documents: list[PatentDocument]
    reviews: list[DocumentReview]
    errors: list[str]
    material_unknowns: list[str]
    material_warnings: list[str]
    suggestions: list[DocumentReview]
    hits: list[dict]
    decision: NoveltyDecision
    trace: list[str]
    search_audit: dict
    expansion: dict
    retrieval_increment: dict
    skill_records: list[dict]
    material_original: str
    material_skill_record: dict
    report_payload: dict
    markdown: str
    patent_context: dict
    claim_history: dict
    claim_target: str
    claim_sources: list[dict]


def suggest_rows(features: list[Feature], documents: list[PatentDocument],
                 lexicon: SynonymLexicon) -> list[DocumentReview]:
    """兼容原候选入口，统一使用LlamaIndex阅读线索，不自动判断技术含义。"""
    return locate_evidence(features, documents, lexicon, allow_synthetic=True)[1]


def model_suggest_rows(features: list[Feature], documents: list[PatentDocument], client,
                       confirmed: bool) -> list[DocumentReview]:
    """兼容原模型建议入口；与主复核共享语义指令、原文和LlamaIndex线索。"""
    if not confirmed:
        raise PermissionError("请先确认要向模型发送的技术要素和选定原文。")
    from modules.novelty_review import EVIDENCE_ALIGNMENT_INSTRUCTIONS, model_review_payload, validate_evidence_rows
    system = EVIDENCE_ALIGNMENT_INSTRUCTIONS + (
        "仅返回JSON对象reviews数组，每项document_id、rows；rows每项feature_id、"
        "status(disclosed/not_found/unknown)、section_id、quote、scheme_id、basis。"
        "同一技术方案用同一个scheme_id，不能确定则留空。不得把所有实施例默认编成同一组。"
    )
    aligned = [model_review_payload(features, document, "") for document in documents]
    payload = {"features": [asdict(f) for f in features],
               "documents": [item["document"] | {"document_id": item["document"]["id"]} for item in aligned],
               "retrieval_hints": [hint for item in aligned for hint in item["retrieval_hints"]],
               "evidence_alignment": {"primary": "model_semantics", "retrieval": "reading_hints_only",
                                      "coverage": "all_snapshot_sections"}}
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized) > 50000:
        raise ValueError("选定原文超过 5 万字，请缩小到需复核的完整相关段落；不要静默截断限定条件。")
    data = parse_object(client.complete(skill_prompt("patent-evidence-alignment", system), serialized, max_tokens=6000))
    if not isinstance(data.get("reviews"), list):
        raise ValueError("模型对照缺少 reviews 数组。")
    docs = {doc.id: doc for doc in documents}
    result = []
    seen: set[str] = set()
    for item in data["reviews"]:
        if not isinstance(item, dict) or item.get("document_id") not in docs or item["document_id"] in seen:
            raise ValueError("模型返回未知或重复文献。")
        doc_id = item["document_id"]
        seen.add(doc_id)
        rows = validate_evidence_rows(item.get("rows"), features, docs[doc_id])
        result.append(DocumentReview(doc_id, rows, review_method="candidate"))  # reviewed 始终为 False。
    if seen != set(docs):
        raise ValueError("模型遗漏选定文献。")
    return result


class NoveltyHarness:
    def __init__(self, *, searcher: Callable | None = None, fetcher: Callable | None = None,
                 lexicon: SynonymLexicon | None = None, max_documents: int = 6,
                 allow_synthetic: bool = False) -> None:
        self.searcher = searcher or search_patents
        self.fetcher = fetcher or fetch_google_document
        self.lexicon = lexicon or SynonymLexicon()
        self.max_documents = max(1, min(max_documents, 20))
        self.allow_synthetic = allow_synthetic

    def _prepare(self, state: NoveltyState) -> dict:
        task = state.get("task", "novelty")
        if task != "novelty":
            return {"task": task, "features": [], "trace": ["prepare"]}
        check_cutoff(state["cutoff"])
        features,record = operations.prepare_materials(state.get("feature_text", ""),features=state.get("features"),
            original_text=state.get("material_original",""),unknowns=state.get("material_unknowns"),warnings=state.get("material_warnings"),
            cutoff=state["cutoff"],context=state.get("patent_context"),sources=state.get("claim_sources", []))
        ctx = record["inputs"]["patent_context"]
        if state.get("material_skill_record"):
            from modules.skill_contracts import validate_record
            candidate=validate_record(state["material_skill_record"])
            expected=[{"id":f.id,"text":f.text} for f in features]
            actual=[{"id":f["id"],"text":f["text"]} for f in candidate["output"].get("features",[])]
            if candidate["skill_id"] != "patent-material-preparation" or actual != expected:
                raise ValueError("材料技能记录与当前确认要素不匹配。")
            record=bind_material(candidate,features,state["cutoff"],ctx)
        return {"task": task, "features": features, "trace": ["prepare"], "skill_records":[record], "patent_context":ctx}

    def _expand(self,state):
        if state.get("expansion") is not None:
            plan=state["expansion"]
            record=operations.record_expansion(state["features"],self.lexicon,plan,
                cutoff=state["cutoff"],context=state["patent_context"],upstream=state["skill_records"])
        else:
            # Keep the existing conservative search budget when no policy was selected.
            plan,record=operations.expand_terms(state["features"],self.lexicon,"theoretical",
                cutoff=state["cutoff"],context=state["patent_context"],upstream=state["skill_records"])
            return {"skill_records":upsert_record(state.get("skill_records",[]),record)}
        return {"expansion":plan,"skill_records":upsert_record(state.get("skill_records",[]),record)}

    def _retrieve(self, state: NoveltyState) -> dict:
        errors = list(state.get("errors", []))
        # 显式传入空快照集与未传快照不同：空集不会偷偷访问网络。
        documents,audit,record=operations.search_candidates(state["features"],state["cutoff"],self.lexicon,
            self.searcher,self.fetcher,self.max_documents,query=state.get("query",""),expansion=state.get("expansion"),
            documents=state.get("documents"),supplied="documents" in state,audit=state.get("search_audit"),errors=errors,
            context=state["patent_context"],upstream=state["skill_records"])
        return {"documents": documents, "search_audit": audit,
                "errors": errors, "trace": state["trace"] + ["retrieve"],
                "skill_records":upsert_record(state.get("skill_records",[]),record)}

    def _locate(self, state: NoveltyState) -> dict:
        hits,candidates,_=operations.locate_candidates(state["features"],state["documents"],self.lexicon,
            allow_synthetic=self.allow_synthetic,locator=locate_evidence)
        record=operations.record_alignment(state["features"],state["documents"],hits,candidates,
            state.get("reviews",[]),state["cutoff"],context=state["patent_context"],upstream=state["skill_records"])
        return {"hits": hits, "suggestions": candidates,
                "trace": state["trace"] + ["locate_evidence"],
                "skill_records":upsert_record(state.get("skill_records",[]),record)}

    def _decide(self, state: NoveltyState) -> dict:
        decision,record = operations.assess_precheck(state["features"], state.get("documents", []), state.get("reviews", []),
                                  state.get("cutoff", "2000-01-01"), task=state["task"],
                                  patent_context=state.get("patent_context"),upstream=state.get("skill_records", []),
                                  handoff_errors=[i["basis"] for i in handoff_issues(state.get("skill_records", [])) if i["status"] == "violated"],
                                  errors=state.get("errors"), allow_synthetic=self.allow_synthetic,
                                  material_unknowns=state.get("material_unknowns"),
                                  material_warnings=state.get("material_warnings"),
                                  search_audit=state.get("search_audit"))
        increment = incremental_summary(state["features"], state.get("cutoff", "2000-01-01"),
            state.get("search_audit", {}), state.get("documents", []), state.get("reviews", []))
        return {"decision": decision, "retrieval_increment": increment, "trace": state["trace"] + ["decide"],
                "skill_records":upsert_record(state.get("skill_records",[]),record)}

    def _deliver(self,state):
        ready=dict(state)
        ready.setdefault("documents",[])
        payload,markdown=operations.deliver_report(ready,state.get("material_original",""),state.get("cutoff",""),self.lexicon.groups)
        return {"report_payload":payload,"markdown":markdown,"skill_records":payload["skill_records"]}

    def run(self, **state) -> NoveltyState:
        if (state.get("patent_context") or {}).get("target",{}).get("claim_id") and state.get("claim_history") is None:
            raise ValueError("权利要求模式须提供claim_history，不能仅用目标标签冒充已经展开引用。")
        if state.get("claim_history") is not None:
            history=validate_history(state["claim_history"])
            features,target,sources=resolve_target(history,state.get("claim_target"))
            if state.get("features") is not None and state["features"] != features:
                raise ValueError("给定要素不属于当前权利要求版本及分支，不能复用。")
            ctx=dict(state.get("patent_context") or {})
            if ctx.get("target") and ctx["target"] != target:
                raise ValueError("已有上下文不属于当前权利要求目标。")
            ctx["target"]=target
            state.update(claim_history=history,features=features,claim_target=target["id"],claim_sources=sources,
                         patent_context=context_for(features,state["cutoff"],ctx,invention=state.get("material_original", "")))
        current = dict(state)
        stages = (self._prepare, self._expand, self._retrieve, self._locate, self._decide, self._deliver)
        if current.get("task", "novelty") != "novelty":
            stages = (self._prepare, self._decide, self._deliver)
        for stage in stages:
            current.update(stage(current))
        return current

    def run_claims(self, claim_history, *, reviews_by_target=None, **state):
        """Evaluate every explicit branch separately; never aggregate sibling limitations or decisions."""
        if any(key in state for key in ("features", "feature_text", "reviews", "expansion", "search_audit", "patent_context")):
            raise ValueError("批量权利要求比对不接收共享要素/复核/扩展；复核按目标提供 reviews_by_target。")
        history=validate_history(claim_history)
        targets=claim_targets(history)
        reviews_by_target=reviews_by_target or {}
        if set(reviews_by_target)-{t["id"] for t in targets}:
            raise ValueError("存在未知权利要求目标的复核。")
        return {"schema": "claim-precheck-v1", "selected_version":history["selected_version"],
                "results":[{"target":target,"state":self.run(claim_history=history,claim_target=target["id"],
                    reviews=reviews_by_target.get(target["id"],[]),**state)} for target in targets],
                "boundary":"逐项、逐引用分支预筛；不合并为全部权利要求均可授权的结论。"}
