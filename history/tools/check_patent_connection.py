"""Check free patent search and full text without using an LLM or paid service."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import requests
from dotenv import load_dotenv
from modules.google_patent_documents import fetch_google_document
from modules.google_patents_transport import configured_entry
from modules.patent_http import retry_policy, default_state
from modules.patent_search import search_patents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', action='append')
    parser.add_argument('--patent-url', action='append', default=[])
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(ROOT/'.env', override=False)
    entry = configured_entry()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    calls, searches, documents = [], [], []

    def get(url, **kwargs):
        started = time.monotonic()
        record = {'requested_url': url, 'params': kwargs.get('params'),
                  'allow_redirects': kwargs.get('allow_redirects')}
        try:
            response = requests.get(url, **kwargs)
            record.update(http_status=response.status_code, final_url=response.url,
                          location=response.headers.get('Location'), response_bytes=len(response.content))
            return response
        except requests.RequestException as exc:
            record['error_type'] = type(exc).__name__
            raise
        finally:
            record['seconds'] = round(time.monotonic()-started, 3)
            calls.append(record)

    get.patent_network_state = default_state()
    for query in args.query or ['squeezed light rubidium']:
        record = {'query': query}
        try:
            rows = search_patents(query, max_results=3, get=get)
            record.update(status='completed', returned=len(rows), results=rows)
        except (ValueError, RuntimeError) as exc:
            record.update(status='failed', error_type=type(exc).__name__, message=str(exc))
        searches.append(record)
    urls = list(dict.fromkeys(args.patent_url))
    if not urls:
        first = searches[0].get('results', [])
        urls = [first[0]['url']] if first else []
    for url in urls:
        record = {'requested_url': url}
        try:
            doc = fetch_google_document(url, get=get)
            valid = bool(doc.sections and doc.parsed_complete and doc.publication_date and not doc.error)
            record.update(status='completed' if valid else 'insufficient_body_or_date',
                          publication_id=doc.id, publication_date=doc.publication_date,
                          section_count=len(doc.sections), snapshot_hash=doc.snapshot_hash,
                          body_characters=sum(len(s.text) for s in doc.sections))
            (args.output_dir/(doc.id+'.snapshot.json')).write_text(
                json.dumps(doc.to_dict() | {'snapshot_hash': doc.snapshot_hash}, ensure_ascii=False, indent=2),
                encoding='utf-8')
        except (ValueError, RuntimeError) as exc:
            record.update(status='failed', error_type=type(exc).__name__, message=str(exc))
        documents.append(record)
    passed = (bool(searches) and all(s['status']=='completed' for s in searches)
              and any(s.get('returned', 0) for s in searches)
              and bool(documents) and all(d['status']=='completed' for d in documents))
    sources = ['modules/patent_http.py', 'modules/google_patents_transport.py',
               'modules/patent_search.py', 'modules/google_patent_documents.py']
    report = {'checked_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'free_connectivity_not_a_scored_model_trial', 'model_executed': False,
              'paid_service_executed': False, 'search_entry': entry, 'http_retry_policy': retry_policy(),
              'all_checks_passed': passed, 'searches': searches, 'documents': documents, 'network_calls': calls,
              'source_sha256': {n: hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in sources}}
    (args.output_dir/'connectivity.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 免费专利检索连通验证', '', f"时间：{report['checked_at']}",
             f'入口：{entry}', '', '| 查询 | 状态 | 返回专利数 |', '|---|---|---|']
    lines += [f"| {s['query'].replace('|','／')} | {s['status']} | {s.get('returned','—')} |" for s in searches]
    lines += ['', '| 正文 | 状态 | 段落数 | 正文字符数 |', '|---|---|---|---|']
    lines += [f"| {d.get('publication_id', d['requested_url'])} | {d['status']} | {d.get('section_count','—')} | {d.get('body_characters','—')} |" for d in documents]
    lines += ['', '逐次请求状态、重试策略及原文快照哈希见 connectivity.json；原文另存 snapshot.json。',
              '本次没有调用模型或收费检索，不是质量评分测评。公网接口仍可能出现临时故障，重试用尽会明确失败。', '']
    (args.output_dir/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'all_checks_passed': passed, 'search_entry': entry,
                      'search_count': len(searches), 'document_count': len(documents),
                      'http_statuses': [c.get('http_status', c.get('error_type')) for c in calls],
                      'report': str(args.output_dir/'report.md')}, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == '__main__':
    raise SystemExit(main())
