"""Search Google Patents directly, separate from Tavily background search."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Iterator
from urllib.parse import urlencode, urljoin, urlparse

import requests

from modules.google_patents_transport import search_response
from modules.patent_network_state import save_verified_response


GOOGLE_PATENTS_XHR = "https://patents.google.com/xhr/query"
GOOGLE_PATENTS_HOME = "https://patents.google.com/"
GOOGLE_PATENTS_HOST = "patents.google.com"
MAX_QUERY_LENGTH = 400


def _clean_text(value: object, limit: int) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]*>", " ", text)
    return " ".join(text.split())[:limit]


def _walk_records(value: object) -> Iterator[dict]:
    if isinstance(value, dict):
        patent = value.get("patent")
        if isinstance(patent, dict):
            yield {**value, **patent, "_outer": value, "_patent": patent}
        for child in value.values():
            yield from _walk_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_records(child)


def _normalize_response(payload: object) -> object:
    """Google's XHR response may wrap the result payload as JSON in `content`."""
    if not isinstance(payload, dict):
        return payload
    content = payload.get("content")
    if isinstance(content, str):
        try:
            return json.loads(content)
        except ValueError:
            return payload
    return payload


def _to_result(item: dict) -> dict | None:
    outer = item.get("_outer", item)
    patent = item.get("_patent", item)
    raw_url = (
        patent.get("patent_url")
        or patent.get("url")
        or outer.get("patent_url")
        or outer.get("url")
    )
    publication_number = (
        patent.get("publication_number")
        or patent.get("patent_number")
        or outer.get("publication_number")
    )
    if raw_url:
        url = urljoin(GOOGLE_PATENTS_HOME, str(raw_url))
    elif publication_number:
        url = urljoin(GOOGLE_PATENTS_HOME, f"patent/{publication_number}/en")
    else:
        return None

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != GOOGLE_PATENTS_HOST:
        return None
    title = _clean_text(patent.get("title") or outer.get("title"), 200)
    snippet = _clean_text(
        outer.get("snippet")
        or patent.get("snippet")
        or patent.get("abstract")
        or outer.get("abstract"),
        1200,
    )
    if not title and not snippet:
        return None
    return {
        "title": title or str(publication_number or "Google Patents 记录"),
        "content": snippet,
        "url": url,
        "publication_number": publication_number,
    }


def search_patents(
    query: str,
    max_results: int = 10,
    get: Callable = requests.get,
) -> list[dict]:
    """Fetch patent records from Google Patents' public search endpoint."""
    if not query.strip():
        raise ValueError("请填写专利检索词。")
    if len(query.strip()) > MAX_QUERY_LENGTH:
        raise ValueError("专利检索词超过 400 字，请拆分查询；不会静默截断。")

    count = max(1, min(max_results, 20))
    inner_query = urlencode({"q": query.strip(), "num": count})
    try:
        response = search_response(query.strip(), inner_query, get)
        response.raise_for_status()
        payload = _normalize_response(response.json())
    except requests.RequestException as exc:
        raise RuntimeError("Google Patents 专利检索失败；请检查网络后重试。") from exc
    except (TypeError, ValueError, AttributeError) as exc:
        raise RuntimeError("Google Patents 返回了无法解析的结果。") from exc

    results: list[dict] = []
    seen: set[str] = set()
    for item in _walk_records(payload):
        record = _to_result(item)
        if record and record["url"] not in seen:
            seen.add(record["url"])
            results.append(record)
            if len(results) >= count:
                break
    if results:
        save_verified_response(response)
    return results
