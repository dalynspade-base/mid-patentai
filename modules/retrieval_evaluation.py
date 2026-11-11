"""Offline scorer-side loss attribution. Never imported by query planning."""
from __future__ import annotations

import hashlib
import json
import math
import re


def publication_key(value):
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def evaluate_retrieval(audit, standard, *, aliases=None):
    """Report known-qrel recall at each saved boundary; unjudged stays unjudged."""
    gold = {publication_key(d["publication_id"]): d for d in standard["documents"]}
    mapping = {publication_key(k): publication_key(v) for k, v in (aliases or {}).items()}
    if any(v not in gold for v in mapping.values()) or any(k in gold and k != v for k, v in mapping.items()):
        raise ValueError("版本映射须指向已封存参考，不能合并两个不同参考身份。")

    def ids(values):
        return list(dict.fromkeys(mapping.get(publication_key(p), publication_key(p)) for p in values if p))

    def metrics(values):
        ordered = ids(values)
        top = ordered[:25]
        known = [pid for pid in top if pid in gold]
        gains = sorted((2 ** int(d["relevance"][1:]) - 1 for d in gold.values()), reverse=True)[:10]
        idcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
        dcg = sum((2 ** int(gold[p]["relevance"][1:]) - 1) / math.log2(i + 2)
                  for i, p in enumerate(ordered[:10]) if p in gold)
        return {"unique_returned": len(ordered), "known_reference_count": len(gold),
                "known_relevant_in_pool": sum(p in gold for p in ordered),
                "known_relevant_at_25": len(known), "known_recall_at_25": len(known) / len(gold),
                "weighted_recall_at_25": sum(gold[p]["weight"] for p in known) / sum(d["weight"] for d in gold.values()),
                "ndcg_at_10_known_qrels": dcg / idcg if idcg else 0,
                "unjudged_returned": [p for p in ordered if p not in gold],
                "ordered_publication_ids": ordered}

    queries = audit.get("queries", [])
    raw = [r.get("publication_id") for q in queries for r in q.get("results", []) if isinstance(r, dict)]
    candidates = audit.get("candidates", [])
    fused = audit.get("candidate_order", [r["publication_id"] for r in candidates])
    fetched = audit.get("fetched_order", [r["publication_id"] for r in candidates if r.get("fetch_status") == "fetched"])
    eligible = audit.get("eligible_fetched_order")
    per_query = [{"query_id": q["id"], "sent_query": q.get("sent_query"), "status": q.get("status"),
                  "platform_total_matches": q.get("total_matches"),
                  **metrics([r.get("publication_id") for r in q.get("results", []) if isinstance(r, dict)])}
                 for q in queries]
    stages = {"platform_returned_pool": metrics(raw), "fused_ranking": metrics(fused),
              "fetched_bodies": metrics(fetched),
              "eligible_fetched_bodies": metrics(eligible) if eligible is not None else None}
    losses = []
    for pid in gold:
        if pid not in ids(raw): reason = "not_in_saved_platform_results"
        elif pid not in ids(fused)[:25]: reason = "fusion_or_top25_cutoff"
        elif pid not in ids(fetched): reason = "body_budget_or_fetch_failure"
        elif eligible is not None and pid not in ids(eligible): reason = "date_or_body_eligibility"
        else: reason = "retrieved_eligibility_known" if eligible is not None else "retrieved_eligibility_not_recorded"
        losses.append({"publication_id": pid, "stage": reason})
    return {"schema": "retrieval-diagnostics-v1", "scope": "offline_known_qrels_not_population_recall",
            "audit_sha256": hashlib.sha256(json.dumps(audit, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "stages": stages, "queries": per_query, "reference_losses": losses,
            "alias_policy": "explicit_verified_mapping_only", "aliases": mapping,
            "note": "平台总命中数不等于返回数；名单外为未判；未保存的阶段为null；诊断不回传给盲测提议者。"}
