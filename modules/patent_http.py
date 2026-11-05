"""Bounded retries for free patent HTTP reads; never converts failure to results."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time

import requests

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2, 5)
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_WAIT_SECONDS = 30


def retry_policy():
    return {'max_attempts': MAX_ATTEMPTS, 'backoff_seconds': list(BACKOFF_SECONDS),
            'retryable_statuses': sorted(RETRYABLE_STATUSES),
            'max_retry_wait_seconds': MAX_RETRY_WAIT_SECONDS}


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


def get_patent(get, url, **kwargs):
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = get(url, **kwargs)
        except requests.exceptions.SSLError:
            raise
        except (requests.Timeout, requests.ConnectionError):
            if attempt + 1 == MAX_ATTEMPTS:
                raise
            time.sleep(BACKOFF_SECONDS[attempt])
            continue
        if response.status_code not in RETRYABLE_STATUSES or attempt + 1 == MAX_ATTEMPTS:
            return response
        delay = _delay(response, attempt)
        if delay is None:
            return response
        response.close()
        time.sleep(delay)
    raise AssertionError('Unreachable retry state')
