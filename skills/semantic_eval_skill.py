"""独立语义评估skill。与六个业务skill分别调用，不读取凭据或自动发送材料。"""
from modules.semantic_eval import evaluate_records,evaluate_skill,evaluation_markdown,evaluation_packet
from modules.skill_contracts import DIMENSIONS,SKILLS


def run(records,client,confirmed=False,references=None,*,required_skills=None):
    return evaluate_records(records,client,confirmed,references,required_skills=required_skills)
