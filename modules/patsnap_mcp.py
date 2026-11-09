"""Read-only access to the existing patent-platform MCP gateway."""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import requests

from modules.novelty_models import PatentDocument, SourceSection, patsnap_patent_url, patsnap_view_url
from modules.patent_network_state import PatentAccessPaused


MAX_PATSNAP_RESULTS = 25  # Validated at the gateway; not an account or provider maximum.
_TOOLS = {"search_patents", "get_bibliography", "get_claims", "get_description"}
_PAUSE_CODES = {
    67200000: "达到平台调用限制", 67200001: "达到平台调用限制", 67200002: "达到调用频率限制",
    67200003: "认证失败", 67200004: "接口无权限或套餐配额已用完",
    67200005: "额度不足", 67200006: "授权已过期", 67200007: "达到调用额度限制",
    67200008: "缺少授权", 67200009: "授权不匹配", 67200100: "平台繁忙或响应超时",
}
_FIELD = re.compile(r"\b(?:TTL|ABST|CLMS|DESC|TACD|AN|ANS|ANCS|IN|IPC|CPC|PRNO|PN|APD|PBD)\s*:", re.I)


def patsnap_query(query: str, *, custom: bool = False) -> str:
    if custom and _FIELD.search(query):
        return query
    # Keep generated queries literal, including terms that look like operators.
    escaped = query.replace("\\", "\\\\").replace('"', '\\"')
    return f'TACD:("{escaped}")'


def _check_error(payload, tool):
    # Do not emit raw gateway errors: they can contain credentials or the full query.
    code = payload.get("error_code", payload.get("code"))
    if isinstance(code, str) and code.isdecimal():
        code = int(code)
    if type(code) is int and code in _PAUSE_CODES:
        raise PatentAccessPaused(f"智慧芽 {tool}：{_PAUSE_CODES[code]}（{code}），已停止本批。")
    if payload.get("status") == "needs_confirmation":
        raise ValueError(f"智慧芽 {tool} 需要开启付费正文权限。")
    if payload.get("status") is False or payload.get("status") == "error" or code not in (0, None) or payload.get("error"):
        suffix = f"（{code}）" if type(code) is int and 100 <= code <= 99999999 else ""
        raise RuntimeError(f"智慧芽 {tool} 未成功{suffix}。")


def _date(value):
    if not isinstance(value, (int, str)):
        return ""
    raw = str(value)
    try:
        return (datetime.strptime(raw, "%Y%m%d").date() if re.fullmatch(r"\d{8}", raw)
                else date.fromisoformat(raw)).isoformat()
    except ValueError:
        return ""


