"""Explicit, versioned claim dependencies; no inference of omitted limitations or legal validity."""
from __future__ import annotations

from copy import deepcopy
from datetime import date

from modules.novelty_models import Feature
from modules.skill_contracts import digest


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}须为非空文本。")
    return value.strip()


def validate_history(value):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        raise ValueError("权利要求历史须使用 schema_version=1。")
    history = deepcopy(value)
    versions = history.get("versions")
    if not isinstance(versions, list) or not versions:
        raise ValueError("至少提供一个权利要求版本。")
    seen, previous_date, previous_claims = set(), None, set()
    for version in versions:
        if not isinstance(version, dict):
            raise ValueError("权利要求版本须为对象。")
        vid = _text(version.get("id"), "版本编号")
        if vid in seen:
            raise ValueError("权利要求版本编号重复。")
        seen.add(vid)
        current_date = date.fromisoformat(_text(version.get("date"), "版本日期"))
        if previous_date and current_date < previous_date:
            raise ValueError("修改历史须按日期先后排列。")
        previous_date = current_date
        _text(version.get("stage"), "版本阶段")
        claims = version.get("claims")
        if not isinstance(claims, list) or not claims:
            raise ValueError("每个版本须有权利要求。")
        by_id = {}
        for claim in claims:
            if not isinstance(claim, dict):
                raise ValueError("权利要求须为对象。")
            cid = _text(claim.get("id"), "权利要求编号")
            if cid in by_id:
                raise ValueError("同一版本的权利要求编号重复。")
            by_id[cid] = claim
            if claim.get("previous_claim_id") is not None and claim["previous_claim_id"] not in previous_claims:
                raise ValueError("重新编号须引用紧邻前一版本实际存在的权利要求。")
            text = _text(claim.get("text"), "权利要求原文")
            parents = claim.get("depends_on", [])
            if (not isinstance(parents, list) or any(not isinstance(p, str) or not p for p in parents)
                    or len(set(parents)) != len(parents)):
                raise ValueError("引用项须为不重复的权利要求编号列表。")
            kind = claim.get("kind")
            if kind not in {"independent", "dependent"} or (kind == "independent") != (not parents):
                raise ValueError("独立/从属类型与引用关系不一致。")
            # Chinese multiple dependencies are alternatives, never a conjunction of siblings.
            if len(parents) > 1 and claim.get("dependency_mode") != "any":
                raise ValueError("多项引用须明确 dependency_mode=any，分别展开，不合并兄弟项。")
            if claim.get("dependency_mode", "any") != "any":
                raise ValueError("仅支持明确的择一引用。")
            limitations = claim.get("limitations")
            if (not isinstance(limitations, list) or not limitations
                    or any(not isinstance(t, str) or not t.strip() or t not in text for t in limitations)):
                raise ValueError("本项新增限定 limitations 须逐字来自该项原文；完整性仍需语义复核。")
            claim["depends_on"] = parents
            claim["dependency_mode"] = "any"
        visiting, done = set(), set()

        def visit(cid):
            if cid not in by_id:
                raise ValueError("引用了当前版本不存在的权利要求。")
            if cid in visiting:
                raise ValueError("权利要求引用形成循环。")
            if cid in done:
                return
            visiting.add(cid)
            for parent in by_id[cid]["depends_on"]:
                visit(parent)
            visiting.remove(cid)
            done.add(cid)

        for cid in by_id:
            visit(cid)
        previous_claims=set(by_id)
        version.setdefault("amendment_basis", [])
        if not isinstance(version["amendment_basis"], list):
            raise ValueError("修改依据须为列表。")
        for basis in version["amendment_basis"]:
            if not isinstance(basis, dict) or basis.get("claim_id") not in by_id:
                raise ValueError("修改依据引用了不存在的权利要求。")
            for key in ("source_id", "quote", "explanation"):
                _text(basis.get(key), "修改依据 " + key)
    if history.get("selected_version") not in seen:
        raise ValueError("请选择历史中存在的权利要求版本。")
    sources = history.setdefault("sources", [])
    if not isinstance(sources, list):
        raise ValueError("原始材料 sources 须为列表。")
    source_ids = set()
    version_order = {v["id"]: i for i, v in enumerate(versions)}
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("修改依据来源须为对象。")
        sid = _text(source.get("id"), "来源编号")
        _text(source.get("text"), "来源原文")
        _text(source.get("location"), "来源位置")
        if sid in source_ids or sid.startswith(("skill_","claim:")) or source.get("version_id") not in seen:
            raise ValueError("来源编号重复或缺少所属版本。")
        source_ids.add(sid)
    by_source = {s["id"]: s for s in sources}
    for index, version in enumerate(versions):
        for basis in version["amendment_basis"]:
            source = by_source.get(basis["source_id"])
            if (source is None or basis["quote"] not in source["text"]
                    or version_order[source["version_id"]] >= index):
                raise ValueError("修改依据须逐字回查更早版本的原始材料，不能用修改后的文字自证。")
    return history


