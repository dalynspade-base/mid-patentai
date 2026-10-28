"""专利预筛底层状态图；公开业务入口为 skills.precheck_skill。

所有网络与模型依赖都可注入，基础 eval 可离线运行；正常运行不会使用合成案例。
自动对照只是候选；通过校验并标明来源的模型复核或历史人工复核才进入规则判断。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

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
from skills import material_preparation_skill, synonym_expansion_skill, candidate_search_skill
from skills import evidence_alignment_skill, novelty_assessment_skill


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
    data = parse_object(client.complete(system, serialized, max_tokens=6000))
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


class PrecheckGraph:
    def __init__(self, *, searcher: Callable | None = None, fetcher: Callable | None = None,
                 lexicon: SynonymLexicon | None = None, max_documents: int = 6,
                 allow_synthetic: bool = False) -> None:
        self.searcher = searcher or search_patents
        self.fetcher = fetcher or fetch_google_document
        self.lexicon = lexicon or SynonymLexicon()
        self.max_documents = max(1, min(max_documents, 20))
        self.allow_synthetic = allow_synthetic
        graph = StateGraph(NoveltyState)
        graph.add_node("prepare", self._prepare)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("expand_terms", self._expand)
        graph.add_node("locate_evidence", self._locate)
        graph.add_node("decide", self._decide)
        graph.add_node("deliver_report", self._deliver)
        graph.add_edge(START, "prepare")
        graph.add_conditional_edges("prepare", lambda s: "decide" if s["task"] != "novelty" else "expand_terms")
        graph.add_edge("expand_terms", "retrieve")
        graph.add_edge("retrieve", "locate_evidence")
        graph.add_edge("locate_evidence", "decide")
        graph.add_edge("decide", "deliver_report")
        graph.add_edge("deliver_report", END)
        # 运行记录由 UI/CLI 导出为本地 JSON；不启用外部追踪或付费服务。
        self.graph = graph.compile()

    def _prepare(self, state: NoveltyState) -> dict:
        task = state.get("task", "novelty")
        if task != "novelty":
            return {"task": task, "features": [], "trace": ["prepare"]}
        check_cutoff(state["cutoff"])
        features,record = material_preparation_skill.run(state.get("feature_text", ""),features=state.get("features"),
            original_text=state.get("material_original",""),unknowns=state.get("material_unknowns"),warnings=state.get("material_warnings"))
        if state.get("material_skill_record"):
            from modules.skill_contracts import validate_record
            candidate=validate_record(state["material_skill_record"])
            expected=[{"id":f.id,"text":f.text} for f in features]
            actual=[{"id":f["id"],"text":f["text"]} for f in candidate["output"].get("features",[])]
            if candidate["skill_id"] != "patent-material-preparation" or actual != expected:
                raise ValueError("材料技能记录与当前确认要素不匹配。")
            record=candidate
        return {"task": task, "features": features, "trace": ["prepare"], "skill_records":[record]}

    def _expand(self,state):
        plan=state.get("expansion")
        if plan is None:
            plan=state.get("search_audit",{}).get("expansion")
        if plan is not None:
            record=synonym_expansion_skill.record_plan(state["features"],self.lexicon,plan)
        else:
            # Keep the existing conservative search budget when no policy was selected.
            plan,record=synonym_expansion_skill.run(state["features"],self.lexicon,"theoretical")
        return {"expansion":plan,"skill_records":upsert_record(state.get("skill_records",[]),record)}

    def _retrieve(self, state: NoveltyState) -> dict:
        errors = list(state.get("errors", []))
        # 显式传入空快照集与未传快照不同：空集不会偷偷访问网络。
        documents,audit,record=candidate_search_skill.run(state["features"],state["cutoff"],self.lexicon,
            self.searcher,self.fetcher,self.max_documents,query=state.get("query",""),expansion=state.get("expansion"),
            documents=state.get("documents"),supplied="documents" in state,audit=state.get("search_audit"),errors=errors)
        return {"documents": documents, "search_audit": audit,
                "errors": errors, "trace": state["trace"] + ["retrieve"],
                "skill_records":upsert_record(state.get("skill_records",[]),record)}

    def _locate(self, state: NoveltyState) -> dict:
        hits,candidates,_=evidence_alignment_skill.locate(state["features"],state["documents"],self.lexicon,
            allow_synthetic=self.allow_synthetic,locator=locate_evidence)
        record=evidence_alignment_skill.record_alignment(state["features"],state["documents"],hits,candidates,
            state.get("reviews",[]),state["cutoff"])
        return {"hits": hits, "suggestions": candidates,
                "trace": state["trace"] + ["locate_evidence"],
                "skill_records":upsert_record(state.get("skill_records",[]),record)}

    def _decide(self, state: NoveltyState) -> dict:
        decision,record = novelty_assessment_skill.run(state["features"], state.get("documents", []), state.get("reviews", []),
                                  state.get("cutoff", "2000-01-01"), task=state["task"],
                                  errors=state.get("errors"), allow_synthetic=self.allow_synthetic,
                                  material_unknowns=state.get("material_unknowns"),
                                  material_warnings=state.get("material_warnings"),
                                  search_audit=state.get("search_audit"))
        increment = incremental_summary(state["features"], state.get("cutoff", "2000-01-01"),
            state.get("search_audit", {}), state.get("documents", []), state.get("reviews", []))
        return {"decision": decision, "retrieval_increment": increment, "trace": state["trace"] + ["decide"],
                "skill_records":upsert_record(state.get("skill_records",[]),record)}

    def _deliver(self,state):
        from skills.report_delivery_skill import run
        ready=dict(state)
        ready.setdefault("documents",[])
        payload,markdown=run(ready,state.get("material_original",""),state.get("cutoff",""),self.lexicon.groups)
        return {"report_payload":payload,"markdown":markdown,"skill_records":payload["skill_records"]}

    def run(self, **state) -> NoveltyState:
        return self.graph.invoke(state, config={"recursion_limit": 12})

    def refresh(self, state: NoveltyState, *, documents=None, reviews=None, search_audit=None) -> NoveltyState:
        """Recompute evidence/decision without replacing the frozen upstream inputs."""
        current = dict(state)
        if documents is not None:
            current["documents"] = documents
        if reviews is not None:
            current["reviews"] = reviews
        if search_audit is not None:
            current["search_audit"] = search_audit
        if not current.get("material_skill_record"):
            current["material_skill_record"] = next(
                (r for r in state.get("skill_records", [])
                 if r["skill_id"] == "patent-material-preparation"), None,
            )
        return self.run(**current)
