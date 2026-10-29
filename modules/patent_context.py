"""Compact task boundaries and per-stage context contracts shared by producers and judges."""
from __future__ import annotations

from copy import deepcopy

from modules.skill_contracts import DIMENSIONS, SKILLS, digest, make_record, plain

CONTEXT_VERSION = "patent-context-v1"
CONTRACTS = {
    "patent-material-preparation": {
        "needs": ["原始材料或明确的用户确认要素", "实际读取范围、未知项", "采用权利要求模式时的原文、版本与引用路径"],
        "check": "从输出回查来源，再从原文反查遗漏；保留关系、条件、否定及继承限定。说明书用于解释，不擅自导入未要求的实施例细节。",
        "boundary": "不补造实现；不把附件存在称为已经读取；不自行确认修改合法性。"},
    "patent-synonym-expansion": {
        "needs": ["确认要素及其完整关系", "发明描述、技术领域、专门定义（有则提供）", "保护术语、扩展词的关系与用途"],
        "check": "将候选放回原关系中比较对象、范围和机制；等义、上下位、相关分别处理；检索有用不等于可替换。",
        "boundary": "只扩展检索表达，不改写权利要求，不判技术披露或法律等同。"},
    "patent-candidate-search": {
        "needs": ["当前要素、基准日与来源范围", "实际查询、扩展来源、获取失败和未读记录"],
        "check": "查询是否表达关键关系；同名提及与相关披露分开，元数据线索与正文分开；说明实际范围，不推定全网覆盖。",
        "boundary": "返回候选不证明已披露；未找到、未读取或失败不证明不存在。"},
    "patent-evidence-alignment": {
        "needs": ["当前完整限定、基准日及目标版本", "该篇原文、定义、相邻语境和交叉引用", "方案归属、组合依据及必要附图的取得情况"],
        "check": "逐项核验全部限定及限定间关系；引文真实还须语义支持；检查全文矛盾和互斥实施例。跨段组合须有文献自身依据。",
        "boundary": "逐篇对照，不跨文献拼接；明确披露、必然隐含和仅相容分开；不推测示意图尺寸。"},
    "patent-novelty-assessment": {
        "needs": ["目标版本和完整限定集", "有效逐项复核、日期链与检索缺口", "法域、任务及适用规则说明"],
        "check": "核验单篇文献同一披露方案是否支持完整目标；分支分别判断；检查证据资格、范围及未知，不以相同功能补足结构。",
        "boundary": "仅新颖性预筛；创造性、公开充分性、支持、超范围等底线分别提示，不替代新颖性或承诺授权。"},
    "patent-report-delivery": {
        "needs": ["最终结构化业务结果与正文", "基准日、范围、目标版本及修改摘要", "来源、未知项与实际执行记录"],
        "check": "双向核对结构化结果和正文，保留条件、缺口、意见来源与结论边界；不把局部结论扩展到其他权利要求或版本。",
        "boundary": "评交付内容是否忠实；保存或下载成功不作为专业验收，评估附录不递归评估自身。"},
}
CONDITIONAL_DIMENSIONS = {
    "functions_effects": "存在功能/结果限定、作用机制或效果主张时必查；由相同效果推定相同手段须判错误。",
    "structure_relations": "输出涉及对象间关系、依赖、过程或组合时必查。",
    "evaluation_expression": "出现评价性表达、确定性或结论强度变化时必查；含技术限定的修饰不能直接删除。",
}
RULE_PROFILE = {
    "id": "CN-novelty-precheck-v1", "jurisdiction": "CN", "task": "novelty",
    "basis": [
        {"title": "专利法第二十二条；新颖性与创造性分别判断", "url": "https://www.cnipa.gov.cn/art/2020/11/23/art_524_171347.html"},
        {"title": "专利审查指南；局令第84号修订自2026-01-01施行，历史案件须核验适用版本",
         "url": "https://www.cnipa.gov.cn/art/2025/11/14/art_99_202568.html"}],
    "rules": ["单篇文献分别对比，不跨文献或互斥方案拼接。", "完整限定及其组合须有直接、明确或必然隐含的支持。",
              "公开日期及可能的抵触申请分开核验；不自动认定优先权有效。",
              "充分公开、日期或语境不能确定时保留未知；未发现不等于可授权。"],
    "limits": "固定为现有CN预筛实现的可核验规则，不声称覆盖全部法定例外；历史规则冲突及修改合法性转专业复核。",
}
PREDECESSORS = {
    "patent-material-preparation": (),
    "patent-synonym-expansion": ("patent-material-preparation",),
    "patent-candidate-search": ("patent-material-preparation", "patent-synonym-expansion"),
    "patent-evidence-alignment": ("patent-material-preparation", "patent-candidate-search"),
    "patent-novelty-assessment": ("patent-material-preparation", "patent-candidate-search", "patent-evidence-alignment"),
    "patent-report-delivery": ("patent-material-preparation", "patent-novelty-assessment"),
}


