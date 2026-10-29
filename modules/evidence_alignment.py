"""统一证据定位：本地检索提供阅读线索，技术含义及披露状态交给模型。"""
from __future__ import annotations

from dataclasses import asdict

from modules.novelty_matching import SynonymLexicon
from modules.novelty_models import DocumentReview, EvidenceRow, Feature, PatentDocument
from modules.evidence_retriever import PatentRetriever


def locate_evidence(features: list[Feature], documents: list[PatentDocument],
                    lexicon: SynonymLexicon | None = None, *,
                    allow_synthetic: bool = False) -> tuple[list[dict], list[DocumentReview]]:
    """每篇独立定位，避免其他文献占满TopK；没有命中也不排除模型阅读该篇。"""
    hits, candidates = [], []
    for document in documents:
        retriever = PatentRetriever([document], lexicon, allow_synthetic=allow_synthetic)
        rows = []
        for feature in features:
            located = retriever.retrieve(feature.text, top_k=3)
            hits.extend(asdict(hit) | {"feature_id": feature.id} for hit in located)
            first = located[0] if located else None
            rows.append(EvidenceRow(
                feature.id, "unknown", first.section_id if first else "", first.text if first else "",
                scheme_id="", basis="本地检索阅读线索；技术含义、全部限定和方案关系由模型独立核查。",
                origin="auto"))
        candidates.append(DocumentReview(document.id, tuple(rows), review_method="candidate"))
    return hits, candidates


def reading_hints(features: list[Feature], document: PatentDocument, hits: list[dict]) -> list[dict]:
    """只发送可回查的段落引用及分数，原文完整保留在document中，不重复堆叠正文。"""
    feature_ids = {feature.id for feature in features}
    sections = {section.id: section.text for section in document.sections}
    result, seen = [], set()
    for hit in hits:
        if hit.get("document_id") != document.id:
            continue
        feature_id, section_id = hit.get("feature_id"), hit.get("section_id")
        if (feature_id not in feature_ids or section_id not in sections
                or hit.get("snapshot_hash") != document.snapshot_hash
                or hit.get("text") != sections[section_id]):
            raise ValueError("本地证据定位提示与当前要素或原文快照不匹配，请重新定位。")
        key = (feature_id, section_id)
        if key not in seen:
            seen.add(key)
            result.append({"document_id": document.id, "feature_id": feature_id,
                           "section_id": section_id, "score": hit.get("score"),
                           "match_basis": hit.get("match_basis", "unresolved")})
    return result
