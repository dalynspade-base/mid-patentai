"""Bounded query planning and an exportable journal; lexical hints are not relevance judgments."""
from __future__ import annotations

import hashlib
import json
import re
import time
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone

from modules.novelty_matching import SynonymLexicon
from modules.novelty_models import Feature, SEARCH_PROVIDERS, candidate_source, fetch_candidate_document
from modules.patent_search import MAX_QUERY_LENGTH
from modules.synonym_expansion import PROFILES, build_expansion_plan, feature_hash
from modules.candidate_ranking import RANKING_VERSION, rank_candidates
from modules.candidate_relevance import annotate_origins
from modules.patent_network_state import PatentAccessPaused

MAX_QUERIES = 8
MAX_QUERY_BUDGET = 16
RESULTS_PER_QUERY = 5


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def search_input_hash(features: list[Feature], cutoff: str) -> str:
    raw = json.dumps({"features": [asdict(f) for f in features], "cutoff": cutoff},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def query_plan(features: list[Feature], query: str, lexicon: SynonymLexicon, expansion: dict | None = None,
               *, max_queries=None, compact=False, before=None) -> dict:
    """Each original feature gets priority over variants. Never shorten a feature."""
    query = query.strip()
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError("专利检索词超过 400 字，请拆分查询；不会静默截断。")
    if expansion is not None and expansion.get("feature_hash") != feature_hash(features):
        raise ValueError("同义词扩展与当前要素不匹配，请重新生成或审核。")
    # 重新按规则构建，不信任外部JSON中已填好的variants、状态或查询预算。
    expansion = build_expansion_plan(features, lexicon, (expansion or {}).get("profile", "theoretical"),
        (expansion or {}).get("protected_terms"), (expansion or {}).get("generation"),
        (expansion or {}).get("approved_candidate_ids"))
    budget = expansion["policy"]["query_budget"]
    if max_queries is not None:
        budget = min(budget, max_queries)
    plan = {"query_mode": "user_query" if query else "per_feature", "queries": [], "skipped": [],
            "query_budget": budget, "expansion": expansion}
    if compact and not query:
        from modules.patsnap_query_plan import concept_plan
        return plan | concept_plan(features, expansion, budget=budget, before=before)
    sources = [([], query)] if query else [([f.id], f.text) for f in features]
    seen: set[str] = set()

    def add(ids: list[str], text: str, kind: str, candidate_ids=()) -> None:
        if text in seen:
            # Same query can cover multiple explicitly identical feature inputs.
            for item in plan["queries"]:
                if item["query"] == text:
                    item["feature_ids"] = list(dict.fromkeys(item["feature_ids"] + ids))
            return
        seen.add(text)
        reason = "超过400字，未发送且未截断" if len(text) > MAX_QUERY_LENGTH and not compact else (
            f"已达到{budget}条查询预算，未发送" if len(plan["queries"]) >= budget else "")
        if reason:
            plan["skipped"].append({"feature_ids": ids, "query": text, "kind": kind, "reason": reason})
        else:
            plan["queries"].append({"id": f"Q{len(plan['queries']) + 1}", "feature_ids": ids,
                                    "query": text, "kind": kind, "candidate_ids": list(candidate_ids)})

    for ids, text in sources:
        add(ids, text, "original")
    # 自定义词保留用户指定范围，不偷偷把整套要素或LLM候选附加进去。
    if not query:
        variants = [[v for v in expansion["variants"] if v["feature_id"] == f.id] for f in features]
        for index in range(expansion["policy"]["variants_per_feature"]):
            for texts in variants:
                if index < len(texts):
                    value = texts[index]
                    add([value["feature_id"]], value["query"], value["kind"], value["candidate_ids"])
    return plan


def _terms(text: str, lexicon: SynonymLexicon) -> set[str]:
    normalized = lexicon.canonicalize(text)
    english = set(re.findall(r"[a-z0-9]+", normalized)) - {
        "a", "an", "the", "of", "to", "and", "or", "in", "on", "is", "are", "with", "has", "have", "when", "into", "by"}
    chinese = {part[i:i+2] for part in re.findall(r"[\u4e00-\u9fff]+", normalized) for i in range(len(part)-1)}
    return english | chinese


def lexical_hint(features: list[Feature], text: str, lexicon: SynonymLexicon) -> dict:
    terms = _terms(text, lexicon)
    overlap = {f.id: round(len(_terms(f.text, lexicon) & terms) / max(1, len(_terms(f.text, lexicon))), 4)
               for f in features}
    return {"score": round(sum(overlap.values()), 4), "feature_overlap": overlap,
            "meaning": "仅按标题/摘要词汇重合排序；不是相关性、披露或新颖性判断"}


def retrieve_with_journal(features: list[Feature], cutoff: str, query: str, lexicon: SynonymLexicon,
                          searcher, fetcher, max_documents: int, expansion: dict | None = None, *,
                          provider: str = "google", results_per_query: int = RESULTS_PER_QUERY,
                          max_queries=None, before=None) -> tuple[list, dict]:
    started = time.perf_counter()
    audit = {"schema_version": 1, "mode": "online", "input_hash": search_input_hash(features, cutoff),
             "provider": provider, "started_at": now(), "query_budget": MAX_QUERIES, "results_per_query": results_per_query,
             "document_budget": max_documents, "synonym_groups": deepcopy(lexicon.groups),
             **query_plan(features, query, lexicon, expansion, max_queries=max_queries,
                          compact=provider == "patsnap", before=before or cutoff),
             "candidates": []}
    candidates: dict[str, dict] = {}
    terms = {row["id"]: row["term"] for row in audit["expansion"]["candidates"]}
    sent = {}
    paused = False
    for record in audit["queries"]:
        record.update(started_at=now(), status="error", results=[], returned_count=0, error="")
        if paused:
            record.update(status="not_executed", error=f"{SEARCH_PROVIDERS[provider]} 已暂停，本条未发送。", finished_at=now())
            continue
        try:
            sent_query = record["query"]
            if provider == "patsnap":
                from modules.patsnap_mcp import patsnap_query
                sent_query = patsnap_query(sent_query, custom=audit["query_mode"] == "user_query" or "concept_groups" in record,
                                          focus=[terms[i] for i in record.get("candidate_ids", [])], before=before)
                record["sent_query"] = sent_query
                if sent_query in sent:
                    first = sent[sent_query]
                    record.update(status=first["status"], reused_query_id=first["id"], results=first["results"],
                                  returned_count=0, error=first["error"], finished_at=now())
                    continue
                sent[sent_query] = record
            raw = searcher(sent_query, results_per_query)
            if provider == "patsnap" and isinstance(raw, dict):
                record["total_matches"] = raw.get("total_matches")
                raw = raw.get("results")
            if not isinstance(raw, list):
                raise ValueError("检索器未返回文献列表。")
            record["returned_count"] = len(raw)
            for result_rank, item in enumerate(raw[:results_per_query], 1):
                if not isinstance(item, dict):
                    record["results"].append({"validation_error": "检索结果不是文献对象"})
                    continue
                result = {key: str(item.get(key) or "") for key in ("title", "url", "content", "publication_number")}
                try:
                    result["url"], pub_id = candidate_source(result, provider)
                    result["publication_id"] = pub_id
                    candidate = candidates.setdefault(pub_id, {**result, "query_ids": [],
                        "lexical_hint": lexical_hint(features, result["title"] + " " + result["content"], lexicon),
                        "fetch_status": "not_selected", "fetch_error": "", "observations": []})
                    observed_hint = lexical_hint(features, result["title"] + " " + result["content"], lexicon)
                    candidate["observations"].append({"id": f"{record['id']}:{result_rank}",
                        "query_id": record["id"], "query_kind": record["kind"], "rank": result_rank,
                        "title": result["title"], "content": result["content"], "lexical_hint": observed_hint})
                    if observed_hint["score"] > candidate["lexical_hint"]["score"]:
                        candidate["lexical_hint"] = observed_hint
                    if record["id"] not in candidate["query_ids"]:
                        candidate["query_ids"].append(record["id"])
                except ValueError as exc:
                    result["validation_error"] = str(exc)
                record["results"].append(result)
            record["status"] = "ok"
        except PatentAccessPaused as exc:
            paused = True
            audit["network_paused"] = True
            record["error"] = str(exc)
        except (RuntimeError, ValueError) as exc:
            record["error"] = str(exc)
        record["finished_at"] = now()
    # All planned queries run before any page budget can stop fetching.
    legacy = sorted(candidates.values(), key=lambda c: c["lexical_hint"]["score"], reverse=True)
    for index, candidate in enumerate(legacy, 1):
        candidate["legacy_lexical_rank"] = index
    annotate_origins(legacy, audit, features)
    ordered = rank_candidates(features, list(candidates.values()), lexicon, audit["expansion"])
    if provider == "patsnap" and audit["query_mode"] != "user_query":
        from modules.patsnap_query_plan import fuse_candidates
        ordered = fuse_candidates(list(candidates.values()), [q['id'] for q in audit['queries']])
    for index, candidate in enumerate(ordered, 1):
        candidate["rank"] = index
    audit["ranking"] = {"version": RANKING_VERSION, "mode": "rules", "metadata_reviews": {},
        "note": "组件、关系与提及语境的规则线索；未进行正文相关性或新颖性确认"}
    if provider == "patsnap" and audit["query_mode"] != "user_query":
        audit["ranking"].update(version="query-round-robin-v1", mode="query_round_robin",
                                note="按独立查询轮流取候选；规则提示仍保留，不代表正文相关性。")
    audit["candidate_order"] = [c["publication_id"] for c in ordered]
    documents = []
    for candidate in ordered[:max_documents]:
        if paused:
            candidate.update(fetch_status="not_executed", fetch_error=f"{SEARCH_PROVIDERS[provider]} 已暂停，未获取正文。")
            continue
        candidate["fetch_started_at"] = now()
        try:
            doc = fetch_candidate_document(candidate, provider, fetcher)
            documents.append(doc)
            candidate.update(fetch_status="fetched", snapshot_hash=doc.snapshot_hash)
        except PatentAccessPaused as exc:
            paused = True
            audit["network_paused"] = True
            candidate.update(fetch_status="error", fetch_error=str(exc))
        except (RuntimeError, ValueError) as exc:
            candidate.update(fetch_status="error", fetch_error=str(exc))
        candidate["fetch_finished_at"] = now()
    audit["candidates"] = ordered
    audit["finished_at"] = now()
    audit["fetched_order"] = [d.id for d in documents]
    audit["eligible_fetched_order"] = [d.id for d in documents if d.publication_date and d.publication_date < cutoff]
    audit["expansion_attribution"] = [
        {"candidate_id": row["id"], "source": row["source"], "term": row["term"], "status": row["status"],
         "planned_query_ids": [q["id"] for q in audit["queries"] if row["id"] in q.get("candidate_ids", [])],
         "sent_query_ids": [q["id"] for q in audit["queries"] if row["id"] in q.get("candidate_ids", [])
                            and q["status"] == "ok" and not q.get("reused_query_id")],
         "retrieved_publication_ids": list(dict.fromkeys(r.get("publication_id") for q in audit["queries"]
             if row["id"] in q.get("candidate_ids", []) for r in q.get("results", []) if r.get("publication_id")))}
        for row in audit["expansion"]["candidates"]]
    audit["retrieval_cost"] = {"search_calls": sum(r["status"] != "not_executed" and "reused_query_id" not in r
                                                 for r in audit["queries"]),
        "page_attempts": sum("fetch_started_at" in c for c in ordered), "successful_pages": len(documents),
        "returned_hits": sum(r["returned_count"] for r in audit["queries"]), "unique_candidates": len(ordered),
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "expansion_generation_cost": audit["expansion"]["generation"].get("cost", {})}
    return documents, audit


def snapshot_journal(features: list[Feature], cutoff: str) -> dict:
    return {"schema_version": 1, "mode": "provided_snapshots", "input_hash": search_input_hash(features, cutoff),
            "queries": [], "candidates": [], "skipped": [],
            "scope_note": "仅比对提供的快照；本轮未执行在线检索，不能推断检索覆盖。"}


def search_warnings(audit: dict | None, features: list[Feature], cutoff: str) -> list[str]:
    if audit is None:  # Direct rule callers have an explicitly bounded snapshot comparison.
        return []
    if (not isinstance(audit, dict) or audit.get("schema_version") != 1 or audit.get("input_hash") != search_input_hash(features, cutoff)
            or audit.get("mode") not in {"online", "provided_snapshots"}):
        return ["检索记录缺失绑定或与当前要素/基准日不一致，不能沿用覆盖记录。"]
    if audit["mode"] == "provided_snapshots":
        return []
    warnings = []
    queries = audit.get("queries", [])
    profile = audit.get("expansion", {}).get("profile", "theoretical")
    budget = PROFILES.get(profile, {}).get("query_budget", MAX_QUERIES)
    if (profile not in PROFILES or not queries or len(queries) > budget
            or budget > MAX_QUERY_BUDGET or audit.get("query_budget", MAX_QUERIES) != budget):
        warnings.append("在线检索缺少有效的有界查询记录。")
    expansion = audit.get("expansion", {})
    if expansion and expansion.get("feature_hash") != feature_hash(features):
        warnings.append("同义词扩展与当前要素不匹配，不能沿用。")
    if expansion.get("generation", {}).get("status") == "error":
        warnings.append("LLM扩展失败，仅使用词典；失败与成本记录已保留。")
    if any(c.get("status") == "pending" for c in expansion.get("candidates", [])):
        warnings.append("存在未审核的LLM候选，未用于查询，不能据此宣称扩展充分。")
    if audit.get("ranking", {}).get("status") == "error":
        warnings.append("候选模型判断失败，已回退规则排序；相关性仍须正文复核。")
    if audit.get("query_mode") != "per_feature":
        warnings.append("使用自定义检索词，逐要素检索覆盖尚未确认。")
    covered = set()
    for record in queries:
        if record.get("status") != "ok":
            warnings.append(f"{record['id']}检索失败：{record.get('error') or '未完成'}")
        else:
            covered.update(record.get("feature_ids", []))
            if not record.get("results"):
                warnings.append(f"{record['id']}未返回候选；空结果不证明没有相关技术。")
            if any(r.get("validation_error") for r in record.get("results", [])):
                warnings.append(f"{record['id']}含来源无效的候选，已保留记录。")
    missing = [f.id for f in features if f.id not in covered]
    if missing:
        warnings.append("以下要素没有成功发送并取得响应的独立查询：" + "、".join(missing))
    if audit.get("skipped"):
        warnings.append("存在因查询长度或预算未发送的查询，详见检索记录。")
    for candidate in audit.get("candidates", []):
        if candidate["fetch_status"] == "error":
            warnings.append(f"{candidate['publication_id']}正文获取失败：{candidate['fetch_error']}")
        elif candidate["fetch_status"] != "fetched":
            reason = candidate.get("fetch_error") or "文献预算限制"
            warnings.append(f"{candidate['publication_id']}未读取正文（{reason}），不能作为已核查。")
    if not audit.get("candidates"):
        label = SEARCH_PROVIDERS.get(audit.get("provider"), "Google Patents")
        warnings.append(f"{label} 没有返回可核对正文；不代表不存在相关现有技术。")
    return list(dict.fromkeys(warnings))