def context_for(features, cutoff="", context=None, *, invention=""):
    features = plain(features)
    if context is not None and not isinstance(context,dict):
        raise ValueError("专利上下文须为对象。")
    result = deepcopy(context or {})
    allowed={"schema","features_hash","cutoff","invention","technical_field","semantic_focus","target","scope",
             "rule_profile","baseline_topics","definitions","reading_scope","context_notes"}
    if set(result)-allowed:
        raise ValueError("上下文含未知字段；质量评分不进入上下文。")
    if result.get("schema", CONTEXT_VERSION) != CONTEXT_VERSION:
        raise ValueError("未知的专利上下文版本。")
    if cutoff:
        from modules.novelty_models import check_cutoff
        check_cutoff(cutoff)
        if result.get("cutoff") and result["cutoff"] != cutoff:
            raise ValueError("上下文与本轮基准日不同，须重建上下文。")
    expected = digest(features)
    if result.get("features_hash") and result["features_hash"] != expected:
        raise ValueError("上下文与当前技术限定不同，须重新确认。")
    focus = result.get("semantic_focus", [])
    if not isinstance(focus, list) or any(not isinstance(d,str) or d not in DIMENSIONS for d in focus) or len(set(focus))!=len(focus):
        raise ValueError("动态必查维度无效。")
    result.update(schema=CONTEXT_VERSION, features_hash=expected, cutoff=cutoff or result.get("cutoff", ""))
    result.setdefault("invention", invention)
    if invention:
        result["invention"] = invention
    result.setdefault("technical_field", "未指定")
    if not isinstance(result["technical_field"],str) or not isinstance(result["invention"],str):
        raise ValueError("发明描述和技术领域须为文本。")
    result.setdefault("semantic_focus", [])
    result.setdefault("target", {"kind": "confirmed_features", "boundary": "整套用户确认要素，不推断遗漏限定。"})
    target=result["target"]
    if not isinstance(target,dict):
        raise ValueError("目标须为确认要素或已解析的权利要求对象。")
    if target.get("claim_id") and (not all(target.get(k) for k in ("id","version_id","path","claims","history_hash"))
                                   or target["path"][-1] != target["claim_id"]):
        raise ValueError("权利要求上下文不完整，须从明确的修改历史解析。")
    scope = {"jurisdiction": "CN", "task": "novelty", "sources": "Google Patents",
             "target_kind": "claim" if result["target"].get("claim_id") else "confirmed_features"}
    if result.get("scope") and result["scope"] != scope:
        raise ValueError("上下文超出当前CN新颖性及Google Patents范围。")
    result["scope"] = scope
    if result.get("rule_profile") and result["rule_profile"] != RULE_PROFILE:
        raise ValueError("当前接口不支持自行替换CN预筛规则；历史适用规则存疑须保留未知。")
    result["rule_profile"]=deepcopy(RULE_PROFILE)
    result.setdefault("baseline_topics", ["general"])
    from modules.application_baselines import select_baselines
    select_baselines(result["baseline_topics"])
    return plain(result)


