"""Bounded retries for free patent HTTP reads; never converts failure to results."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time

import requests
from modules.patent_network_state import (
    PatentAccessPaused, PatentNetworkState, blocked_page, request_key,
    MIN_INTERVAL_SECONDS, TEMPORARY_PAUSE_SECONDS,
)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (5, 15)
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_WAIT_SECONDS = 30
_NETWORK_GET = requests.get
_DEFAULT_STATE = None


def default_state():
    global _DEFAULT_STATE
    if _DEFAULT_STATE is None:
        _DEFAULT_STATE = PatentNetworkState()
    return _DEFAULT_STATE


def retry_policy():
    return {'max_attempts': MAX_ATTEMPTS, 'backoff_seconds': list(BACKOFF_SECONDS),
            'retryable_statuses': sorted(RETRYABLE_STATUSES),
            'max_retry_wait_seconds': MAX_RETRY_WAIT_SECONDS,
            'minimum_interval_seconds': MIN_INTERVAL_SECONDS,
            'automated_query_block': 'pause_until_explicit_resume',
            'temporary_failure_pause_seconds': TEMPORARY_PAUSE_SECONDS,
            'cache': 'validated_search_1h_document_30d_preserve_retrieved_at'}


def _delay(response, attempt):
    delay = BACKOFF_SECONDS[attempt]
    value = response.headers.get('Retry-After', '')
    if value:
        try:
            requested = float(value)
        except ValueError:
            try:
                requested = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                requested = 0
        # If the server asks for a longer pause, stop this trial instead of
        # retrying earlier than requested or waiting indefinitely.
        if requested > MAX_RETRY_WAIT_SECONDS:
            return None
        delay = max(delay, requested)
    return delay


def get_patent(get, url, *, state=None, **kwargs):
    # Dependency-injected test/diagnostic transports are unmanaged unless a state is supplied.
    if state is None:
        state = getattr(get, '__dict__', {}).get('patent_network_state')
    if state is None and get is _NETWORK_GET:
        state = default_state()
    key = request_key(url, kwargs)
    if state is not None:
        cached = state.cached(key)
        if cached is not None:
            return cached
    for attempt in range(MAX_ATTEMPTS):
        try:
            if state is None:
                response = get(url, **kwargs)
                if blocked_page(response):
                    raise PatentAccessPaused('Google Patents 返回自动查询拦截页；已停止本批请求，请人工检查网络后恢复。')
            else:
                # Commit the pause before raising so it survives a process restart.
                with state.request_slot() as con:
                    response = get(url, **kwargs)
                    fetched_at = state.clock()
                    blocked = blocked_page(response)
                    state.event(key, url, response.status_code, 'blocked' if blocked else 'network', con=con)
                    if blocked:
                        state.pause('自动查询拦截，需要人工确认网络已恢复', con=con)
                    elif response.status_code in RETRYABLE_STATUSES and attempt + 1 == MAX_ATTEMPTS:
                        state.pause('临时故障重试用尽', fetched_at + TEMPORARY_PAUSE_SECONDS, con=con)
                if blocked:
                    response.close()
                    raise PatentAccessPaused('Google Patents 返回自动查询拦截页；已暂停整批请求，请人工检查网络后显式恢复。')
                response.patent_origin = {'source': 'network', 'fetched_at': fetched_at, 'request_key': key}
                response.patent_state = state
        except requests.exceptions.SSLError:
            raise
        except (requests.Timeout, requests.ConnectionError):
            if state is not None:
                state.event(key, url, None, 'connection_error')
            if attempt + 1 == MAX_ATTEMPTS:
                if state is not None:
                    state.pause('连接或读取超时重试用尽', state.clock() + TEMPORARY_PAUSE_SECONDS)
                raise
            time.sleep(BACKOFF_SECONDS[attempt])
            continue
        if response.status_code not in RETRYABLE_STATUSES or attempt + 1 == MAX_ATTEMPTS:
            return response
        delay = _delay(response, attempt)
        if delay is None:
            if state is not None:
                state.pause('服务端要求更长等待，请稍后人工恢复')
            return response
        response.close()
        time.sleep(delay)
    raise AssertionError('Unreachable retry state')
