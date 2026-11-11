"""Isolate evidence judgments from retrieval. No gold input and no searching."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.novelty_models import PatentDocument, SourceSection
from modules.rsi_experiment import validate_inputs


def corpus_documents(path, cutoff):
    docs = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip(): continue
        row = json.loads(line)
        if set(row) - {"publication_id", "title", "url", "publication_date", "filing_date", "sections"}:
            raise ValueError("固定正文不能携带relevance、标签、金标或其他答案字段。")
        doc = PatentDocument(row["publication_id"], row["title"], row["url"], row["publication_date"],
                             tuple(SourceSection(**s) for s in row["sections"]),
                             filing_date=row.get("filing_date", ""), source_kind="patsnap_mcp_snapshot")
        if not doc.sections or not doc.publication_date or doc.publication_date >= cutoff:
            raise ValueError("固定正文缺失，或公开日不早于基准日。")
        docs.append(doc)
    if not docs or len({d.id for d in docs}) != len(docs):
        raise ValueError("固定正文须非空且公开号唯一。")
    return docs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--corpus", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--run-model", action="store_true")
    p.add_argument("--confirm-external", action="store_true")
    a = p.parse_args()
    if a.output_dir.exists(): p.error("输出目录须为新目录。")
    if a.run_model and not a.confirm_external: p.error("实际模型运行需确认公开输入及固定正文的外发。")
    inputs = validate_inputs(json.loads(a.input.read_text(encoding="utf-8-sig")))
    if len(inputs["cases"]) != 1 or inputs["cases"][0].get("scoring_profile") != "manual-v1.2":
        p.error("固定正文对照当前接收单案例manual-v1.2公开输入。")
    case = inputs["cases"][0]
    documents = corpus_documents(a.corpus, case["cutoff"])
    packet = {"schema": "fixed-corpus-evidence-v1", "execution": "not_executed", "public_input": inputs,
              "documents": [d.to_dict() for d in documents], "scope": "evidence_only_no_retrieval_metric",
              "input_sha256": hashlib.sha256(a.input.read_bytes()).hexdigest(),
              "corpus_sha256": hashlib.sha256(a.corpus.read_bytes()).hexdigest(),
              "note": "正文由外部固定提供；不能把覆盖25篇记为检索召回100%。缺独立复核时不产总分。"}
    a.output_dir.mkdir(parents=True, exist_ok=False)
    (a.output_dir / "frozen_inputs.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf8")
    if a.run_model:
        from modules.llm_client import LLMClient
        from modules.rsi_trial import ModelPatentTrial
        client = LLMClient(os.environ.get("LLM_API_KEY", ""), os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
                           os.environ.get("LLM_MODEL", "deepseek-flash"))
        settings = {"risk_levels": inputs["risk_levels"], "abstain_label": inputs["abstain_label"]}
        try:
            result = ModelPatentTrial(client).assess_fixed_corpus(case, settings, documents)
            (a.output_dir / "predictions.json").write_text(json.dumps([result], ensure_ascii=False, indent=2), encoding="utf8")
        except (RuntimeError, ValueError) as exc:
            (a.output_dir / "failure.json").write_text(json.dumps({"execution": "failed", "error": str(exc)}, ensure_ascii=False), encoding="utf8")
            return 2
    print(json.dumps({"execution": "completed" if a.run_model else "not_executed", "documents": len(documents),
                      "scope": packet["scope"], "output": str(a.output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
