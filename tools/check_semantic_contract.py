"""Offline contract demo; does not run the semantic judge or evaluate patent quality."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.semantic_eval_examples import contract_demo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="新文件，不覆盖历史记录")
    args = parser.parse_args()
    output = args.output or ROOT / ".local/evaluations" / (
        "contract-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + ".json"
    )
    if output.exists():
        parser.error("输出文件已存在，请使用新文件名。")
    report = contract_demo()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"execution": report["execution"], "summary": report["summary"],
                      "report": str(output)}, ensure_ascii=False, indent=2))
    return 0 if all(row["status"] == "passed" for row in report["checks"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
