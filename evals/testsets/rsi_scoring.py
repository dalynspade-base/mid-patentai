# -*- coding: utf-8 -*-
"""RSI v2 纯判分函数：不调用模型、不读写文件、不输出专业质量分。"""
from __future__ import annotations

import math
import re
from collections import Counter

RELEVANCE = {"related", "unrelated", "unknown"}
COVERAGE = {"full_disclosure", "partial_coverage", "local_association", "insufficient_evidence", "not_applicable"}
FEATURE_STATUS = {"disclosed", "not_found", "unknown"}
PRIVATE_KEYS = {"derived_from", "adjustment_note", "answer_key", "label", "usage", "basis", "key_gap"}


def norm_quote(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def private_key_paths(value, path="public_case") -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            current = f"{path}.{key}"
            if key in PRIVATE_KEYS:
                found.append(current)
            found.extend(private_key_paths(item, current))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(private_key_paths(item, f"{path}[{index}]"))
    return found


def validate_case(case: dict, patents: dict) -> list[str]:
    errors = [f"公开材料含答案字段：{p}" for p in private_key_paths(case.get("public_case"))]
    targets = case.get("public_case", {}).get("targets", [])
    if len(targets) != 1:
        errors.append("当前数据迁移应只有一个已核验目标；多目标须另行核验。")
    for target in targets:
        features = target.get("features", [])
        ids = [f.get("id") for f in features]
        if not ids or len(ids) != len(set(ids)):
            errors.append(f"目标 {target.get('id')} 要素为空或编号重复。")
    candidates = case.get("candidates", [])
    ids = [c.get("id") for c in candidates]
    if len(ids) != len(set(ids)):
        errors.append("候选公开号重复。")
    if not {c.get("usage") for c in candidates} == {"train", "anchor"}:
        errors.append("训练池或锚池缺失/含未知用途。")
    for cand in candidates:
        pid = cand.get("id")
        if pid not in patents:
            errors.append(f"{pid} 缺少专利快照。")
            continue
        if not patents[pid].get("sections"):
            errors.append(f"{pid} 没有原文段落。")
        for section_id, section in patents[pid].get("sections", {}).items():
            if not section.get("text", "").strip():
                errors.append(f"{pid}/{section_id} 原文段落为空。")
        label = cand.get("label", {})
        if label.get("relevance") not in {"related", "unrelated"}:
            errors.append(f"{pid} 相关性标签无效。")
        if type(label.get("prior_art_at_cutoff")) is not bool:
            errors.append(f"{pid} 日期标签不是布尔值。")
        coverage = label.get("coverage")
        if coverage not in COVERAGE:
            errors.append(f"{pid} 覆盖标签无效。")
        expected = "not_applicable" if label.get("relevance") == "unrelated" else coverage
        if coverage != expected:
            errors.append(f"{pid} 无关文献覆盖标签必须为 not_applicable。")
        statuses = label.get("feature_status", {})
        if set(statuses) - {f["id"] for t in targets for f in t["features"]}:
            errors.append(f"{pid} 含未知要素标签。")
        for fid, sections in label.get("evidence_sections", {}).items():
            if statuses.get(fid) != "disclosed":
                errors.append(f"{pid}/{fid} 有证据位置但不是 disclosed。")
            for section_id in sections:
                if section_id not in patents[pid]["sections"]:
                    errors.append(f"{pid}/{fid} 引用了不存在的 {section_id}。")
        if coverage == "full_disclosure":
            required = {f["id"] for t in targets for f in t["features"]}
            if set(statuses) != required or any(v != "disclosed" for v in statuses.values()):
                errors.append(f"{pid} 完整披露标签必须覆盖全部要素。")
        if coverage == "partial_coverage" and not (0 < list(statuses.values()).count("disclosed") < len(statuses)):
            errors.append(f"{pid} 部分覆盖必须同时包含披露和未披露要素。")
        if coverage == "local_association" and (not statuses or any(v != "not_found" for v in statuses.values())):
            errors.append(f"{pid} 局部关联应全部为 not_found。")
        if coverage == "insufficient_evidence" and patents[pid].get("evidence_scope", {}).get("complete_for_target"):
            errors.append(f"{pid} 证据不足标签与完整证据范围冲突。")
    return errors


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _string_list(value, field: str, sections: dict, errors: list[dict]) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        errors.append(_error("schema", f"{field} 必须是字符串数组。"))
        return []
    if len(value) != len(set(value)) or any(x not in sections for x in value):
        errors.append(_error("section", f"{field} 含重复或未知原文位置。"))
    return value


def validate_judgment(obj: dict, candidate: dict, target: dict) -> tuple[dict, list[dict]]:
    """校验一篇输出并派生覆盖程度；任何错误都使该篇不进入有效判断覆盖率。"""
    errors: list[dict] = []
    if not isinstance(obj, dict):
        return {}, [_error("schema", "输出不是 JSON 对象。")]
    sections = candidate.get("sections", {})
    if obj.get("document_id") != candidate.get("id"):
        errors.append(_error("identity", "document_id 与当前候选不一致。"))
    if obj.get("target_id") != target.get("id"):
        errors.append(_error("identity", "target_id 与当前目标不一致。"))
    if obj.get("status") != "completed":
        errors.append(_error("status", "status 必须为 completed；失败由运行器单独记录。"))
    relevance = obj.get("relevance")
    if relevance not in RELEVANCE:
        errors.append(_error("schema", "relevance 取值无效。"))
    if not isinstance(obj.get("relevance_basis"), str) or not obj.get("relevance_basis", "").strip():
        errors.append(_error("schema", "relevance_basis 不能为空。"))
    relevance_sections = _string_list(obj.get("relevance_section_ids"), "relevance_section_ids", sections, errors)
    if relevance in {"related", "unrelated"} and not relevance_sections:
        errors.append(_error("evidence_section", "相关性结论缺少原文位置。"))
    prior_art = obj.get("prior_art")
    if type(prior_art) is not bool:
        errors.append(_error("schema", "prior_art 必须为 JSON 布尔值。"))
    if not isinstance(obj.get("date_basis"), str) or not obj.get("date_basis", "").strip():
        errors.append(_error("schema", "date_basis 不能为空。"))
    coverage = obj.get("coverage")
    if coverage not in COVERAGE:
        errors.append(_error("schema", "coverage 取值无效。"))
    scope_complete = obj.get("evidence_scope_complete")
    if type(scope_complete) is not bool:
        errors.append(_error("schema", "evidence_scope_complete 必须为布尔值。"))
    expected_scope = candidate.get("evidence_scope", {}).get("complete_for_target")
    if expected_scope is False and scope_complete is True:
        errors.append(_error("scope_overclaim", "公开证据包标记为不足，模型不得改称完整。"))
    rows = obj.get("rows")
    features = target.get("features", [])
    feature_ids = [f["id"] for f in features]
    normalized_rows = []
    if not isinstance(rows, list):
        errors.append(_error("schema", "rows 必须为数组。"))
        rows = []
    by_feature = {}
    for row in rows:
        if not isinstance(row, dict):
            errors.append(_error("schema", "要素行必须是对象。"))
            continue
        fid = row.get("feature_id")
        if fid not in feature_ids or fid in by_feature:
            errors.append(_error("schema", f"要素编号未知或重复：{fid}。"))
            continue
        status = row.get("status")
        if status not in FEATURE_STATUS:
            errors.append(_error("schema", f"{fid} 状态无效。"))
        if not isinstance(row.get("basis"), str) or not row.get("basis", "").strip():
            errors.append(_error("schema", f"{fid} basis 不能为空。"))
        evidence = row.get("evidence")
        if not isinstance(evidence, list):
            errors.append(_error("schema", f"{fid} evidence 必须为数组。"))
            evidence = []
        normalized_evidence = []
        for item in evidence:
            if not isinstance(item, dict):
                errors.append(_error("schema", f"{fid} 证据项必须为对象。"))
                continue
            section_id, quote = item.get("section_id"), item.get("quote")
            if section_id not in sections or not isinstance(quote, str) or not quote.strip():
                errors.append(_error("evidence_section", f"{fid} 证据位置或引文为空/无效。"))
                continue
            if norm_quote(quote) not in norm_quote(sections[section_id].get("text", "")):
                errors.append(_error("evidence_quote", f"{fid} 引文不能逐字回查到 {section_id}。"))
            normalized_evidence.append({"section_id": section_id, "quote": quote})
        if status == "disclosed" and not normalized_evidence:
            errors.append(_error("evidence_missing", f"{fid} disclosed 缺少有效引文。"))
        normalized = {"feature_id": fid, "status": status, "basis": row.get("basis", ""), "evidence": normalized_evidence}
        by_feature[fid] = normalized
        normalized_rows.append(normalized)
    if set(by_feature) != set(feature_ids):
        errors.append(_error("schema", "模型遗漏目标要素。"))
    normalized_rows = [by_feature[fid] for fid in feature_ids if fid in by_feature]
    statuses = [row["status"] for row in normalized_rows]
    if relevance == "unrelated":
        derived = "not_applicable"
    elif not scope_complete or any(s == "unknown" for s in statuses) or len(statuses) != len(feature_ids):
        derived = "insufficient_evidence"
    elif statuses and all(s == "disclosed" for s in statuses):
        derived = "full_disclosure"
    elif any(s == "disclosed" for s in statuses):
        derived = "partial_coverage"
    else:
        derived = "local_association"
    if coverage != derived:
        errors.append(_error("coverage_inconsistent", f"自报 coverage={coverage} 与逐要素派生 {derived} 不一致。"))
    gaps = obj.get("key_gaps")
    if not isinstance(gaps, list) or any(not isinstance(x, str) for x in gaps):
        errors.append(_error("schema", "key_gaps 必须为字符串数组。"))
        gaps = []
    normalized_obj = dict(obj)
    normalized_obj.update({
        "rows": normalized_rows,
        "relevance_section_ids": relevance_sections,
        "key_gaps": gaps,
        "derived_coverage": derived,
        "evidence_scope_complete": scope_complete,
    })
    return normalized_obj, errors


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def score_pool(records: list[dict], ids: list[str], case: dict) -> dict:
    labels = {c["id"]: c["label"] for c in case["candidates"]}
    by_id = {}
    duplicates = set()
    for record in records:
        pid = record.get("document_id") or record.get("judgment", {}).get("document_id")
        if pid in by_id:
            duplicates.add(pid)
        by_id[pid] = record
    tp = fp = fn = tn = 0
    valid_count = 0
    judgment_correct = 0
    per_item, per_document, unjudged = {}, {}, {}
    errors = []
    error_counts = Counter()
    full_fp = full_fn = coverage_confusion = date_errors = evidence_errors = 0
    nonfull_valid = full_valid = 0
    related_total = sum(labels[cid]["relevance"] == "related" for cid in ids)
    for cid in ids:
        gold = labels[cid]
        record = by_id.get(cid)
        item_errors = []
        if not record or cid in duplicates or not record.get("valid"):
            codes = ["duplicate"] if cid in duplicates else (record or {}).get("errors", [{"code": "missing"}])
            codes = [e["code"] if isinstance(e, dict) else str(e) for e in codes] or ["invalid"]
            for code in codes:
                error_counts[code] += 1
            if any(str(code).startswith(("evidence", "scope")) for code in codes):
                evidence_errors += 1
            unjudged[cid] = codes
            per_item[cid] = False
            per_document[cid] = {"valid": False, "correct": False, "errors": codes}
            errors.append(f"未判 {cid}：{'、'.join(map(str, codes))}")
            continue
        valid_count += 1
        j = record["judgment"]
        predicted_relevance = j["relevance"]
        expected_relevance = gold["relevance"]
        if expected_relevance == "related" and predicted_relevance == "related":
            tp += 1
        elif expected_relevance == "related":
            fn += 1
            item_errors.append("相关性漏报")
        elif predicted_relevance == "related":
            fp += 1
            item_errors.append("相关性误报")
        else:
            tn += 1
        correct = predicted_relevance == expected_relevance
        predicted_coverage = j.get("derived_coverage")
        if expected_relevance == "related":
            if j["prior_art"] != gold["prior_art_at_cutoff"]:
                date_errors += 1
                item_errors.append(f"日期资格错误：应 {gold['prior_art_at_cutoff']}")
                correct = False
            expected_coverage = gold["coverage"]
            if predicted_coverage == "full_disclosure" and expected_coverage != "full_disclosure":
                full_fp += 1
                item_errors.append("完整披露误报")
                correct = False
            if expected_coverage != "full_disclosure":
                nonfull_valid += 1
            if expected_coverage == "full_disclosure":
                full_valid += 1
                if predicted_coverage != "full_disclosure":
                    full_fn += 1
                    item_errors.append("完整披露漏报")
                    correct = False
            if {expected_coverage, predicted_coverage} == {"partial_coverage", "local_association"}:
                coverage_confusion += 1
            if predicted_coverage != expected_coverage:
                item_errors.append(f"覆盖错误：应 {expected_coverage}，得 {predicted_coverage}")
                correct = False
            rows = {row["feature_id"]: row for row in j["rows"]}
            for fid, expected_status in gold.get("feature_status", {}).items():
                row = rows.get(fid, {})
                if row.get("status") != expected_status:
                    item_errors.append(f"{fid} 状态错误：应 {expected_status}，得 {row.get('status')}")
                    correct = False
                if expected_status == "disclosed" and row.get("status") == "disclosed":
                    allowed = set(gold.get("evidence_sections", {}).get(fid, []))
                    actual = {e["section_id"] for e in row.get("evidence", [])}
                    if not actual & allowed:
                        evidence_errors += 1
                        item_errors.append(f"{fid} 披露证据未落在标准证据位置 {sorted(allowed)}")
                        correct = False
            if correct:
                judgment_correct += 1
        per_item[cid] = bool(correct)
        per_document[cid] = {
            "valid": True,
            "correct": bool(correct),
            "expected": {"relevance": expected_relevance, "prior_art": gold["prior_art_at_cutoff"], "coverage": gold["coverage"]},
            "predicted": {"relevance": predicted_relevance, "prior_art": j["prior_art"], "coverage": predicted_coverage},
            "errors": item_errors,
        }
        errors.extend(f"{cid}：{message}" for message in item_errors)
    relevance = _prf(tp, fp, fn)
    coverage_rate = valid_count / len(ids) if ids else 0.0
    judgment_accuracy = judgment_correct / related_total if related_total else 0.0
    f_signal = 0.5 * relevance["f1"] + 0.5 * judgment_accuracy if valid_count == len(ids) and ids else None
    return {
        "formula_version": case["scoring"]["formula_version"],
        "total_items": len(ids),
        "valid_items": valid_count,
        "judgment_coverage": coverage_rate,
        "unjudged": unjudged,
        "candidate_relevance": relevance | {"tn": tn},
        "judgment_accuracy": judgment_accuracy,
        "judgment_correct": judgment_correct,
        "related_total": related_total,
        "f_signal": f_signal,
        "full_disclosure_false_positives": full_fp,
        "full_disclosure_false_negatives": full_fn,
        "full_false_positive_rate": {"numerator": full_fp, "denominator": nonfull_valid, "value": full_fp / nonfull_valid if nonfull_valid else None},
        "full_false_negative_rate": {"numerator": full_fn, "denominator": full_valid, "value": full_fn / full_valid if full_valid else None},
        "coverage_confusion": coverage_confusion,
        "date_errors": date_errors,
        "evidence_errors": evidence_errors,
        "error_counts": dict(error_counts),
        "errors": errors,
        "per_item": per_item,
        "per_document": per_document,
    }


def score_retrieval(returned_ids: list[str], ids: list[str], case: dict) -> dict:
    labels = {c["id"]: c["label"] for c in case["candidates"]}
    expected = {cid for cid in ids if labels[cid]["expected_retrieval_hit"]}
    returned = set(returned_ids)
    tp = len(returned & expected)
    fp = len(returned - expected)
    fn = len(expected - returned)
    return _prf(tp, fp, fn) | {
        "expected": sorted(expected),
        "returned": sorted(returned),
        "missed": sorted(expected - returned),
        "unexpected": sorted(returned - expected),
        "note": "这是独立检索结果的对账，不与固定证据包判断混算。",
    }


def acceptance(rule: str, new: dict, best_total: float | None, best_items: dict, eps: float) -> tuple[bool, str]:
    if new.get("f_signal") is None or new.get("judgment_coverage") != 1.0:
        return False, "有效判断覆盖率不足，本轮不得采纳。"
    total = new["f_signal"]
    if best_total is None:
        return True, "首个完整基线。"
    if rule == "better":
        return total > best_total, f"better：{total:.3f} vs {best_total:.3f}"
    if rule == "ineq":
        gain = total - best_total
        return gain > eps, f"ineq：增益 {gain:+.3f}，阈值 eps={eps}"
    b = sum(1 for key, value in new["per_item"].items() if value and not best_items.get(key))
    c = sum(1 for key, value in best_items.items() if value and not new["per_item"].get(key))
    n = b + c
    if n == 0:
        return False, "sign：b=c=0，无逐项变化，不采纳；统计功效不足。"
    p = sum(math.comb(n, k) for k in range(b, n + 1)) / 2 ** n
    note = f"sign：变好 b={b}，变坏 c={c}，n={n}，p={p:.3f}"
    if n < 20:
        note += "；n<20，统计功效有限"
    return p < 0.05, note
