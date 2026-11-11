"""Inspect local routing and transport state; opt-in one public search and one document."""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import requests
from modules.patent_http import default_state
from modules.patent_search import search_patents
from modules.google_patent_documents import fetch_google_document
from modules.patent_network_state import PatentAccessPaused


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true', help='最多一次逻辑公开检索及一次正文；不调用模型')
    parser.add_argument('--resume', action='store_true', help='仅在已人工确认网络恢复后显式清除暂停状态')
    parser.add_argument('--output', type=Path, help='新诊断JSON文件，不覆盖旧记录')
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error('诊断文件已存在，请使用新文件。')
    state = default_state()
    proxies = requests.utils.get_environ_proxies('https://patents.google.com/')
    # Report routing shape without recording proxy credentials or external addresses.
    report = {'model_executed': False, 'system_settings_changed': False,
              'routing': {scheme: {'configured': True, 'loopback': urlsplit(value).hostname in
                          {'127.0.0.1', 'localhost', '::1'}} for scheme, value in proxies.items()
                          if scheme in {'http', 'https', 'all'}},
              'before': state.status()}
    if args.resume:
        state.resume()
        report['explicit_resume'] = True
    if args.probe:
        try:
            rows = search_patents('"pressure sensor"', max_results=1)
            report.update(search_status='completed', returned=len(rows))
            if rows:
                doc = fetch_google_document(rows[0]['url'])
                report.update(document_status='completed' if doc.sections else 'missing_body',
                              publication=doc.id, section_count=len(doc.sections),
                              snapshot_hash=doc.snapshot_hash, retrieved_at=doc.retrieved_at)
        except PatentAccessPaused as exc:
            report.update(execution='paused', message=str(exc))
        except (RuntimeError, ValueError, requests.RequestException) as exc:
            report.update(execution='failed', error_type=type(exc).__name__, message=str(exc))
    report['after'] = state.status()
    report.setdefault('execution', 'completed' if args.probe else 'not_executed')
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report['execution'] in {'paused', 'failed'} else 0


if __name__ == '__main__':
    raise SystemExit(main())
