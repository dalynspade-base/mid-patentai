"""候选检索 skill：Google Patents/显式快照，失败与覆盖边界保留。"""
from modules.novelty_search import retrieve_with_journal, snapshot_journal
from modules.skill_contracts import make_record, document_sources
from modules.patent_context import stage_record


def run(features, cutoff, lexicon, searcher, fetcher, max_documents, *, query="", expansion=None,
        documents=None, supplied=False, audit=None, errors=None, context=None, upstream=()):
    if supplied:
        documents = list(documents or [])
        audit = audit if audit is not None else snapshot_journal(features,cutoff)
    else:
        documents,audit = retrieve_with_journal(features,cutoff,query,lexicon,searcher,fetcher,max_documents,expansion)
    record = stage_record("patent-candidate-search", {"features":features,"cutoff":cutoff,"query":query,
        "mode":"provided_snapshots" if supplied else "online","expansion":expansion},
        {"documents":documents,"search_audit":audit,"errors":errors or []}, sources=document_sources(documents),
        features=features, cutoff=cutoff, context=context, upstream=upstream,
        note="实际检索与快照对照分开；正文失败和未读文献不解释为不存在。")
    return documents,audit,record
