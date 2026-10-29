"""报告交付：统一刷新业务记录、绑定最终正文，供网页和主流程使用。"""
from modules.novelty_report import build_payload, to_markdown
from modules.skill_contracts import make_record, upsert_record
from skills.evidence_alignment_skill import record_alignment
from skills.novelty_assessment_skill import record_decision
from modules.patent_context import stage_record, handoff_issues


def record_report(payload,markdown,*,upstream=()):
    keys=("invention","features","documents","reviews","decision","material_unknowns","material_warnings","search_audit",
          "limits","material_case","feature_preparation","feature_preparation_matches_input","cutoff","scope","patent_context")
    # Bind the delivered structured conclusions without recursively embedding records/evaluation or duplicating raw documents.
    delivered={key:payload.get(key) for key in ("task","cutoff","scope","features","decision","material_unknowns","material_warnings","patent_context")}
    return stage_record("patent-report-delivery", {key:payload.get(key) for key in keys},
        {"markdown":markdown,"json":delivered},features=payload["features"],cutoff=payload["cutoff"],
        context=payload.get("patent_context"),upstream=upstream,
        note="正文及交付JSON的结论、限定与范围同时验收；原文快照另行绑定，不递归评估附录。")


def build_report(state, invention, cutoff, lexicon_groups, *, material_case=None,
                 feature_preparation=None, feature_preparation_matches_input=True):
    payload = build_payload(state, invention, cutoff, lexicon_groups)
    if material_case is not None:
        payload["material_case"] = material_case
    if feature_preparation is not None:
        payload["feature_preparation"] = feature_preparation
        payload["feature_preparation_matches_input"] = feature_preparation_matches_input
    records = upsert_record(state.get("skill_records", []), record_alignment(
        state["features"], state["documents"], state.get("hits", []),
        state.get("suggestions", []), state.get("reviews", []), cutoff,
        context=payload["patent_context"],upstream=state.get("skill_records", []),
    ))
    records = upsert_record(records, record_decision(
        state["features"], state["documents"], state.get("reviews", []), cutoff,
        state["decision"], {key: state.get(key) for key in (
            "errors", "search_audit", "material_unknowns", "material_warnings",
        )},
        patent_context=payload["patent_context"],upstream=records,
    ))
    payload["skill_architecture"] = "precheck-six-business-skills-and-independent-semantic-eval"
    # Freeze business text before adding audit/eval sections to avoid self-reference.
    payload["skill_records"] = upsert_record(
        records, record_report(payload, to_markdown(payload, include_skill_audit=False),upstream=records),
    )
    payload["handoff_checks"]=handoff_issues(payload["skill_records"])
    return payload


def run(state, invention, cutoff, lexicon_groups, **context):
    payload = build_report(state, invention, cutoff, lexicon_groups, **context)
    return payload, to_markdown(payload)
