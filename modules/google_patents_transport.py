"""Optional legacy Google entry; patent identities remain canonical Google URLs."""
from __future__ import annotations

import os
from urllib.parse import urljoin, urlsplit

from modules.novelty_models import google_patent_url

CANONICAL_ENTRY = 'https://patents.google.com/'
LEGACY_ENTRY = 'https://www.google.com/patents/'
ENTRY_VARIABLE = 'GOOGLE_PATENTS_ENTRY_URL'
HEADERS = {'User-Agent': 'PatentWorkflowHarness/0.1'}
TIMEOUT = (5, 30)


def configured_entry() -> str:
    value = os.getenv(ENTRY_VARIABLE, '').strip() or CANONICAL_ENTRY
    if value.rstrip('/') not in {CANONICAL_ENTRY.rstrip('/'), LEGACY_ENTRY.rstrip('/')}:
        raise ValueError('GOOGLE_PATENTS_ENTRY_URL 仅支持 Google Patents 官方新旧入口。')
    return value.rstrip('/') + '/'


def _official_redirect(response, requested_url: str):
    response.raise_for_status()
    if response.status_code not in {301, 302, 303, 307, 308}:
        raise RuntimeError('Google Patents 旧入口未返回预期的官方跳转；停止请求。')
    location = response.headers.get('Location', '')
    target = urlsplit(urljoin(requested_url, location))
    if (not location or target.scheme != 'https' or target.hostname != 'patents.google.com'
            or target.port not in {None, 443} or target.username or target.password):
        raise RuntimeError('Google Patents 旧入口返回非预期跳转；停止请求。')
    return target


def search_response(query: str, inner_query: str, get):
    entry = configured_entry()
    if entry == LEGACY_ENTRY:
        landing = get(entry, params={'q': query}, headers=HEADERS,
                      timeout=TIMEOUT, allow_redirects=False)
        target = _official_redirect(landing, entry)
        if target.path not in {'', '/'} or target.fragment:
            raise RuntimeError('Google Patents 检索入口跳转路径异常；停止请求。')
    # The legacy entry redirects its UI, not the JSON endpoint. Send the full
    # original query to the canonical XHR endpoint after validating that redirect.
    return get(CANONICAL_ENTRY + 'xhr/query', params={'url': inner_query, 'exp': ''},
               headers={**HEADERS, 'Accept': 'application/json'},
               timeout=TIMEOUT, allow_redirects=False)


def document_response(url: str, get):
    url = google_patent_url(url)
    if configured_entry() == LEGACY_ENTRY:
        publication = url.split('/patent/', 1)[1].split('/')[0]
        entry = LEGACY_ENTRY + publication
        landing = get(entry, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False)
        target = _official_redirect(landing, entry)
        if target.query or target.fragment:
            raise RuntimeError('Google Patents 单篇入口跳转路径异常；停止请求。')
        redirected = google_patent_url(target.geturl())
        redirected_id = redirected.split('/patent/', 1)[1].split('/')[0]
        if redirected_id != publication:
            raise RuntimeError('Google Patents 跳转后的公开号不一致；停止请求。')
    # Keep the requested language and canonical source URL for snapshot hashes.
    return get(url, headers={'User-Agent': 'PatentNoveltyHarness/1.0'},
               timeout=TIMEOUT, allow_redirects=False)
