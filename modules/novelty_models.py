"""新颖性链路的数据契约：原文、候选、模型复核与历史人工复核分开保存。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from urllib.parse import urlparse


LABELS = {
    "potential_novelty_risk": "发现单篇文献完整披露风险（预筛）",
    "no_complete_disclosure_found": "本次已复核文献中未发现完整披露",
    "insufficient_evidence": "证据不足，需要补充或复核",
    "out_of_scope": "超出本工具的新颖性范围",
}


def google_patent_url(url: str) -> str:
    """只接收 Google Patents 单篇页面，不允许相似域名或任意外部地址。"""
    parsed = urlparse(url.strip())
    if (parsed.scheme != "https" or parsed.hostname != "patents.google.com"
            or parsed.port not in {None, 443} or parsed.username or parsed.password
            or not re.fullmatch(r"/patent/[A-Za-z0-9]+(?:/[a-zA-Z-]+)?/?", parsed.path)):
        raise ValueError("来源必须是 https://patents.google.com/patent/公开号 的单篇页面。")
    return f"https://patents.google.com{parsed.path.rstrip('/')}"


@dataclass(frozen=True)
class Feature:
    id: str
    text: str


def parse_features(text: str) -> list[Feature]:
    """按用户确认的整套要素逐行读取，保留句内连接关系、否定词和数值。

    不自动按逗号拆分，防止把“传感器连接控制器”拆成两个无关系的名词。
    当前不解析独立/从属权利要求依赖，也不推测用户遗漏的必要特征。
    """
    lines = [re.sub(r"^\s*(?:[-*•]|\d+[.、）)])\s*", "", line).strip()
             for line in re.split(r"[\r\n；;]+", text)]
    lines = [line for line in lines if line]
    if not lines or len(lines) > 30 or any(len(line) > 1500 for line in lines):
        raise ValueError("请逐行填写 1–30 个技术要素，每个不超过 1500 字，并保留关系和限定。")
    return [Feature(f"F{i}", value) for i, value in enumerate(dict.fromkeys(lines), 1)]


@dataclass(frozen=True)
class SourceSection:
    id: str
    text: str
    source_location: str = ""
    source_anchor: str = ""


@dataclass(frozen=True)
class PatentDocument:
    id: str
    title: str
    url: str
    publication_date: str
    sections: tuple[SourceSection, ...]
    filing_date: str = ""
    priority_date: str = ""
    source_kind: str = "google_patents_page"
    retrieved_at: str = ""
    complete: bool = False
    error: str = ""
    snapshot_schema_version: int = 1
    parsed_complete: bool = False

    def __post_init__(self) -> None:
        if self.snapshot_schema_version not in {1, 2}:
            raise ValueError("不支持的快照格式版本。")
        if self.snapshot_schema_version == 1 and (
                self.parsed_complete or any(s.source_location or s.source_anchor for s in self.sections)):
            raise ValueError("原文定位信息须使用第2版快照，不能添加到旧版哈希之外。")

    @property
    def snapshot_hash(self) -> str:
        # 日期也是判断输入，所以不仅对正文做哈希。
        raw = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.snapshot_schema_version == 1:
            # Preserve the original canonical representation and hashes exactly.
            data.pop("snapshot_schema_version")
            data.pop("parsed_complete")
            for section in data["sections"]:
                section.pop("source_location")
                section.pop("source_anchor")
        return data

    @classmethod
    def from_dict(cls, value: dict) -> "PatentDocument":
        data = dict(value)
        expected_hash = data.pop("snapshot_hash", None)
        data["sections"] = tuple(SourceSection(**item) for item in data.get("sections", []))
        document = cls(**data)
        if expected_hash is not None and document.snapshot_hash != expected_hash:
            raise ValueError("证据快照哈希不匹配，不能沿用原复核记录。")
        return document


@dataclass(frozen=True)
class EvidenceRow:
    feature_id: str
    status: str = "unknown"  # disclosed / not_found / unknown
    section_id: str = ""
    quote: str = ""
    scheme_id: str = ""  # 同一文献中同一技术方案；不得拼接互斥实施例。
    basis: str = "unreviewed"
    origin: str = "unknown"  # auto / model / human; unknown for legacy records.

    def __post_init__(self) -> None:
        # 表格清空文本单元格时 Streamlit 可能返回 None，按空证据处理而不是崩溃。
        for key in ("feature_id", "status", "section_id", "quote", "scheme_id", "basis", "origin"):
            value = getattr(self, key)
            if value is None:
                object.__setattr__(self, key, "")
            elif not isinstance(value, str):
                raise ValueError(f"要素对照的 {key} 必须是文本。")


@dataclass(frozen=True)
class DocumentReview:
    document_id: str
    rows: tuple[EvidenceRow, ...]
    reviewed: bool = False
    same_scheme_confirmed: bool = False
    enabling_disclosure_confirmed: bool = False
    reviewer: str = ""
    reviewed_at: str = ""
    input_hash: str = ""
    same_scheme_basis: str = ""
    scheme_section_ids: tuple[str, ...] = ()
    relevance: str = "unknown"  # related / unrelated / unknown; never inferred from lexical score.
    relevance_basis: str = ""
    relevance_section_ids: tuple[str, ...] = ()
    # 默认 human 兼容旧记录；模型复核必须显式记录来源，不能伪装人工确认。
    review_method: str = "human"
    review_model: str = ""
    review_endpoint: str = ""
    review_prompt_version: str = ""
    context_complete: bool = False
    completeness_basis: str = ""
    reviewed_section_ids: tuple[str, ...] = ()
    review_context_hash: str = ""

    def __post_init__(self) -> None:
        for key in ("reviewer", "reviewed_at", "input_hash", "same_scheme_basis", "relevance", "relevance_basis",
                    "review_method", "review_model", "review_endpoint", "review_prompt_version", "completeness_basis", "review_context_hash"):
            value = getattr(self, key)
            if value is None:
                object.__setattr__(self, key, "")
            elif not isinstance(value, str):
                raise ValueError(f"复核记录的 {key} 必须是文本。")
        if self.review_method not in {"human", "model", "candidate"} or type(self.context_complete) is not bool:
            raise ValueError("复核来源或上下文完整性字段无效。")
        for key in ("scheme_section_ids", "relevance_section_ids", "reviewed_section_ids"):
            value = getattr(self, key)
            if value is None:
                object.__setattr__(self, key, ())
            elif (not isinstance(value, (tuple, list)) or any(not isinstance(x, str) for x in value)):
                raise ValueError("复核依据位置须为编号列表。")
            else:
                object.__setattr__(self, key, tuple(value))


def review_content(review: DocumentReview) -> dict:
    """Editable content, excluding the timestamp/hash assigned only on submit."""
    data = asdict(review)
    data.pop("reviewed_at")
    data.pop("input_hash")
    return data


def review_input_hash(features: list[Feature], document: PatentDocument, cutoff: str,
                      review: DocumentReview) -> str:
    data = asdict(review)
    data.pop("input_hash")
    if not review.review_context_hash:
        data.pop("review_context_hash")  # Preserve the exact hash of historical records.
    # 旧人工记录沿用原哈希；新的模型字段全部绑定，修改模型或上下文判定会失效。
    defaults = {"review_method": "human", "review_model": "", "review_endpoint": "",
                "review_prompt_version": "", "context_complete": False,
                "completeness_basis": "", "reviewed_section_ids": ()}
    if all(getattr(review, key) == value for key, value in defaults.items()):
        for key in defaults:
            data.pop(key)
    raw = json.dumps({"features": [asdict(f) for f in features], "cutoff": cutoff,
                      "snapshot_hash": document.snapshot_hash, "review": data},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def stamp_review(review: DocumentReview, features: list[Feature],
                 document: PatentDocument, cutoff: str, *, patent_context=None) -> DocumentReview:
    """绑定已完成的复核；模型建议不得调用，模型终审须显式标记 review_method=model。"""
    if not review.reviewed:
        return replace(review, reviewed_at="", input_hash="")
    if review.review_method == "candidate":
        raise ValueError("候选建议不能直接提交为已完成复核。")
    if patent_context is not None:
        from modules.patent_context import context_for
        from modules.skill_contracts import digest
        review=replace(review,review_context_hash=digest(context_for(features,cutoff,patent_context)))
    stamped = replace(review, reviewer=review.reviewer.strip(),
                      reviewed_at=datetime.now(timezone.utc).isoformat(), input_hash="")
    return replace(stamped, input_hash=review_input_hash(features, document, cutoff, stamped))


@dataclass
class NoveltyDecision:
    label: str
    reasons: list[str]
    document_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return LABELS[self.label]


def check_cutoff(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("判断基准日必须为 YYYY-MM-DD；有优先权时请人工确定适用基准日。") from exc
