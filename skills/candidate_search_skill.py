"""候选检索 skill：Google Patents/显式快照，失败与覆盖边界保留。"""
from modules.novelty_search import retrieve_with_journal, snapshot_journal
from modules.skill_contracts import make_record, document_sources


def search_execution(audit, documents):
    """Completion describes acquisition, not semantic adequacy or a novelty verdict."""
    if audit.get("mode") == "online":
        queries = audit.get("queries", [])
        if not queries or not any(q.get("status") == "ok" for q in queries):
            return "failed"
        if (any(q.get("status") != "ok" for q in queries) or audit.get("skipped")
                or any(c.get("fetch_status") != "fetched" for c in audit.get("candidates", []))
                or any(d.error or not d.sections for d in documents)):
            return "partial"
        # A successful empty response is distinct from a network failure.
        return "completed"
    if not documents:
        return "not_executed"
    valid = [d for d in documents if d.sections and not d.error]
    if len(valid) == len(documents):
        return "completed"
    return "partial" if valid else "failed"


def run(features, cutoff, lexicon, searcher, fetcher, max_documents, *, query="", expansion=None,
        documents=None, supplied=False, audit=None, errors=None):
    if supplied:
        documents = list(documents or [])
        audit = audit if audit is not None else snapshot_journal(features,cutoff)
    else:
        documents,audit = retrieve_with_journal(features,cutoff,query,lexicon,searcher,fetcher,max_documents,expansion)
    record = make_record("patent-candidate-search", {"features":features,"cutoff":cutoff,"query":query,
        "mode":audit["mode"],"expansion":audit.get("expansion", expansion)},
        {"documents":documents,"search_audit":audit,"errors":errors or []}, sources=document_sources(documents),
        execution=search_execution(audit, documents),
        note="实际检索与快照对照分开；正文失败和未读文献不解释为不存在。")
    return documents,audit,record
