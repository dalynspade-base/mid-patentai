"""证据对照 skill：检索只给线索，模型逐项理解全部输入原文。"""
from modules.evidence_alignment import locate_evidence
from modules.novelty_review import model_review_documents
from modules.skill_contracts import make_record, document_sources
from modules.patent_context import stage_record


def record_alignment(features, documents, hits, candidates, reviews, cutoff="", *, context=None, upstream=()):
    from modules.candidate_relevance import valid_body_review
    from modules.patent_context import context_for
    ctx=context_for(features,cutoff,context)
    # The caller supplies the cutoff to the model review; freshness is rechecked by the decision layer.
    by_id = {doc.id: doc for doc in documents}
    complete = bool(documents) and bool(cutoff) and {r.document_id for r in reviews
        if valid_body_review(r,features,by_id.get(r.document_id),cutoff,patent_context=ctx)} == set(by_id)
    return stage_record("patent-evidence-alignment", {"features":features,"documents":documents,"reading_hints":hits,"cutoff":cutoff},
        {"candidate_rows":candidates,"reviews":reviews}, sources=document_sources(documents),
        features=features, cutoff=cutoff, context=context, upstream=upstream,
        execution="completed" if complete else "partial", producer="model_or_historical_review" if complete else "reading_hints_only",
        note="有阅读线索但没有全部文献有效逐项复核时，不冒称已完成语义对照。")


def locate(features, documents, lexicon=None, allow_synthetic=False, locator=None):
    hits,candidates = (locator or locate_evidence)(features,documents,lexicon,allow_synthetic=allow_synthetic)
    return hits,candidates,record_alignment(features,documents,hits,candidates,[])


def review(features,documents,cutoff,client,confirmed=False,candidates=None,*,evidence_hits=None,context=None,upstream=()):
    reviews=model_review_documents(features,documents,cutoff,client,confirmed,candidates,
                                  evidence_hits=evidence_hits,patent_context=context)
    return reviews,record_alignment(features,documents,evidence_hits or [],candidates or [],reviews,cutoff,
                                   context=context,upstream=upstream)
