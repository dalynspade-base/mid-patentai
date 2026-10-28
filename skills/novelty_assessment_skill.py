"""新颖性判断 skill：完整限定与同一方案，沿用现有CN预筛规则。"""
from modules.novelty_rules import assess_novelty
from modules.skill_contracts import make_record, document_sources
from modules.patent_context import stage_record


def run(features,documents,reviews,cutoff,*,patent_context=None,upstream=(),handoff_errors=(),**kwargs):
    if handoff_errors:
        from modules.novelty_models import NoveltyDecision
        decision=NoveltyDecision("insufficient_evidence",list(handoff_errors))
    else:
        decision=assess_novelty(features,documents,reviews,cutoff,patent_context=patent_context,**kwargs)
    return decision,record_decision(features,documents,reviews,cutoff,decision,kwargs,
                                   patent_context=patent_context,upstream=upstream)


def record_decision(features,documents,reviews,cutoff,decision,context=None,*,patent_context=None,upstream=()):
    record=stage_record("patent-novelty-assessment", {"features":features,"documents":documents,"reviews":reviews,
        "cutoff":cutoff,"context":context or {}}, decision, sources=document_sources(documents),
        features=features,cutoff=cutoff,context=patent_context,upstream=upstream,
        note="证据不足也是业务结论；是否正确保留未知由独立语义裁判核验。")
    return record
