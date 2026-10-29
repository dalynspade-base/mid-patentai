"""Teaching records and judge-contract checks; never a model semantic benchmark."""
from copy import deepcopy
from modules.skill_contracts import DIMENSIONS,SKILLS,make_record,serialized
from modules.semantic_eval import evaluation_packet,validate_judgment


def teaching_records():
    raw="控制器仅在温度高于设定界限时启动冷却泵。"
    outputs={
        "patent-material-preparation":{"features":[{"id":"F1","text":raw}],"unknowns":[]},
        "patent-synonym-expansion":{"source":"控制器","term":"控制单元","relation":"equivalent","basis":"本题技术语境限定为泵控制器。"},
        "patent-candidate-search":{"query":raw,"candidate":"温度触发泵控制","scope":"给定教学原文；未在线检索"},
        "patent-evidence-alignment":{"quote":raw,"basis":"原文支持温度触发条件；同一控制方案。"},
        "patent-novelty-assessment":{"label":"insufficient_evidence","basis":"教学片段不证明完整现有技术范围及日期。"},
        "patent-report-delivery":{"markdown":"给定教学片段描述温度触发泵控制；材料范围不足以形成完整新颖性判断。"},
    }
    return [make_record(key,{"material":raw},outputs[key],sources=[{"id":"teaching_source","text":raw}],
        producer="teaching_fixture_not_actual_skill_run",note="作者编写的契约示例，不是真实执行或专业参考。") for key in SKILLS]


def judgment_example(record,*,violation=False):
    findings=[]
    for key in DIMENSIONS:
        bad=violation and key=="logic_constraints"
        findings.append({"dimension":key,"status":"violated" if bad else "satisfied",
            "basis":"教学核验示例：实际输出和原材料应结合比较；此文字不代表模型执行。",
            "citations":[{"source_id":"skill_output","quote":serialized(record["output"])},
                         {"source_id":"skill_input","quote":serialized(record["inputs"])}],
            "counterexample":"教学反例：把仅在条件下启动改为无条件启动。" if bad else "",
            "next_action":"恢复触发条件。" if bad else ""})
    return {"findings":findings,"activated_dimensions":list(DIMENSIONS),
            "verdict":"needs_revision" if violation else "acceptable","summary":"仅用于裁判契约自测。"}


def contract_demo():
    record=teaching_records()[0]
    packet=evaluation_packet(record)
    good=judgment_example(record)
    checks=[]
    for name,change in [
        ("接受完整契约",None),
        ("拒绝缺失维度",lambda d:d["findings"].pop()),
        ("拒绝伪造引文",lambda d:d["findings"][0]["citations"][0].update(quote="原文中不存在的教学引文")),
        ("拒绝数值评分字段",lambda d:d.update(score=1)),
        ("拒绝裁决与维度矛盾",lambda d:d.update(verdict="needs_revision")),
    ]:
        data=deepcopy(good)
        if change: change(data)
        try:
            validate_judgment(data,packet)
            valid=True
        except ValueError:
            valid=False
        checks.append({"check":name,"status":"passed" if valid==(change is None) else "failed"})
    return {"schema":"semantic-eval-contract-demo","execution":"not_executed","verdict":None,
        "summary":"离线契约示例已检查；没有执行业务技能语义评估或调用模型。",
        "checks":checks,"skills":list(SKILLS),"dimensions":DIMENSIONS,
        "provenance":"teaching_contract_examples_not_model_evaluation"}