class PatsnapMCP:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path or os.getenv("PATENT_PLATFORM_MCP_CONFIG")
                                or (Path.home() / "Downloads" / "mcp.json")).expanduser()
        self._headers = {}
        self._url = ""
        self._id = 0
        self._connected = False

    def _connect(self):
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
            server = config["mcpServers"]["patent-platform"]
            url = server["url"]
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.hostname != "patent.jiuan.com" or parsed.username
                    or parsed.password or parsed.fragment or parsed.port not in (None, 443)):
                raise ValueError
            auth = next((value for key, value in server["headers"].items()
                         if key.lower() == "authorization"), None)
            if not isinstance(auth, str) or not auth.strip() or "\r" in auth or "\n" in auth:
                raise ValueError
        except (OSError, KeyError, ValueError, TypeError, AttributeError):
            raise PatentAccessPaused("智慧芽连接配置不可用，请检查 PATENT_PLATFORM_MCP_CONFIG 指向的 patent-platform 配置。") from None
        self._url = url
        self._headers = {"Authorization": auth, "Accept": "application/json, text/event-stream"}
        initialized = self._post("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "patent-workflow", "version": "2.4.0"}})
        version = initialized.get("protocolVersion")
        if version not in {"2025-03-26", "2025-06-18", "2025-11-25"}:
            raise PatentAccessPaused("智慧芽连接返回不支持的 MCP 协议版本。")
        self._headers["MCP-Protocol-Version"] = version
        self._post("notifications/initialized", notification=True)
        self._connected = True

    def _post(self, method, params=None, *, notification=False):
        self._id += 1
        packet = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            packet["params"] = params
        if not notification:
            packet["id"] = self._id
        try:
            with requests.post(self._url, json=packet, headers=dict(self._headers), timeout=(8, 40),
                               allow_redirects=False, stream=True) as response:
                status = response.status_code
                if status in {401, 402, 403, 404, 429} or 300 <= status < 400 or status >= 500:
                    raise PatentAccessPaused(f"智慧芽连接暂停（HTTP {status}），本批不再发送请求。")
                if not 200 <= status < 300:
                    raise RuntimeError(f"智慧芽连接返回 HTTP {status}。")
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._headers["Mcp-Session-Id"] = session_id
                if notification:
                    return {}
                if "text/event-stream" in response.headers.get("Content-Type", ""):
                    event = []
                    result = None
                    for line in response.iter_lines():
                        if line.startswith(b"data:"):
                            event.append(line[5:].lstrip())
                        elif not line and event:
                            data = json.loads(b"\n".join(event))
                            event.clear()
                            if isinstance(data, dict) and data.get("id") == self._id:
                                result = data
                                break
                else:
                    result = response.json()
        except (requests.RequestException, ValueError):
            raise PatentAccessPaused("智慧芽连接失败或响应不可读，本批不再发送请求。") from None
        if not isinstance(result, dict) or result.get("id") != self._id or not isinstance(result.get("result"), dict):
            raise PatentAccessPaused("智慧芽返回无效的 MCP 响应，本批不再发送请求。")
        return result["result"]

    def call(self, tool, arguments):
        if tool not in _TOOLS:
            raise ValueError("当前只允许智慧芽检索和正文读取工具。")
        if not self._connected:
            self._connect()
        response = self._post("tools/call", {"name": tool, "arguments": arguments})
        payload = response.get("structuredContent")
        if not isinstance(payload, dict):
            blocks = response.get("content", [])
            parts = [item["text"] for item in blocks if isinstance(item, dict)
                     and item.get("type") == "text" and isinstance(item.get("text"), str)]
            try:
                payload = json.loads(parts[0]) if len(parts) == 1 else None
            except ValueError:
                code = re.search(r"\b672(?:0000[0-9]|00100)\b", parts[0])
                payload = {"status": False, "error_code": int(code[0]) if code else None}
        if not isinstance(payload, dict):
            raise RuntimeError(f"智慧芽 {tool} 返回格式无效。")
        _check_error(payload, tool)
        if response.get("isError"):
            raise RuntimeError(f"智慧芽 {tool} 调用失败。")
        return payload

    def search(self, query, limit):
        if type(limit) is not int or not 1 <= limit <= MAX_PATSNAP_RESULTS:
            raise ValueError(f"智慧芽每条查询可保留 1–{MAX_PATSNAP_RESULTS} 篇候选。")
        payload = self.call("search_patents", {"query": query, "limit": limit, "offset": 0})
        data = payload.get("data")
        if payload.get("status") is not True or not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise RuntimeError("智慧芽检索响应缺少结果列表。")
        results = []
        for hit in data["results"]:
            if not isinstance(hit, dict):
                results.append(None)
                continue
            try:
                url = patsnap_view_url(hit.get("patent_id"))
            except ValueError:
                url = ""
            results.append({"title": hit.get("title"), "url": url, "publication_number": hit.get("pn")})
        total = data.get("total_search_result_count")
        return {"results": results, "total_matches": total if type(total) is int and total >= len(results) else None}

    def fetch(self, candidate):
        url = patsnap_patent_url(candidate["url"])
        patent_id = parse_qs(urlsplit(url).query)["patentId"][0]
        pn = candidate["publication_id"]
        if not re.fullmatch(r"[A-Z]{2}\d[A-Z0-9]{3,22}", pn):
            raise ValueError("智慧芽候选的公开号无效。")
        key = {"patent_number": pn}
        biblio = self.call("get_bibliography", key)
        data = biblio.get("data")
        item = data[0] if isinstance(data, list) and len(data) == 1 else None
        if (biblio.get("status") is not True or not isinstance(item, dict)
                or item.get("pn") != pn or str(item.get("patent_id")).lower() != patent_id):
            raise ValueError("智慧芽著录项的公开号或记录 ID 不匹配。")
        detail = item.get("bibliographic_data")
        if not isinstance(detail, dict):
            raise ValueError("智慧芽著录项缺少日期。")
        pub = detail.get("publication_reference")
        filing = detail.get("application_reference")
        published = _date(pub.get("date")) if isinstance(pub, dict) else ""
        if not published:
            raise ValueError("智慧芽著录项未提供有效公开日期。")
        claims = self.call("get_claims", {**key, "confirm_paid": True})
        claim_text = _body(claims, "claim_text", "claim_length", pn)
        if not claim_text:
            raise ValueError("智慧芽未返回可核对的权利要求文本。")
        desc = self.call("get_description", {**key, "force": False})
        desc_text = _body(desc, "description_text", "description_length", pn)
        if not desc_text:
            raise ValueError("智慧芽未返回可核对的说明书文本。")
        sections = [SourceSection("C1", claim_text, _body_location(claims, "权利要求全文", "P018"))]
        sections.extend(SourceSection(f"D{i}", paragraph, _body_location(desc, f"说明书返回文本第 {i} 段", "P019"))
                        for i, paragraph in enumerate((p for p in desc_text.splitlines() if p.strip()), 1))
        titles = detail.get("invention_title")
        titles = [t for t in titles if isinstance(t, dict) and isinstance(t.get("text"), str) and t["text"].strip()] if isinstance(titles, list) else []
        if pn.startswith("CN"):
            titles.sort(key=lambda t: str(t.get("lang", "")).upper() not in {"CN", "ZH", "ZH-CN"})
        title = titles[0]["text"] if titles else candidate.get("title") or pn
        return PatentDocument(pn, title, url, published, tuple(sections),
                              filing_date=_date(filing.get("date")) if isinstance(filing, dict) else "",
                              source_kind="patsnap_mcp_snapshot", retrieved_at=datetime.now(timezone.utc).isoformat(),
                              snapshot_schema_version=2, parsed_complete=not bool(claims.get("claims_with_images")))


def _body(payload, field, length_field, pn):
    returned_pn = payload.get("patent_number") or payload.get("pn")
    if returned_pn and returned_pn != pn:
        raise ValueError("智慧芽返回的正文公开号不一致。")
    text = payload.get(field)
    if not isinstance(text, str) or not text.strip():
        return ""
    if type(payload.get(length_field)) is int and payload[length_field] != len(text):
        raise ValueError("智慧芽正文长度与回执不一致。")
    return text


def _body_location(payload, label, api):
    origins = {"zhihuiya_p018": "智慧芽 P018", "zhihuiya_p019": "智慧芽 P019",
               "uspto_odp": "USPTO ODP（平台返回）", "local_cache": "平台本地缓存"}
    origin = next((origins[str(payload[k])] for k in ("source", "_source")
                   if k in payload and str(payload[k]) in origins), "平台返回，未标明上游")
    return f"智慧芽接入 · {label}（{api}工具；{origin}）"
