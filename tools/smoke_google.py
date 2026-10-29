"""可选的真实只读连通性检查：只检索公开通用词，不调用模型或发送用户发明。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.google_patent_documents import fetch_google_document
from modules.patent_search import search_patents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="新运行目录；默认保存于.local/verification")
    args = parser.parse_args()
    output = args.output_dir or Path(__file__).resolve().parents[1] / ".local/verification" / (
        "google-smoke-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    )
    # Reserve a new destination before any network call.
    output.mkdir(parents=True, exist_ok=False)
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "query": '"pressure sensor"',
              "search_ok": False, "document_ok": False, "paid_model_called": False}
    try:
        records = search_patents(report["query"], max_results=1)
        report["search_ok"] = True
        report["candidates"] = len(records)
        if records:
            document = fetch_google_document(records[0]["url"])
            report.update({"document_ok": bool(document.sections), "url": document.url,
                           "publication_date": document.publication_date, "section_count": len(document.sections),
                           "complete": document.complete})
    except (RuntimeError, ValueError) as exc:
        report["error"] = str(exc)
    path = output / "live_google_smoke.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
