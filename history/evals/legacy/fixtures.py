"""把人工编写的教学事实转换成运行输入；expected 标签从不参与被测流程。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from modules.novelty_models import (DocumentReview, EvidenceRow, Feature, PatentDocument,
                                    SourceSection, review_input_hash)


def dataset() -> dict:
    return json.loads((Path(__file__).with_name("basic_cases.json")).read_text(encoding="utf-8"))


def case_inputs(case: dict, cutoff: str = "2024-01-01") -> dict:
    features = [Feature(f"F{i}", text) for i, text in enumerate(case["features"], 1)]
    documents = []
    reviews = []
    for raw in case.get("documents", []):
        doc_id = raw["id"]
        documents.append(PatentDocument(
            id=doc_id, title=f"教学合成案例 {doc_id}", url=f"https://patents.google.com/patent/{doc_id}",
            publication_date=raw.get("publication_date", "2023-01-01"),
            filing_date=raw.get("filing_date", ""), sections=(SourceSection("s1", raw["text"]),),
            source_kind="synthetic_eval", complete=raw.get("complete", True),
        ))
        rows = []
        for index, (feature, status) in enumerate(zip(features, raw["statuses"])):
            quote = raw.get("quotes", case["features"])[index] if status == "disclosed" else ""
            scheme_id = raw.get("scheme_ids", ["scheme1"] * len(features))[index]
            rows.append(EvidenceRow(feature.id, status, "s1", quote, scheme_id,
                                    "教学夹具模拟复核，不是真实人工确认", origin="human"))
        review = DocumentReview(doc_id, tuple(rows), raw.get("reviewed", True), True, True,
                                reviewer="synthetic-fixture-not-a-real-reviewer",
                                reviewed_at="2024-01-01T00:00:00+00:00",
                                same_scheme_basis="教学夹具给定的方案关系，不代表真实专利判断",
                                scheme_section_ids=("s1",), relevance="related",
                                relevance_basis="教学夹具模拟的相关技术范围；不是实际相关性确认",
                                relevance_section_ids=("s1",))
        reviews.append(replace(review, input_hash=review_input_hash(features, documents[-1], cutoff, review)))
    return {"features": features, "documents": documents, "reviews": reviews,
            "cutoff": cutoff, "task": case.get("task", "novelty"), "errors": case.get("errors", [])}
