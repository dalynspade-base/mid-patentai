"""Diagnose a frozen search journal offline; never issues searches or model calls."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.retrieval_evaluation import evaluate_retrieval


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--audit", type=Path, required=True)
    p.add_argument("--gold", type=Path, required=True)
    p.add_argument("--case-id", required=True)
    p.add_argument("--aliases", type=Path)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    if a.output_dir.exists():
        p.error("输出目录须为新目录。")
    read = lambda path: json.loads(path.read_text(encoding="utf-8-sig"))
    audit = read(a.audit)
    audit = audit.get("search_audit", audit)
    reference = next(c for c in read(a.gold)["cases"] if c["case_id"] == a.case_id)
    result = evaluate_retrieval(audit, reference["assessment"]["standard"],
                                aliases=read(a.aliases) if a.aliases else None)
    a.output_dir.mkdir(parents=True, exist_ok=False)
    (a.output_dir / "retrieval_diagnostics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({"execution": "completed", "scope": result["scope"], "output": str(a.output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
