"""A scoped semantic comparison of handoffs; not a ninth business skill."""
from modules.skill_library import skill_prompt, skill_snapshot
from modules.skill_contracts import DIMENSIONS, SKILLS, digest, serialized
from modules.patent_context import CONDITIONAL_DIMENSIONS, handoff_issues


def handoff_packet(records):
    if {r["skill_id"] for r in records} != set(SKILLS):
        return None
    # Old frozen records remain readable; missing context is explicitly reported, never invented.
    if not all(r["inputs"].get("patent_context") for r in records):
        return None
    inputs, outputs = {}, {}
    for record in records:
        key=record["skill_id"]
        inputs[key]={k:v for k,v in record["inputs"].items() if k not in {"documents","reviews","reading_hints"}}
        inputs[key]["source_bindings"]=[{"id":s["id"],"text_hash":digest(s["text"])} for s in record["sources"]]
        output=dict(record["output"])
        if "documents" in output:
            output["documents"]=[{k:v for k,v in d.items() if k!="sections"} for d in output["documents"]]
        outputs[key]=output
    frozen={"inputs":inputs,"output":outputs}
    checks=handoff_issues(records)
    required=["structure_relations","logic_constraints","context_reference","evaluation_expression"]
    for record in records:
        for dimension in record["inputs"]["patent_context"].get("semantic_focus",[]):
            if dimension not in required:
                required.append(dimension)
    return {"kind":"cross_skill_handoff", "record":frozen, "dimensions":DIMENSIONS,
        "judge_document":skill_snapshot("patent-semantic-eval"),
        "required_dimensions":required,"conditional_dimensions":CONDITIONAL_DIMENSIONS,
        "obligations":"核验六步之间的含义传递：原限定是否被扩展词改写、候选是否升级为披露、未知和条件是否丢失、结论是否被报告放大。版本、基准日、范围及上游绑定必须一致。",
        "context_contract":{"needs":["六步实际记录的衔接投影"],
            "boundary":"此投影去除重复原文，不替代各skill的全文评估；不得重做新颖性法律判断。"},
        "binding_checks":checks,"record_hashes":[r["record_hash"] for r in records],
        "sources":[{"id":"skill_input","text":serialized(inputs),"location":"六步实际输入的衔接投影"},
                   {"id":"skill_output","text":serialized(outputs),"location":"六步实际输出的衔接投影"}],
        "reference":None,"reference_status":"none"}


def evaluate_handoff(packet,client):
    from modules.json_output import parse_object
    from modules.semantic_eval import JUDGE_SYSTEM, validate_judgment, _identity
    data=validate_judgment(parse_object(client.complete(skill_prompt("patent-semantic-eval", JUDGE_SYSTEM),serialized(packet),max_tokens=10000)),packet)
    # A judge cannot override a demonstrable binding contradiction with a semantic acceptance.
    issues=packet["binding_checks"]
    if any(i["status"]=="violated" for i in issues):
        finding=next(f for f in data["findings"] if f["dimension"]=="context_reference")
        finding.update(status="violated",basis="；".join(i["basis"] for i in issues),
            citations=[{"source_id":s["id"],"quote":s["text"]} for s in packet["sources"]],
            counterexample="当前步骤的基准日、范围、版本或上游输出绑定存在不一致。",next_action="回到最早不一致的步骤重建并重新评估后续输出。")
        data["verdict"]="needs_revision"
        data=validate_judgment(data,packet)
    return data | {"execution":"completed","input_hash":digest(packet),"judge":_identity(client),
                   "provenance":"model_semantic_judge_not_human"}
