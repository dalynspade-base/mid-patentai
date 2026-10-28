"""术语扩展 skill：执行已有的候选、保护限定与显式审核契约。"""
from modules.synonym_expansion import build_expansion_plan, generate_llm_candidates
from modules.skill_contracts import make_record
from modules.patent_context import stage_record


def run(features, lexicon, profile="engineering", protected_terms=None, generated=None, approved_ids=None,
        *, cutoff="", context=None, upstream=()):
    plan = build_expansion_plan(features, lexicon, profile, protected_terms, generated, approved_ids)
    return plan, record_plan(features, lexicon, plan, cutoff=cutoff, context=context, upstream=upstream)


def record_plan(features, lexicon, plan, *, cutoff="", context=None, upstream=()):
    from modules.patent_context import context_for
    from modules.skill_contracts import digest
    ctx=context_for(features,cutoff,context)
    if plan.get("context_binding") and plan["context_binding"] != digest({k:ctx[k] for k in ("features_hash","cutoff","scope","target")}):
        raise ValueError("术语扩展不属于当前限定、基准日或权利要求版本。")
    return stage_record("patent-synonym-expansion", {"features":features,"dictionary":lexicon.groups,
        "policy":plan.get("policy"),"protected_terms":plan.get("protected_terms",[])}, plan,
        features=features, cutoff=cutoff, context=context, upstream=upstream,
        producer="model_candidates_and_explicit_filter" if plan.get("generation",{}).get("status")=="ok" else "dictionary_and_explicit_filter")


def generate(*args, **kwargs):
    return generate_llm_candidates(*args, **kwargs)
