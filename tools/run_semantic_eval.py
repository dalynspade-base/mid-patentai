"""Freeze actual skill outputs before reading references; model judging is opt-in."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modules.skill_contracts import SKILLS,serialized,validate_record
from modules.semantic_eval import evaluation_packet,evaluation_markdown
from modules.semantic_eval_examples import teaching_records
from modules.semantic_eval import evaluate_records as run
from modules.semantic_handoff import handoff_packet


def main(default_skill=None):
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--records",type=Path,help="导出的报告JSON或技能记录数组")
    source.add_argument("--demo",action="store_true",help="仅生成教学评估计划，不执行模型")
    parser.add_argument("--skill",choices=list(SKILLS),default=default_skill)
    parser.add_argument("--output-dir",type=Path,required=True,help="新目录，不覆盖历史记录")
    parser.add_argument("--references",type=Path)
    parser.add_argument("--run-model",action="store_true")
    parser.add_argument("--confirm-external",action="store_true")
    args=parser.parse_args()
    if args.output_dir.exists(): parser.error("输出目录已存在。")
    if args.run_model and (not args.confirm_external or args.demo):
        parser.error("真实语义运行需要实际记录和--confirm-external；教学示例不能冒充实际运行。")
    data=json.loads(args.records.read_text(encoding="utf-8")) if args.records else teaching_records()
    records=data.get("skill_records") if isinstance(data,dict) else data
    if not isinstance(records,list) or not records: parser.error("缺少实际业务技能记录。")
    records=[validate_record(r) for r in records]
    if args.skill: records=[r for r in records if r["skill_id"]==args.skill]
    if not records: parser.error("没有指定技能的输出记录。")
    if len({r["skill_id"] for r in records})!=len(records): parser.error("单轮记录中技能重复。")
    args.output_dir.mkdir(parents=True,exist_ok=False)
    (args.output_dir/"frozen_skill_outputs.json").write_text(serialized(records),encoding="utf-8")
    refs=json.loads(args.references.read_text(encoding="utf-8")) if args.references else None
    if args.run_model:
        from modules.llm_client import LLMClient
        client=LLMClient(os.environ.get("LLM_API_KEY",""),os.environ.get("LLM_BASE_URL","https://api.deepseek.com"),
                         os.environ.get("LLM_MODEL","deepseek-flash"))
        report=run(records,client,True,refs,required_skills=[args.skill] if args.skill else None)
        (args.output_dir/"semantic_eval.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        (args.output_dir/"semantic_eval.md").write_text(evaluation_markdown(report),encoding="utf-8")
        print(json.dumps({"execution":report["execution"],"verdict":report["verdict"],"output":str(args.output_dir)},ensure_ascii=False))
        return 0 if report["execution"]=="completed" else 2
    plan={"execution":"not_executed","verdict":None,"evaluation_inputs":[evaluation_packet(r,(refs or {}).get(r["skill_id"])) for r in records],
          "handoff_input":handoff_packet(records) if not args.skill else None,
          "note":"仅生成外发预览；没有调用语义裁判，没有验收结论。"}
    (args.output_dir/"semantic_eval_plan.json").write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"execution":"not_executed","output":str(args.output_dir)},ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