def stage_record(skill_id, inputs, output, *, features=(), cutoff="", context=None, upstream=(), sources=(), **kwargs):
    ctx = context_for(features, cutoff, context)
    by_id = {r["skill_id"]: r for r in upstream}
    links = [{"skill_id": key, "record_hash": by_id[key]["record_hash"]}
             for key in PREDECESSORS[skill_id] if key in by_id]
    return make_record(skill_id, dict(inputs, patent_context=ctx, upstream_records=links), output,
                       sources=sources, **kwargs)


def bind_material(record, features, cutoff, context):
    """Produce a new, traceable contextual record; never mutate a saved material record."""
    old_context = record["inputs"].get("patent_context")
    if old_context and (old_context.get("target") != context.get("target")
                        or old_context.get("cutoff") not in {"", cutoff}):
        raise ValueError("材料整理记录的目标版本或基准日已变化，不能直接沿用。")
    inputs = dict(record["inputs"], original_record_hash=record["record_hash"])
    return stage_record(record["skill_id"], inputs, record["output"], features=features, cutoff=cutoff,
        context=context, sources=record["sources"], execution=record["execution"],
        producer=record["producer"], note=record["note"])


def required_dimensions(record):
    required = set(SKILLS[record["skill_id"]]["required"])
    ctx = record["inputs"].get("patent_context", {})
    required.update(ctx.get("semantic_focus", []))
    if ctx.get("target", {}).get("claim_id"):
        required.update(("structure_relations", "logic_constraints", "context_reference"))
    return [key for key in DIMENSIONS if key in required]


def handoff_issues(records):
    """Only factual binding checks here; meaning preservation is separately judged semantically."""
    by_id = {r["skill_id"]: r for r in records}
    issues, identity, documents = [], None, None
    for record in records:
        ctx = record["inputs"].get("patent_context")
        if not ctx:
            issues.append({"skill_id": record["skill_id"], "status": "undetermined", "basis": "历史记录缺少上下文合同，无法确认步骤衔接。"})
            continue
        current = {key: ctx.get(key) for key in ("features_hash", "cutoff", "scope", "target", "technical_field",
                   "definitions","semantic_focus","rule_profile","baseline_topics")}
        if identity is None:
            identity = current
        elif current != identity:
            issues.append({"skill_id": record["skill_id"], "status": "violated", "basis": "步骤间限定、基准日、范围或权利要求版本不一致。"})
        own = record["inputs"].get("features", record["inputs"].get("confirmed_features"))
        if own is not None and digest(own) != ctx.get("features_hash"):
            issues.append({"skill_id": record["skill_id"], "status": "violated", "basis": "当前要素与上下文绑定不同。"})
        if "cutoff" in record["inputs"] and record["inputs"]["cutoff"] != ctx.get("cutoff"):
            issues.append({"skill_id": record["skill_id"], "status":"violated", "basis":"步骤实际基准日与其上下文不同。"})
        own_documents=(record["output"].get("documents") if record["skill_id"]=="patent-candidate-search"
                       else record["inputs"].get("documents"))
        if own_documents is not None:
            snapshots={d["id"]:digest({k:v for k,v in d.items() if k!="snapshot_hash"}) for d in own_documents}
            if documents is None:
                documents=snapshots
            elif snapshots != documents:
                issues.append({"skill_id":record["skill_id"],"status":"violated","basis":"步骤之间的文献集合、原文快照或日期元数据不同。"})
        links = {r["skill_id"]: r["record_hash"] for r in record["inputs"].get("upstream_records", [])}
        for parent in PREDECESSORS[record["skill_id"]]:
            if parent in by_id and links.get(parent) != by_id[parent]["record_hash"]:
                issues.append({"skill_id": record["skill_id"], "status": "violated", "basis": f"上游{parent}已变化或没有绑定，不能沿用旧衔接。"})
    return issues
