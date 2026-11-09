"""取得 Google Patents 正文快照。检索摘要只用于找候选，不能冒充完整披露。"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from dataclasses import replace

import requests
from bs4 import BeautifulSoup

from modules.novelty_models import PatentDocument, SourceSection, google_patent_url
from modules.google_patents_transport import document_response
from modules.patent_network_state import save_verified_response, original_retrieved_at


def parse_google_page(url: str, html: str) -> PatentDocument:
    url = google_patent_url(url)
    soup = BeautifulSoup(html, "html.parser")

    def meta(*names: str) -> str:
        for name in names:
            node = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"itemprop": name})
            if node and node.get("content"):
                return str(node["content"]).strip()
            node = soup.find("time", attrs={"itemprop": name})
            if node:
                return str(node.get("datetime") or node.get_text()).strip()
        return ""

    sections: list[SourceSection] = []
    selectors = (
        ("abstract", '[itemprop="abstract"], .abstract-content'),
        ("claim", 'div.claim, [itemprop="claim"]'),
        ("description", '[itemprop="description"] .description-paragraph, .description-line'),
    )
    seen: set[tuple[str, str]] = set()
    kinds: set[str] = set()
    counts: dict[str, int] = {}
    ids: set[str] = set()
    for kind, selector in selectors:
        nodes = soup.select(selector)
        if kind == "description" and not nodes:
            nodes = soup.select('[itemprop="description"]')
        for node in nodes:
            if kind == "claim" and node.select('div.claim, [itemprop="claim"]'):
                continue  # A wrapper around claims is not a separate combined claim.
            text = " ".join(node.get_text(" ", strip=True).split())
            if len(text) < 4 or (kind, text) in seen:
                continue
            seen.add((kind, text))
            kinds.add(kind)
            counts[kind] = counts.get(kind, 0) + 1
            number = str(node.get("num", "")).strip()
            if kind == "claim":
                leading = re.match(r"^(\d+)\s*[.、)]", text)
                number = number if number.isdigit() else leading[1] if leading else ""
                number = str(int(number)) if number else ""
                label = f"权利要求{number}" if number else f"快照内权利要求第{counts[kind]}段（原文未编号）"
            elif kind == "description":
                leading = re.match(r"^\[(\d+)\]", text)
                number = number if number.isdigit() else leading[1] if leading else ""
                label = f"说明书[{number}]" if number else f"快照内说明书第{counts[kind]}段（原文未编号）"
            else:
                number = str(counts[kind])
                label = f"摘要第{counts[kind]}段"
            section_id = f"{kind}-{number}" if number else f"{kind}-snapshot-{counts[kind]}"
            base_id = section_id
            suffix = 2
            while section_id in ids:
                section_id = f"{base_id}-{suffix}"
                suffix += 1
            ids.add(section_id)
            sections.append(SourceSection(section_id, text, label, str(node.get("id", ""))))
    doc_id = url.split("/patent/", 1)[1].split("/")[0]
    return PatentDocument(
        id=doc_id, title=meta("DC.title", "citation_title") or doc_id, url=url,
        publication_date=meta("publicationDate", "citation_publication_date"),
        filing_date=meta("filingDate", "citation_filing_date"),
        priority_date=meta("priorityDate", "citation_priority_date"),
        sections=tuple(sections), complete=False, snapshot_schema_version=2,
        parsed_complete="claim" in kinds and "description" in kinds,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        error="" if sections else "页面没有取得可核对正文。",
    )


def fetch_google_document(url: str, get: Callable | None = None) -> PatentDocument:
    url = google_patent_url(url)
    get = get or requests.get
    try:
        response = document_response(url, get)
        if 300 <= response.status_code < 400:
            raise RuntimeError("Google Patents 返回重定向，请人工打开并核对单篇来源。")
        response.raise_for_status()
        document = parse_google_page(url, response.text)
        retrieved_at = original_retrieved_at(response)
        if retrieved_at:
            document = replace(document, retrieved_at=retrieved_at)
        if document.sections and not document.error:
            save_verified_response(response)
        return document
    except requests.RequestException as exc:
        raise RuntimeError("Google Patents 正文获取失败；可粘贴同一来源的原文快照。") from exc


def manual_google_document(url: str, title: str, publication_date: str, body: str,
                           filing_date: str = "", priority_date: str = "",
                           complete: bool = False) -> PatentDocument:
    """网页无法访问时的显式人工入口；报告保留人工录入标记，不伪装成网络抓取。"""
    url = google_patent_url(url)
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", body) if item.strip()]
    if not paragraphs:
        raise ValueError("请粘贴该 Google Patents 页面的原文，并用空行分段。")
    return PatentDocument(
        id=url.split("/patent/", 1)[1].split("/")[0], title=title.strip() or "人工录入原文",
        url=url, publication_date=publication_date.strip(), filing_date=filing_date.strip(),
        priority_date=priority_date.strip(), source_kind="manual_google_patents_snapshot",
        sections=tuple(SourceSection(f"manual-{i}", text, f"人工快照第{i}段（原文编号未核）")
                       for i, text in enumerate(paragraphs, 1)),
        snapshot_schema_version=2,
        complete=complete, retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
