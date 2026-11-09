"""Local, process-shared pacing, verified response cache and resumable plans."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

import requests


MIN_INTERVAL_SECONDS = 5
SEARCH_CACHE_SECONDS = 3600
DOCUMENT_CACHE_SECONDS = 30 * 86400
TEMPORARY_PAUSE_SECONDS = 300
STATE_PATH = Path(__file__).resolve().parents[1] / '.local/google-patents/network.sqlite3'


class PatentAccessPaused(RuntimeError):
    """A whole Google Patents batch must stop, retaining completed work."""


def blocked_page(response):
    # Inspect the response, including 200 challenge pages, before retry/parse/cache.
    if response.status_code not in {200, 403, 429, 503}:
        return False
    body = response.text.casefold()
    explicit = any(marker in body for marker in (
        'your computer or network may be sending automated queries',
        'our systems have detected unusual traffic',
    ))
    return explicit or ('/sorry/' in body and ('recaptcha' in body or 'unusual traffic' in body))


def request_key(url, kwargs):
    data = {'url': url, 'params': kwargs.get('params'), 'headers': kwargs.get('headers', {})}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class PatentNetworkState:
    def __init__(self, path=STATE_PATH, *, interval=MIN_INTERVAL_SECONDS, clock=time.time, sleep=time.sleep):
        self.path = Path(path)
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS control (
                    id INTEGER PRIMARY KEY CHECK(id=1), last_request REAL NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '', retry_at REAL NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO control(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS responses (
                    key TEXT PRIMARY KEY, fetched REAL NOT NULL, expires REAL NOT NULL,
                    url TEXT NOT NULL, headers TEXT NOT NULL, encoding TEXT, body BLOB NOT NULL,
                    sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, at REAL NOT NULL, key TEXT, url TEXT,
                    status INTEGER, outcome TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=45)
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @contextmanager
    def request_slot(self):
        # Keep the cross-process lock throughout the request: all callers share one lane.
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            last, reason, retry_at = con.execute(
                'SELECT last_request,reason,retry_at FROM control WHERE id=1').fetchone()
            if reason and (not retry_at or retry_at > self.clock()):
                raise PatentAccessPaused('Google Patents 已暂停：' + reason + '；已保存成功请求，请检查网络后显式恢复。')
            if reason:
                con.execute("UPDATE control SET reason='',retry_at=0 WHERE id=1")
            wait = max(0, last + self.interval - self.clock())
            if wait:
                self.sleep(wait)
            con.execute('UPDATE control SET last_request=? WHERE id=1', (self.clock(),))
            try:
                yield con
            finally:
                # Retain the reserved slot even if the network call raises a timeout.
                con.commit()

    def pause(self, reason, retry_at=0, *, con=None):
        if con is None:
            with self.connection() as connection:
                self.pause(reason, retry_at, con=connection)
        else:
            con.execute('UPDATE control SET reason=?,retry_at=? WHERE id=1', (reason, retry_at))

    def resume(self):
        with self.connection() as con:
            con.execute("UPDATE control SET reason='',retry_at=0 WHERE id=1")

    def status(self):
        with self.connection() as con:
            reason, retry_at = con.execute('SELECT reason,retry_at FROM control WHERE id=1').fetchone()
            counts = dict(con.execute('SELECT outcome,COUNT(*) FROM events GROUP BY outcome'))
        return {'paused': bool(reason and (not retry_at or retry_at > self.clock())),
                'reason': reason, 'retry_at': retry_at or None, 'events': counts,
                'minimum_interval_seconds': self.interval, 'state_path': str(self.path)}

    def event(self, key, url, status, outcome, *, con=None):
        if con is None:
            with self.connection() as connection:
                self.event(key, url, status, outcome, con=connection)
        else:
            con.execute('INSERT INTO events(at,key,url,status,outcome) VALUES(?,?,?,?,?)',
                        (self.clock(), key, url, status, outcome))

    def cached(self, key):
        with self.connection() as con:
            row = con.execute('SELECT fetched,expires,url,headers,encoding,body,sha256 FROM responses WHERE key=?', (key,)).fetchone()
        if row is None or row[1] <= self.clock():
            return None
        fetched, _, url, headers, encoding, body, checksum = row
        if hashlib.sha256(body).hexdigest() != checksum:
            raise ValueError('Google Patents 本地缓存校验失败；未使用或自动覆盖。')
        response = requests.Response()
        response.status_code, response.url, response.encoding = 200, url, encoding
        response.headers.update(json.loads(headers))
        response._content, response._content_consumed = body, True
        response.patent_origin = {'source': 'cache', 'fetched_at': fetched, 'request_key': key}
        self.event(key, urlsplit(url).path, 200, 'cache_hit')
        return response

    def save_response(self, response):
        origin = getattr(response, 'patent_origin', None)
        if not origin or origin['source'] != 'network' or response.status_code != 200:
            return
        path = urlsplit(response.url).path
        ttl = SEARCH_CACHE_SECONDS if path == '/xhr/query' else DOCUMENT_CACHE_SECONDS
        if path != '/xhr/query' and not path.startswith('/patent/'):
            return
        safe_headers = {k: v for k, v in response.headers.items()
                        if k.casefold() in {'content-type', 'content-language', 'etag', 'last-modified', 'date'}}
        with self.connection() as con:
            con.execute('INSERT OR REPLACE INTO responses VALUES(?,?,?,?,?,?,?,?)',
                        (origin['request_key'], origin['fetched_at'], origin['fetched_at'] + ttl,
                         response.url, json.dumps(safe_headers), response.encoding, response.content,
                         hashlib.sha256(response.content).hexdigest()))

    def load_checkpoint(self, key):
        with self.connection() as con:
            row = con.execute('SELECT value FROM checkpoints WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_checkpoint(self, key, value):
        with self.connection() as con:
            con.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?)',
                        (key, json.dumps(value, ensure_ascii=False)))

    def finish_checkpoint(self, key):
        with self.connection() as con:
            con.execute('DELETE FROM checkpoints WHERE key=?', (key,))


def save_verified_response(response):
    state = getattr(response, 'patent_state', None)
    if state is not None:
        state.save_response(response)


def original_retrieved_at(response):
    origin = getattr(response, 'patent_origin', None)
    return datetime.fromtimestamp(origin['fetched_at'], timezone.utc).isoformat() if origin else None
