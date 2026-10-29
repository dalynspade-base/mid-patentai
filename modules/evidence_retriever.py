"""LlamaIndex 阅读线索实现；运行时不调用外部模型，不作为业务 skill。

采用可解释的 TF-IDF 嵌入作为离线基线，不把它宣传为通用语义模型。
检索使用规范词，但返回的始终是未改写的原文和快照标识。
"""

from __future__ import annotations

from dataclasses import dataclass

from llama_index.core import VectorStoreIndex
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.schema import TextNode
from pydantic import PrivateAttr
from sklearn.feature_extraction.text import TfidfVectorizer

from modules.novelty_matching import SynonymLexicon
from modules.novelty_models import PatentDocument, google_patent_url


class LocalSynonymEmbedding(BaseEmbedding):
    """文献和查询共用冻结词表，避免查询时重新拟合而导致向量维度不一致。"""

    _vectorizer: TfidfVectorizer = PrivateAttr()
    _lexicon: SynonymLexicon = PrivateAttr()

    def __init__(self, texts: list[str], lexicon: SynonymLexicon) -> None:
        super().__init__(model_name="local-synonym-char-tfidf-v1")
        self._lexicon = lexicon
        self._vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), max_features=12000)
        self._vectorizer.fit([lexicon.canonicalize(text) for text in texts])

    def _get_text_embedding(self, text: str) -> list[float]:
        vector = self._vectorizer.transform([self._lexicon.canonicalize(text)]).toarray()[0].tolist()
        # 完全未知术语落入专用维度，避免零向量造成 cosine=NaN；不会与普通文献相似。
        return vector + [0.0 if any(vector) else 1.0]

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._get_text_embedding(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)


@dataclass(frozen=True)
class EvidenceHit:
    document_id: str
    section_id: str
    text: str
    url: str
    snapshot_hash: str
    score: float
    match_basis: str


class PatentRetriever:
    def __init__(self, documents: list[PatentDocument], lexicon: SynonymLexicon | None = None,
                 *, allow_synthetic: bool = False) -> None:
        self.lexicon = lexicon or SynonymLexicon()
        self.documents = {doc.id: doc for doc in documents}
        self.sections: dict[str, tuple[PatentDocument, str, str]] = {}
        nodes = []
        for doc in documents:
            google_patent_url(doc.url)
            if doc.source_kind == "synthetic_eval" and not allow_synthetic:
                raise ValueError("教学合成来源不能进入真实检索。")
            for section in doc.sections:
                if not section.text.strip():
                    continue
                # 长段落做检索窗口，命中后返回完整父段，保留前后限定条件。
                for offset in range(0, len(section.text), 900):
                    node_id = f"{doc.id}:{section.id}:{offset}"
                    self.sections[node_id] = (doc, section.id, section.text)
                    nodes.append(TextNode(
                        id_=node_id, text=section.text[offset:offset + 1200],
                        metadata={"document_id": doc.id, "section_id": section.id,
                                  "url": doc.url, "snapshot_hash": doc.snapshot_hash},
                        excluded_embed_metadata_keys=["document_id", "section_id", "url", "snapshot_hash"],
                    ))
        self.index = None
        if nodes:
            embedding = LocalSynonymEmbedding([node.text for node in nodes], self.lexicon)
            self.index = VectorStoreIndex(nodes, embed_model=embedding, show_progress=False)

    def retrieve(self, query: str, top_k: int = 5) -> list[EvidenceHit]:
        """LlamaIndex 召回 + 同义词精确候选提示；评分永不作为新颖性阈值。"""
        if top_k < 1 or not query.strip():
            raise ValueError("检索词不能为空，top_k 必须为正整数。")
        if self.index is None:
            return []
        vector_hits = self.index.as_retriever(similarity_top_k=min(top_k * 3, len(self.sections))).retrieve(query)
        merged: dict[tuple[str, str], EvidenceHit] = {}
        for item in vector_hits:
            doc, section_id, text = self.sections[item.node.node_id]
            basis = self.lexicon.candidate_match(query, text)
            bonus = 1.0 if basis != "unresolved" else 0.0
            hit = EvidenceHit(doc.id, section_id, text, doc.url, doc.snapshot_hash,
                              float(item.score or 0.0) + bonus, basis)
            key = (doc.id, section_id)
            if key not in merged or hit.score > merged[key].score:
                merged[key] = hit
        return sorted(merged.values(), key=lambda hit: (-hit.score, hit.document_id, hit.section_id))[:top_k]
