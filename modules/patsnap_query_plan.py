"""Compile public concept anchors and approved aliases into bounded PatSnap queries.

This module never accepts qrels, reference identities or evaluator feedback.
Retrieval anchors select concepts; they do not replace the complete claim text.
"""
from __future__ import annotations

from datetime import date, timedelta
from itertools import combinations, zip_longest
import re

VERSION = "patsnap-concepts-v2"
GENERIC = {"一种", "装置", "设备", "系统", "方法", "模块", "单元", "包括", "所述", "连接", "其特征在于"}


def literal(term):
    english_phrase = isinstance(term, str) and bool(re.fullmatch(r'[A-Za-z0-9 /µμΩ%°+\-]+', term)) and len(term.split()) <= 8
    if (not isinstance(term, str) or not 2 <= len(term.strip()) <= (80 if english_phrase else 32)
            or term.strip() in GENERIC or re.search(r'[\r\n\t"\\]|\b(?:AND|OR|NOT)\b', term, re.I)):
        raise ValueError("智慧芽概念须是2–32字的短技术术语，不能包含查询操作符。")
    return '"' + term.strip() + '"'


def validate_anchors(raw, features):
    if not isinstance(raw, list) or len(raw) > 6 * len(features):
        raise ValueError("智慧芽 anchors 须为列表，每个要素最多6个概念。")
    by_id = {f.id: f.text for f in features}
    result = []
    seen = set()
    for row in raw:
        if (not isinstance(row, dict) or set(row) != {"feature_id", "term"}
                or not isinstance(row["feature_id"], str) or row["feature_id"] not in by_id):
            raise ValueError("智慧芽概念须绑定实际要素编号。")
        term = row["term"]
        literal(term)
        if term != term.strip() or term not in by_id[row["feature_id"]]:
            raise ValueError("原始概念须是所属要素的连续原文；同义表达另交审核。")
        key = row["feature_id"], term
        if key not in seen:
            result.append(dict(row))
            seen.add(key)
    if any(sum(r["feature_id"] == f.id for r in result) > 6 for f in features):
        raise ValueError("每个要素最多6个智慧芽概念。")
    return result


def compile_groups(groups, before=None):
    """OR only within a concept; AND between concepts. Never OR unrelated parts."""
    if not 1 <= len(groups) <= 2 or any(not group or len(group) > 4 for group in groups):
        raise ValueError("每条查询须有1–2个概念，每个概念最多4个已审核表达。")
    compiled = "TACD:(" + " AND ".join(
        "(" + " OR ".join(literal(t) for t in dict.fromkeys(group)) + ")"
        if len(set(group)) > 1 else literal(group[0]) for group in groups) + ")"
    if before:
        upper = date.fromisoformat(before) - timedelta(days=1)
        if upper.year < 1900:
            raise ValueError("当前智慧芽检索日期下界为1900年。")
        compiled += f" AND PBD:[19000101 TO {upper:%Y%m%d}]"
    return compiled


def concept_plan(features, expansion, *, budget, before=None):
    anchors = validate_anchors(expansion["generation"].get("anchors", []), features)
    accepted = [r for r in expansion["candidates"] if r["status"] == "accepted"]
    lanes = []
    blocked = []
    for feature in features:
        terms = [r["term"] for r in anchors if r["feature_id"] == feature.id]
        # A short confirmed feature is already a usable concept. Long claims need
        # explicit anchors: arbitrary character slices are not technical terms.
        if not terms and len(feature.text.strip()) <= 32:
            literal(feature.text.strip())
            sources = list(dict.fromkeys(r['source'] for r in accepted if r['feature_id'] == feature.id))
            terms = (sources + [feature.text.strip()])[:6]
        if not terms:
            raise ValueError(f"{feature.id}：长要素缺少短概念 anchors，请生成术语扩展或显式提供智慧芽查询。")
        terms = list(dict.fromkeys(terms))
        rows = [r for r in accepted if r["feature_id"] == feature.id]
        groups = []
        for term in terms:
            eligible = [r for r in rows if r["source"] == term]
            eligible = [r for turn in zip_longest([r for r in eligible if r['origin'] == 'dictionary'],
                                                   [r for r in eligible if r['origin'] == 'llm'])
                        for r in turn if r is not None]
            aliases, ids = [term], []
            for row in eligible:
                try:
                    literal(row["term"])
                except ValueError:
                    blocked.append({"candidate_id": row["id"], "reason": "已审核表达超出智慧芽短语约束"})
                    continue
                if row["term"] not in aliases and len(aliases) < 4:
                    aliases.append(row["term"])
                    ids.append(row["id"])
            groups.append((aliases, ids))
        lane = []

        def add(indices, expanded, kind):
            selected = [groups[i][0] if expanded else [terms[i]] for i in indices]
            ids = [cid for i in indices for cid in groups[i][1]] if expanded else []
            lane.append({"feature_ids": [feature.id], "concept_groups": selected,
                         "candidate_ids": ids, "kind": kind,
                         "query": compile_groups(selected, before)})

        # Each feature gets an original baseline before synonym queries. Expanded
        # pairs are prioritized so extra aliases cannot be starved by base pairs.
        add(list(range(min(2, len(terms)))), False, "original")
        for i, (_, ids) in enumerate(groups):
            if ids:
                add([i, next(j for j in range(len(terms)) if j != i)] if len(terms) > 1 else [i],
                    True, "synonym_variant")
        for pair in combinations(range(len(terms)), 2):
            add(pair, False, "concept_pair")
        for i in range(len(terms)):
            add([i], True, "focused_fallback")
        lanes.append(lane)
    queries, skipped, seen = [], [], set()
    for turn in zip_longest(*lanes):
        for row in turn:
            if row is None:
                continue
            if row["query"] in seen:
                existing = next((q for q in queries if q['query'] == row['query']), None)
                if existing:
                    existing['feature_ids'] = list(dict.fromkeys(existing['feature_ids'] + row['feature_ids']))
                    existing['candidate_ids'] = list(dict.fromkeys(existing['candidate_ids'] + row['candidate_ids']))
                continue
            seen.add(row["query"])
            if len(queries) >= budget:
                skipped.append(row | {"reason": "查询预算已用完，未发送"})
            else:
                queries.append(row | {"id": f"Q{len(queries)+1}"})
    used = {cid for row in queries for cid in row["candidate_ids"]}
    unused = [{"candidate_id": r["id"], "reason": "原词未入选概念、别名上限或查询预算限制"}
              for r in accepted if r["id"] not in used]
    return {"queries": queries, "skipped": skipped, "compiler": VERSION,
            "anchors": anchors, "unused_approved_candidates": unused, "blocked_aliases": blocked}


def fuse_candidates(candidates, query_order=None):
    """Round robin reserves early slots for independent query paths, without qrels."""
    queues = {}
    by_id = {r["publication_id"]: r for r in candidates}
    for candidate in candidates:
        for observation in candidate["observations"]:
            queues.setdefault(observation["query_id"], []).append((observation["rank"], candidate["publication_id"]))
    lists = [[pid for _, pid in sorted(queues[qid])] for qid in (query_order or queues) if qid in queues]
    ordered, seen = [], set()
    for turn in zip_longest(*lists):
        for pid in turn:
            if pid is not None and pid not in seen:
                ordered.append(by_id[pid])
                seen.add(pid)
    return ordered