def claim_targets(value):
    history = validate_history(value)
    version = next(v for v in history["versions"] if v["id"] == history["selected_version"])
    by_id = {c["id"]: c for c in version["claims"]}
    memo = {}

    def paths(cid):
        if cid not in memo:
            parents = by_id[cid]["depends_on"]
            memo[cid] = [p + [cid] for parent in parents for p in paths(parent)] if parents else [[cid]]
        return memo[cid]

    result = []
    for claim in version["claims"]:
        for path in paths(claim["id"]):
            key = "/".join(path)
            result.append({"id": key, "version_id": version["id"], "claim_id": claim["id"],
                           "path": path, "kind": claim["kind"]})
    return result


def resolve_target(value, target_id=None):
    history = validate_history(value)
    targets = claim_targets(history)
    if target_id is None and len(targets) != 1:
        raise ValueError("存在多个权利要求或引用分支；须明确 target_id，批量比对使用 run_claims。")
    selected = next((t for t in targets if t["id"] == target_id), None) if target_id else targets[0]
    if selected is None:
        raise ValueError("权利要求比对目标不存在。")
    version = next(v for v in history["versions"] if v["id"] == selected["version_id"])
    by_id = {c["id"]: c for c in version["claims"]}
    binding = digest(history)
    features, owners = [], []
    for cid in selected["path"]:
        for index, text in enumerate(by_id[cid]["limitations"], 1):
            fid = f"{version['id']}:{selected['id']}:{cid}.L{index}"
            features.append(Feature(fid, text))
            owners.append({"feature_id": fid, "claim_id": cid, "quote": text})
    changes = []
    previous = {}
    for item in history["versions"]:
        current = {c["id"]: c for c in item["claims"]}
        if previous:
            for cid in selected["path"]:
                claim = current.get(cid)
                old_id = claim.get("previous_claim_id", cid) if claim else cid
                old = previous.get(old_id)
                if claim != old:
                    bases = [b for b in item["amendment_basis"] if b["claim_id"] == cid]
                    changes.append({"version_id": item["id"], "claim_id": cid,
                        "before": old, "after": claim, "basis": bases,
                        "support_status": "basis_provided_not_legal_confirmation" if bases else "basis_not_provided"})
        previous = current
        if item["id"] == selected["version_id"]:
            break
    target = selected | {"history_hash": binding, "claims": [by_id[cid] for cid in selected["path"]],
                         "feature_origins": owners, "amendments": changes,
                         "boundary": "已展开明确引用；不自动推断遗漏限定、引用合法性或修改是否超范围。"}
    used = {b["source_id"] for c in changes for b in c["basis"]}
    sources = [{"id": f"claim:{version['id']}:{cid}", "text": by_id[cid]["text"],
                "location": f"{version['stage']} {version['date']} 权利要求{cid}"} for cid in selected["path"]]
    sources += [{k: s[k] for k in ("id", "text", "location")} for s in history["sources"] if s["id"] in used]
    return features, target, sources
