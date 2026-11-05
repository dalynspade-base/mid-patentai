"""Isolated temporary-Skill RSI: preview by default, explicit live execution only."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.rsi_experiment import SCHEMA, run_experiment, validate_inputs, validate_gold
from modules.rsi_limits import MAX_INPUT_CHARACTERS


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def teaching_dataset():
    inputs = {'schema': SCHEMA, 'dataset_id': 'explicit-teaching-only', 'provenance': 'teaching_demo',
        'risk_levels': ['低', '中', '高', '待核实'], 'abstain_label': '待核实', 'cases': [
            {'case_id': 'demo-copy-01', 'cutoff': '2024-01-01', 'text': '虚构教学副本：温度超过阈值时，控制器经无线链路启动冷却泵。'}]}
    targets = [{'target_id': f'T{i}', 'title': f'虚构教学专利结构{i}', 'publication_ids': [f'RSIDEMO{i:03}'],
                'title_aliases': [], 'publication_date': '2020-01-01'} for i in range(1, 4)]
    gold = {'schema': SCHEMA, 'dataset_id': inputs['dataset_id'], 'cases': [
        {'case_id': 'demo-copy-01', 'risk_label': '高', 'source_target_id': 'T1', 'targets': targets}]}
    return inputs, gold


class TeachingTrial:
    """Predetermined responses demonstrate plumbing, never model capability."""
    def __call__(self, skill, case, settings):
        improvements = skill.read_text(encoding='utf-8').count('演示改进项')
        documents = [{'publication_id': f'RSIDEMO{i:03}', 'title': f'虚构教学专利结构{i}',
                      'publication_date': '2020-01-01', 'url': f'https://example.invalid/demo/{i}'}
                     for i in range(1, min(3, improvements+1)+1)]
        return {'case_id': case['case_id'], 'execution': 'completed', 'retrieved': documents,
                'risk_label': '高' if improvements >= 2 else '待核实',
                'basis': '预设教学响应，不是模型判断。', 'queries': ['虚构教学查询']}


class TeachingProposer:
    def __init__(self):
        self.number = 0

    def __call__(self, feedback):
        self.number += 1
        return {'addition': f'演示改进项 {self.number}：扩展对象和关系的检索表达，保留触发条件，以原文核对机制。',
                'rationale': '仅演示临时文档增补及分数采纳流程。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='公开输入：修改副本及历史基准日')
    parser.add_argument('--gold', type=Path, help='私有参考：应找回名单、专利A身份、风险标签')
    parser.add_argument('--anchor-input', type=Path)
    parser.add_argument('--anchor-gold', type=Path)
    parser.add_argument('--output-dir', type=Path, help='新的 .local/rsi/运行目录，不覆盖旧结果')
    parser.add_argument('--rounds', type=int, default=5, choices=range(1, 6))
    parser.add_argument('--max-queries', type=int, default=4)
    parser.add_argument('--max-documents', type=int, default=20)
    parser.add_argument('--retrieval-weight', type=float, default=None)
    parser.add_argument('--run-model', action='store_true')
    parser.add_argument('--confirm-external', action='store_true')
    parser.add_argument('--demo', action='store_true', help='只用虚构数据和预设响应，无网络、无模型')
    args = parser.parse_args()
    if args.demo and (args.run_model or args.input or args.gold or args.anchor_input or args.anchor_gold):
        parser.error('Demo cannot be mixed with real data/model execution.')
    if bool(args.anchor_input) != bool(args.anchor_gold):
        parser.error('Both anchor input and gold are required.')
    if (args.retrieval_weight is not None and not 0 <= args.retrieval_weight <= 1) or not 1 <= args.max_queries <= 20 or not 1 <= args.max_documents <= 100:
        parser.error('Invalid frozen score weights or retrieval budget.')
    if args.demo:
        inputs, gold = teaching_dataset()
    elif args.input and args.gold:
        inputs, gold = validate_inputs(load(args.input)), load(args.gold)
        gold = validate_gold(inputs, gold)
    else:
        parser.error('Provide completed --input and --gold, or use --demo.')
    budget = {'max_queries': args.max_queries, 'max_documents': args.max_documents}
    profiles = [c.get('assessment', {}).get('profile', 'legacy-label-v1') for c in gold['cases']]
    handbook = profiles == ['manual-v1.2'] or set(profiles) == {'manual-v1.2'}
    if handbook and args.retrieval_weight is not None:
        parser.error('manual-v1.2 fixes weights at 35/45/20; --retrieval-weight does not apply.')
    retrieval_weight = 0.5 if args.retrieval_weight is None else args.retrieval_weight
    weights = (retrieval_weight, 1-retrieval_weight)
    component_weights = {'retrieval':35,'document_evidence':45,'claim_conclusion':20} if handbook else {'retrieval':weights[0],'risk':weights[1]}
    case_count = len(inputs['cases'])
    if not args.run_model and not args.demo:
        print(json.dumps({'execution': 'not_executed', 'cases': case_count, 'expected_per_case': [len(c['targets']) for c in gold['cases']],
            'rounds': args.rounds, 'paired_trials_per_case': 1+2*args.rounds,
            'max_model_requests_without_anchor': sum((1+args.max_documents+2 + args.max_documents+2
                if c.get('scoring_profile')=='manual-v1.2' else 1 + args.max_documents * len(c['assessment_targets'])
                if c.get('assessment_targets') else 2 + args.max_documents) * (1+2*args.rounds) for c in inputs['cases']) + args.rounds,
            'budget_per_trial': budget, 'component_weights': component_weights,
            'max_input_characters': MAX_INPUT_CHARACTERS,
            'scoring_profiles': profiles,
            'private_review_external': handbook,
            'note': '仅验证与预览；尚未调用检索、模型或生成真实分数。'}, ensure_ascii=False, indent=2))
        return 0
    if not args.output_dir:
        parser.error('Execution requires a new --output-dir.')
    if args.run_model and not args.confirm_external:
        parser.error('Live model/search calls require --confirm-external.')
    assessor = None
    if args.demo:
        predictor, proposer = TeachingTrial(), TeachingProposer()
        runtime = {'adapter': 'scripted_teaching_no_model'}
    else:
        from dotenv import load_dotenv
        from modules.llm_client import LLMClient
        from modules.rsi_trial import ModelPatentTrial, ModelSkillProposer
        from modules.google_patents_transport import configured_entry
        from modules.patent_http import retry_policy
        load_dotenv(ROOT/'.env', override=False)
        client = LLMClient(os.getenv('LLM_API_KEY', ''), os.getenv('LLM_BASE_URL', 'https://api.deepseek.com'), os.getenv('LLM_MODEL', ''))
        predictor, proposer = ModelPatentTrial(client), ModelSkillProposer(client)
        if handbook:
            from modules.rsi_rubric_review import ModelRubricAssessor
            assessor = ModelRubricAssessor(client)
        runtime = {'adapter': 'ModelPatentTrial', 'model': client.model,
                   'provider_host': urlsplit(client.url).hostname, 'temperature': 0.2,
                   'search_entry': configured_entry(), 'http_retry_policy': retry_policy()}
    anchor = (lambda: (load(args.anchor_input), load(args.anchor_gold))) if args.anchor_input else None
    result = run_experiment(inputs, gold, args.output_dir, predictor, proposer,
        formal_root=ROOT/'.agents/skills', rounds=args.rounds, budget=budget, weights=weights,
        mode='teaching_demo' if args.demo else 'live', anchor=anchor, runtime=runtime, assessor=assessor)
    print(json.dumps({'execution': result['execution'], 'mode': result['mode'], 'selected_round': result['selected_round'],
        'accepted_rounds': result['accepted_rounds'], 'report': str(args.output_dir/'result.md'),
        'note': '教学分数为预设响应产生。' if args.demo else '同一集合优化，独立泛化情况见报告。'}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'RSI stopped: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2)
