"""历史数值检索工程基线，仅保留Python兼容；当前业务验收用skills.semantic_eval_skill。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from statistics import mean

from modules.novelty_matching import SynonymLexicon
from modules.novelty_models import Feature, google_patent_url
from modules.novelty_search import lexical_hint, query_plan
from modules.synonym_expansion import PROFILES, build_expansion_plan, candidate_id
from modules.candidate_ranking import RANKING_VERSION, rank_candidates

ARMS = ("original", "dictionary", "llm_raw", "llm_filtered")


def relevance_input_hash(case: dict) -> str:
    """相关性标签绑定实际题目和完整固定池，不把参考标签泄漏给查询规划器。"""
    raw = json.dumps({"features": case["features"], "documents": case["documents"],
                      "scope": case.get("scope", "frozen_pool"), "cutoff": case.get("cutoff")},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def case_plans(case: dict, lexicon: SynonymLexicon) -> dict:
    features = [Feature(**f) for f in case["features"]]
    profile = case["profile"]
    protected = case.get("protected_terms", [])
    generated = {"status": "ok", "candidates": case.get("llm_candidates", []),
                 "cost": case.get("generation_cost", {"llm_calls": None}),
                 "provenance": case.get("candidate_provenance", "frozen_simulated_candidates")}
    approved = [candidate_id(value) for value in case.get("approved_aliases", [])]
    dictionary = build_expansion_plan(features, lexicon, profile, protected)
    filtered = build_expansion_plan(features, lexicon, profile, protected, generated, approved)
    budget = PROFILES[profile]["query_budget"]
    original = list(dict.fromkeys(f.text for f in features if len(f.text) <= 400))[:budget]
    # raw仅为离线诊断，刻意绕过语义审核以量化漂移损失；不连接Google或正式流程。
    raw = list(original)
    by_id = {f.id: f.text for f in features}
    for value in case.get("llm_candidates", []):
        source = by_id[value["feature_id"]]
        if value["source"] in source:
            text = source.replace(value["source"], value["term"])
            if text not in raw and len(text) <= 400:
                raw.append(text)
    raw += [v["query"] for v in dictionary["variants"] if v["query"] not in raw]
    return {"features": features, "expansion": filtered, "budget": budget, "queries": {
        "original": original, "dictionary": [q["query"] for q in query_plan(features, "", lexicon, dictionary)["queries"]],
        "llm_raw": raw[:budget], "llm_filtered": [q["query"] for q in query_plan(features, "", lexicon, filtered)["queries"]]}}


def retrieval_metrics(ranked_ids: list[str], relevant: set[str], judged: set[str], k: int,
                       *, complete: bool = True) -> dict:
    if type(k) is not int or k < 1 or len(ranked_ids) != len(set(ranked_ids)):
        raise ValueError("K须为正整数，排序文献编号不得重复。")
    top = ranked_ids[:k]
    missing = set(top) - judged
    valid = complete and not missing and relevant <= judged
    hits = len(set(top) & relevant)
    return {"recall_at_k": hits / len(relevant) if valid and relevant else None,
        "precision_at_k": hits / k if valid else None, "relevant_at_k": hits,
        "returned_at_k": len(top), "reference_relevant_count": len(relevant), "unjudged_ids": sorted(missing),
        "scored": valid and bool(relevant), "note": "少于K条时Precision分母仍为K；Recall只针对固定已标注池"}


def run_synonym_evaluation(dataset: dict, k: int = 5, *, ranker: str = "relation") -> dict:
    if ranker not in {"relation", "legacy"}:
        raise ValueError("排序器须为relation或legacy。")
    if dataset.get("schema_version") != 1 or not isinstance(dataset.get("cases"), list) or not dataset["cases"]:
        raise ValueError("扩展评测需要第1版非空案例集。")
    if dataset.get("provenance") not in {"synthetic_teaching_fixture", "google_patents_frozen_pool"}:
        raise ValueError("须明确合成题或Google Patents固定检索池的来源。")
    if dataset["provenance"] == "google_patents_frozen_pool" and dataset.get("reference_author_type") != "human":
        raise ValueError("真实评估需独立人工相关性标签，不能用待测模型自评分。")
    lexicon = SynonymLexicon(dataset.get("synonym_groups"))
    rows = []
    seen = set()
    for case in dataset["cases"]:
        if case["id"] in seen:
            raise ValueError("评测案例编号重复。")
        seen.add(case["id"])
        plans = case_plans(case, lexicon)
        docs = {d["id"]: d for d in case["documents"]}
        if len(docs) != len(case["documents"]):
            raise ValueError("固定池文献编号重复。")
        if dataset["provenance"] == "google_patents_frozen_pool":
            reference = case.get("reference", {})
            try:
                valid_time = datetime.fromisoformat(reference.get("reviewed_at", "")).utcoffset() is not None
            except (ValueError, TypeError):
                valid_time = False
            if (reference.get("author_type") != "human" or not valid_time
                    or any(not isinstance(reference.get(key), str) or not reference[key].strip() for key in ("reviewer", "basis"))
                    or reference.get("input_hash") != relevance_input_hash(case)):
                raise ValueError("真实相关性参考缺少独立人工来源、时间、说明或有效的固定池输入绑定。")
            for doc_id, document in docs.items():
                url = google_patent_url(document.get("url", ""))
                if url.split("/patent/", 1)[1].split("/")[0] != doc_id:
                    raise ValueError("真实固定池文献编号与Google Patents来源不一致。")
        relevant, judged = set(case["relevant_ids"]), set(case["judged_ids"])
        if not relevant <= judged or not judged <= set(docs):
            raise ValueError("相关性标签必须来自固定文献池且已标注。")
        arms = {}
        original_records = [case["results_by_query"].get(q) for q in plans["queries"]["original"]]
        baseline_complete = all(r is not None and r.get("status") == "ok" for r in original_records)
        original_ids = {key for r in original_records if r and r.get("status") == "ok" for key in r.get("ids", [])}
        for arm in ARMS:
            queries = plans["queries"][arm]
            missing_queries = []
            pool = {}
            http_counts = []
            for query in queries:
                record = case["results_by_query"].get(query)
                if record is None or record.get("status") != "ok":
                    missing_queries.append(query)
                    continue
                ids = record.get("ids")
                if (not isinstance(ids, list) or any(not isinstance(i, str) or i not in docs for i in ids)
                        or len(ids) > 5 or len(ids) != len(set(ids))):
                    raise ValueError("缓存每查询最多5篇、编号唯一且须存在于固定池。")
                http_counts.append(record.get("http_requests"))
                for doc_id in ids:
                    # 与正式检索相同：按公开号去重，按要素与标题/摘要的词汇提示排序。
                    hint = lexical_hint(plans["features"], docs[doc_id].get("title", "")
                                                       + " " + docs[doc_id].get("content", ""), lexicon)
                    pool.setdefault(doc_id, {"publication_id": doc_id, "title": docs[doc_id].get("title", ""),
                        "content": docs[doc_id].get("content", ""), "lexical_hint": hint})
            ordered = (rank_candidates(plans["features"], list(pool.values()), lexicon, plans["expansion"])
                       if ranker == "relation" else sorted(pool.values(), key=lambda c: c["lexical_hint"]["score"], reverse=True))
            ranked = [c["publication_id"] for c in ordered]
            metrics = retrieval_metrics(ranked, relevant, judged, k,
                complete=case.get("reference_complete") is True and judged == set(docs) and not missing_queries)
            arms[arm] = {"queries": queries, "ranked_ids": ranked[:k], "metrics": metrics,
                "ranking_details": [{"publication_id": c["publication_id"], "lexical_hint": c["lexical_hint"],
                                     "ranking_hint": c.get("ranking_hint")} for c in ordered],
                "increment": {"baseline_complete": baseline_complete, "new_candidate_ids": sorted(set(pool) - original_ids),
                    "new_relevant_ids": sorted((set(pool) - original_ids) & relevant) if metrics["precision_at_k"] is not None and baseline_complete else None,
                    "new_relevant_at_k_ids": sorted((set(ranked[:k]) - original_ids) & relevant) if metrics["precision_at_k"] is not None and baseline_complete else None,
                    "new_irrelevant_ids": sorted((set(pool) - original_ids) - relevant) if metrics["precision_at_k"] is not None and baseline_complete else None},
                "missing_or_failed_queries": missing_queries,
                "cost": {"planned_search_queries": len(queries), "recorded_queries": len(queries)-len(missing_queries),
                         "recorded_search_http_requests": sum(http_counts) if http_counts and all(type(n) is int and n >= 0 for n in http_counts) else None,
                         "unique_candidates": len(pool), "live_requests_in_this_eval": 0,
                         "generation_cost": (case.get("generation_cost", {"llm_calls": None}) if arm.startswith("llm") else {"llm_calls": 0})}}
        rows.append({"id": case["id"], "family_id": case.get("family_id", case["id"]), "profile": case["profile"],
                     "same_query_cap": plans["budget"], "expansion": plans["expansion"], "arms": arms})
    def compare(selected):
        comparisons = {}
        for baseline in ("original", "dictionary"):
            paired = [r for r in selected if r["arms"][baseline]["metrics"]["precision_at_k"] is not None and
                      r["arms"]["llm_filtered"]["metrics"]["precision_at_k"] is not None]
            metric_pairs = {metric: [r for r in paired if r["arms"][baseline]["metrics"][metric] is not None
                                    and r["arms"]["llm_filtered"]["metrics"][metric] is not None]
                            for metric in ("recall_at_k", "precision_at_k")}
            comparisons[baseline + "_to_filtered"] = {"paired_cases": len(paired),
                "paired_families": len({r["family_id"] for r in paired}),
                "metric_pair_counts": {metric: len(values) for metric, values in metric_pairs.items()},
            **{metric + "_delta": mean(r["arms"]["llm_filtered"]["metrics"][metric] - r["arms"][baseline]["metrics"][metric]
                                       for r in metric_pairs[metric]) if metric_pairs[metric] else None
               for metric in ("recall_at_k", "precision_at_k")},
            "mean_search_query_delta": mean(r["arms"]["llm_filtered"]["cost"]["planned_search_queries"] -
                                            r["arms"][baseline]["cost"]["planned_search_queries"] for r in paired) if paired else None,
                "precision_regression_cases": [r["id"] for r in paired if r["arms"]["llm_filtered"]["metrics"]["precision_at_k"] <
                                                r["arms"][baseline]["metrics"]["precision_at_k"]],
                "note": "同一题、K、排序器及最大查询预算；零相关题的Recall不可计算但Precision参与配对；实际查询数单列，不把未评题计零"}
        return comparisons
    comparisons = compare(rows)
    return {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "k": k,
        "provenance": dataset["provenance"], "dataset_id": dataset.get("dataset_id"), "comparisons": comparisons,
        "ranker": RANKING_VERSION if ranker == "relation" else "legacy-lexical-v1",
        "comparisons_by_profile": {profile: compare([r for r in rows if r["profile"] == profile])
                                   for profile in PROFILES if any(r["profile"] == profile for r in rows)},
        "quality_evidence": ("synthetic_demonstration_only" if dataset["provenance"] == "synthetic_teaching_fixture" else
                             "independent_reference_on_bounded_frozen_pool_not_global_recall"),
        "cases": rows, "limits": ["合成示例只验证流程，不证明Google Patents召回改善。",
            "真实Recall限于固定已标注池，不能解释为全库召回率。", "未记录查询、失败或未标注TopK不计算质量指标。",
            "固定词典、候选和标签须按专利族分离开发/盲测；不能用同一模型生成标签并自证改进。",
            "LLM及HTTP成本只记录实际提供的用量；未提供的不填零，不虚构货币费用。"]}


def evaluation_markdown(report: dict) -> str:
    lines = ["# 同义词扩展成对检索评测", "", f"来源：{report['provenance']}；K={report['k']}", ""]
    if report["provenance"] == "synthetic_teaching_fixture":
        lines += ["本报告使用作者编写的模拟候选与冻结结果，只验证工程流程，不证明Google真实检索提升。", ""]
    lines += [f"排序器：{report.get('ranker', '历史未记录')}", "",
             "|案例|类型|方案|Recall@K|Precision@K|计划查询数|独立候选数|新增相关文献|LLM调用|缓存HTTP次数|",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in report["cases"]:
        for arm, value in row["arms"].items():
            m = value["metrics"]
            fmt = lambda v: "不可计算" if v is None else f"{v:.3f}"
            label = {"original": "原词", "dictionary": "词典", "llm_raw": "原始LLM候选", "llm_filtered": "审核后扩展"}[arm]
            lines.append(f"|{row['id']}|{'理论类' if row['profile'] == 'theoretical' else '工程类'}|{label}|{fmt(m['recall_at_k'])}|{fmt(m['precision_at_k'])}|"
                         f"{value['cost']['planned_search_queries']}|{value['cost']['unique_candidates']}|"
                         f"{len(value.get('increment', {}).get('new_relevant_ids')) if value.get('increment', {}).get('new_relevant_ids') is not None else '不可计算'}|"
                         f"{value['cost']['generation_cost'].get('llm_calls', '未记录')}|"
                         f"{value['cost']['recorded_search_http_requests'] if value['cost']['recorded_search_http_requests'] is not None else '未记录'}|")
    lines += ["", "## 扩展前后比较", ""]
    delta = lambda value: "不可计算" if value is None else f"{value:+.3f}"
    for name, values in report["comparisons"].items():
        label = "原词 → 审核后扩展" if name.startswith("original") else "词典 → 审核后扩展"
        lines += [f"- {label}：配对题数={values['paired_cases']}；Recall变化={delta(values['recall_at_k_delta'])}；"
                  f"Precision变化={delta(values['precision_at_k_delta'])}；平均新增查询={delta(values['mean_search_query_delta'])}"]
        lines += [f"  Precision变差案例：{', '.join(values['precision_regression_cases']) or '本固定池未见'}"]
    lines += ["", "## 按类型比较", "", "|类型|对照|配对题|Recall变化|Precision变化|平均新增查询|",
              "|---|---|---:|---:|---:|---:|"]
    for profile, comparisons in report["comparisons_by_profile"].items():
        for name, value in comparisons.items():
            label = "原词→审核后" if name.startswith("original") else "词典→审核后"
            lines += [f"|{'理论类' if profile == 'theoretical' else '工程类'}|{label}|{value['paired_cases']}|"
                      f"{delta(value['recall_at_k_delta'])}|{delta(value['precision_at_k_delta'])}|{delta(value['mean_search_query_delta'])}|"]
    lines += ["", "## 成本与边界", "",
              "JSON逐题保存LLM调用/用量、缓存HTTP次数、查询列表与未记录项；本轮只读缓存，不发网络请求。"]
    lines += [f"- {value}" for value in report["limits"]]
    return "\n".join(lines) + "\n"


def ranking_comparison(dataset: dict, k: int = 3) -> dict:
    """只改变排序器，固定查询、池与标签，防止把改查询的收益算到排序上。"""
    before = run_synonym_evaluation(dataset, k, ranker="legacy")
    after = run_synonym_evaluation(dataset, k, ranker="relation")
    rows = []
    for old, new in zip(before["cases"], after["cases"]):
        for arm in ARMS:
            a, b = old["arms"][arm], new["arms"][arm]
            if a["queries"] != b["queries"] or a["cost"] != b["cost"]:
                raise ValueError("排序对照的查询或成本发生变化，不能作为纯排序对照。")
            rows.append({"case_id": old["id"], "profile": old["profile"], "arm": arm,
                "before": {"ranked_ids": a["ranked_ids"], **a["metrics"]},
                "after": {"ranked_ids": b["ranked_ids"], **b["metrics"]},
                "increment": b["increment"], "same_queries_and_cost": True})
    return {"k": k, "provenance": dataset["provenance"], "before_ranker": before["ranker"],
            "after_ranker": after["ranker"], "rows": rows,
            "limits": ["固定合成池的排序修复不代表真实Google检索效果。", "排序改善不会自动创造新增相关文献，新增收益单独统计。"]}


def ranking_comparison_markdown(report):
    lines = ["# 候选排序前后对照", "", f"K={report['k']}；来源={report['provenance']}。查询、候选池、标签和成本固定，只改变排序。", "",
        "|案例|方案|旧Recall|新Recall|旧Precision|新Precision|新增相关文献|", "|---|---|---:|---:|---:|---:|---:|"]
    fmt = lambda value: "不可计算" if value is None else f"{value:.3f}"
    for row in report["rows"]:
        a, b = row["before"], row["after"]
        extra = row["increment"]["new_relevant_ids"]
        lines.append(f"|{row['case_id']}|{row['arm']}|{fmt(a['recall_at_k'])}|{fmt(b['recall_at_k'])}|"
            f"{fmt(a['precision_at_k'])}|{fmt(b['precision_at_k'])}|{len(extra) if extra is not None else '不可计算'}|")
    lines += ["", *[f"- {value}" for value in report["limits"]]]
    return "\n".join(lines) + "\n"
