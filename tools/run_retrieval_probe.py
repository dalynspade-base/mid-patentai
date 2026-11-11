"""Freeze and optionally run a public-only PatSnap retrieval trial, without judging."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.novelty_models import Feature, PATSNAP_SOURCE_SCOPE
from modules.novelty_matching import SynonymLexicon
from modules.novelty_search import query_plan, retrieve_with_journal
from modules.patent_context import context_for
from modules.rsi_experiment import validate_inputs
from modules.synonym_expansion import build_expansion_plan, generate_llm_candidates, feature_hash


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--expansion', type=Path, help='已保存并审核的扩展计划')
    source.add_argument('--generate-terms', action='store_true', help='模型生成概念与候选，并单独核验等义')
    p.add_argument('--run-search', action='store_true')
    p.add_argument('--confirm-external', action='store_true')
    p.add_argument('--max-queries', type=int, default=8)
    p.add_argument('--results-per-query', type=int, default=25)
    p.add_argument('--max-documents', type=int, default=25)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args()
    if a.output_dir.exists(): p.error('输出目录须为新目录。')
    if (a.run_search or a.generate_terms) and not a.confirm_external:
        p.error('实际术语生成或智慧芽检索须先确认公开输入和查询的外发。')
    if not 1 <= a.max_queries <= 16 or not 1 <= a.results_per_query <= 25 or not 1 <= a.max_documents <= 100:
        p.error('查询预算1–16，每条结果1–25，正文尝试预算1–100。')
    public = validate_inputs(json.loads(a.input.read_text(encoding='utf-8-sig')))
    if len(public['cases']) != 1: p.error('一次检索诊断接收一个公开案例。')
    case = public['cases'][0]
    features = [Feature(t['id'], t['text']) for t in case.get('assessment_targets', [])] or [Feature('1', case['text'])]
    lexicon = SynonymLexicon()
    a.output_dir.mkdir(parents=True)

    def save(name, value):
        (a.output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')

    save('frozen_configuration.json', {'schema': 'retrieval-probe-v1', 'public_input': public,
        'max_queries': a.max_queries, 'results_per_query': a.results_per_query,
        'max_documents': a.max_documents, 'compiler': 'patsnap-concepts-v2',
        'fusion': 'query-round-robin-v1', 'gold_access': False})
    try:
        if a.expansion:
            raw = json.loads(a.expansion.read_text(encoding='utf-8-sig'))
            if raw.get('feature_hash') != feature_hash(features):
                raise ValueError('扩展计划与公开要素不匹配。')
            plan = build_expansion_plan(features, lexicon, raw['profile'], raw.get('protected_terms'),
                                        raw.get('generation'), raw.get('approved_candidate_ids'))
        else:
            from modules.llm_client import LLMClient
            from modules.rsi_trial import ModelPatentTrial
            client = LLMClient(os.environ.get('LLM_API_KEY', ''), os.environ.get('LLM_BASE_URL', 'https://api.deepseek.com'),
                               os.environ.get('LLM_MODEL', ''))
            context = context_for(features, case['cutoff'], invention=case['text'], sources=PATSNAP_SOURCE_SCOPE)
            generated = generate_llm_candidates(features, lexicon, 'engineering', [], client, True, patent_context=context)
            if generated['status'] != 'ok': raise RuntimeError(generated.get('error', '术语生成失败'))
            plan = build_expansion_plan(features, lexicon, 'engineering', generated=generated)
            pending = [r for r in plan['candidates'] if r['status'] == 'pending']
            approved = ModelPatentTrial(client)._review_terms(features, pending) if pending else []
            plan = build_expansion_plan(features, lexicon, 'engineering', generated=generated, approved_ids=approved)
        save('expansion.json', plan)
        queries = query_plan(features, '', lexicon, plan, compact=True, max_queries=a.max_queries, before=case['cutoff'])
        save('query_preview.json', queries)
        if a.run_search:
            from modules.patsnap_mcp import PatsnapMCP
            platform = PatsnapMCP()
            docs, audit = retrieve_with_journal(features, case['cutoff'], '', lexicon, platform.search, platform.fetch,
                a.max_documents, plan, provider='patsnap', results_per_query=a.results_per_query,
                max_queries=a.max_queries, before=case['cutoff'])
            save('search_audit.json', audit)
            save('source_snapshots.json', [d.to_dict() for d in docs])
            failed = any(q['status'] != 'ok' for q in audit['queries']) or any(c['fetch_status'] == 'error' for c in audit['candidates'])
            if failed:
                save('summary.json', {'execution': 'failed', 'gold_access': False, 'judgment': None})
                return 2
        save('summary.json', {'execution': 'completed' if a.run_search else 'not_executed',
                               'term_generation': 'completed' if a.generate_terms else 'provided_reviewed_plan',
                               'gold_access': False, 'judgment': None})
        print(json.dumps({'output': str(a.output_dir), 'search_executed': a.run_search, 'gold_access': False}, ensure_ascii=False))
        return 0
    except (RuntimeError, ValueError) as exc:
        save('failure.json', {'execution': 'failed', 'error': str(exc)})
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
