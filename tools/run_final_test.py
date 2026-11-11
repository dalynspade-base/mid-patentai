"""Collect engineering checks, dataset audit and evidence/Eval previews separately."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.rsi_experiment import validate_inputs, validate_gold
from modules.semantic_eval import evaluation_packet
from modules.semantic_handoff import handoff_packet
from modules.skill_contracts import validate_record


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def audit_dataset(directory):
    read = lambda name: json.loads((directory / name).read_text(encoding='utf-8-sig'))
    public, gold = validate_inputs(read('public/input.json')), read('private/gold.json')
    validate_gold(public, gold)
    provenance = read('provenance.json')
    hashes = {str(p.relative_to(directory)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(directory.rglob('*')) if p.is_file()}
    batches = [read(f'grades/grade-batch-{i}.json') for i in (1, 2, 3)]
    sets = [{r['publication_id'] for r in b['documents']} for b in batches]
    docs = [json.loads(l) for l in (directory / 'corpus.jsonl').read_text(encoding='utf-8-sig').splitlines() if l.strip()]
    standard = gold['cases'][0]['assessment']['standard']
    standard_ids = {d['publication_id'] for d in standard['documents']}
    corpus_ids = {d['publication_id'] for d in docs}
    if len(corpus_ids) != len(docs) or corpus_ids != standard_ids:
        raise ValueError('金标与固定正文身份不一致或重复。')
    if any(d['publication_date'] >= public['cases'][0]['cutoff'] or not d['sections'] for d in docs):
        raise ValueError('固定正文存在日期或正文缺口。')
    return {'schema': 'dataset-audit-v1', 'validation': 'passed', 'dataset_id': public['dataset_id'],
            'cases': len(public['cases']), 'documents': len(docs), 'sections': sum(len(d['sections']) for d in docs),
            'counts': standard['counts'], 'actual_file_sha256': hashes,
            'source_provenance_hash_matches': {name: hashes.get(name) == expected for name, expected in provenance.get('sha256', {}).items()},
            'review_batches': [{'batch': b['reviewer']['batch'], 'documents': len(b['documents']),
                                'declared_model': b['reviewer']['declared_model'],
                                'relevance_counts': dict(Counter(r['relevance'] for r in b['documents']))} for b in batches],
            'cross_reviewed_documents': len((sets[0] & sets[1]) | (sets[0] & sets[2]) | (sets[1] & sets[2])),
            'consensus_established': False,
            'dataset_role': 'development_pool_not_unseen_holdout',
            'note': '原始provenance保留；本文件记录实际字节指纹。模型分批标注不冒称共识；未判文献不当负例。'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--handover', type=Path, help='可选：已分析的交接ZIP，按原字节保存指定数据集')
    p.add_argument('--output-dir', type=Path, default=ROOT / 'final_test' / datetime.now().strftime('run-%Y%m%d-%H%M%S'))
    p.add_argument('--skip-engineering', action='store_true', help='仅重建数据/Eval预览，不冒称执行工程检查')
    a = p.parse_args()
    if a.output_dir.exists(): p.error('输出目录已存在，使用新运行目录。')
    a.output_dir.mkdir(parents=True)
    summary = {'schema': 'final-test-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
               'engineering': {'execution': 'not_executed'},
               'live_patsnap_retrieval': {'execution': 'not_executed', 'recall': None},
               'fixed_corpus_evidence': {'execution': 'not_executed', 'verdict': None},
               'independent_semantic_eval': {'execution': 'not_executed', 'verdict': None},
               'rsi': {'execution': 'not_executed', 'gain': None}}
    if not a.skip_engineering:
        output = a.output_dir / 'engineering'
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ROOT / 'tools/run_verification.py'),
                                 '--output-dir', str(output)], cwd=ROOT, capture_output=True, text=True, encoding='utf8')
        (a.output_dir / 'engineering-process.log').write_text(result.stdout + result.stderr, encoding='utf8')
        summary['engineering'] = {'execution': 'completed' if result.returncode == 0 else 'failed',
                                  'returncode': result.returncode,
                                  'verification': json.loads((output / 'verification.json').read_text(encoding='utf8'))
                                  if (output / 'verification.json').exists() else None}
    if a.handover:
        summary['handover_sha256'] = hashlib.sha256(a.handover.read_bytes()).hexdigest()
        with zipfile.ZipFile(a.handover) as archive:
            prefix = 'artifacts/dataset-patsnap-gold-v1-20261111/'
            dataset = a.output_dir / 'dataset'
            for n in archive.namelist():
                if n.startswith(prefix) and not n.endswith('/'):
                    relative = Path(n[len(prefix):])
                    destination = (dataset / relative).resolve()
                    if not destination.is_relative_to(dataset.resolve()): raise ValueError('归档路径越界。')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(n))
            summary['dataset_audit'] = audit_dataset(dataset)
            write(a.output_dir / 'dataset_audit.json', summary['dataset_audit'])
            cmd = [sys.executable, '-B', '-X', 'utf8', str(ROOT / 'tools/run_fixed_corpus.py'),
                   '--input', str(dataset / 'public/input.json'), '--corpus', str(dataset / 'corpus.jsonl'),
                   '--output-dir', str(a.output_dir / 'fixed_corpus_preview')]
            completed = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding='utf8')
            (a.output_dir / 'fixed-corpus-process.log').write_text(completed.stdout + completed.stderr, encoding='utf8')
            if completed.returncode: raise RuntimeError('固定正文预览构造失败，见fixed-corpus-process.log。')
            readiness = {}
            for label, name in [('delivered', 'artifacts/case-host-smoke-20261110/state.json'),
                                ('early_smoke', 'artifacts/verification/host-agent-smoke-20261110/state.json')]:
                state = json.loads(archive.read(name).decode('utf-8-sig'))
                records = [validate_record(r) for r in state['skill_records']]
                write(a.output_dir / 'semantic_previews' / label / 'frozen_skill_outputs.json', records)
                plan = {'execution': 'not_executed', 'verdict': None, 'source': 'historical_frozen_records',
                        'record_count': len(records)}
                try:
                    plan.update(evaluation_inputs=[evaluation_packet(r) for r in records],
                                handoff_input=handoff_packet(records), readiness='preview_ready')
                except ValueError as exc:
                    if 'Skill 文档' not in str(exc): raise
                    plan.update(readiness='blocked_stale_skill', evaluation_inputs=None, handoff_input=None,
                                reason=str(exc), note='保留旧指纹；新版须重跑业务再评。')
                readiness[label] = plan['readiness']
                write(a.output_dir / 'semantic_previews' / label / 'semantic_eval_plan.json', plan)
            summary['independent_semantic_eval']['historical_record_readiness'] = readiness
    write(a.output_dir / 'summary.json', summary)
    engineering = summary['engineering'].get('verification') or {}
    groups = engineering.get('groups', {})
    lines = ['# 本轮测试结果', '', f"运行目录：`{a.output_dir.name}`。", '',
             '| 项目 | 实际状态 |', '|---|---|',
             f"| 当前工程 | {groups.get('current_engineering', {})} |",
             f"| 历史回归 | {groups.get('legacy_engineering', {})} |",
             '| 智慧芽真实检索 | 未执行；召回率未测量 |',
             '| 固定正文判断 | 仅冻结输入；未调用模型 |',
             '| 独立语义 Eval | 检查历史记录绑定；旧Skill指纹不符时须重跑业务，无裁判结论 |',
             '| RSI | 未执行；没有增益结论 |', '',
             '工程通过不证明业务语义正确。固定提供正文不能计作检索召回。原始数据及私有答案留在本地。']
    (a.output_dir / 'README.md').write_text('\n'.join(lines) + '\n', encoding='utf8')
    print(json.dumps({'output': str(a.output_dir), 'engineering': summary['engineering']['execution'],
                      'live_retrieval': 'not_executed', 'semantic_eval': 'not_executed'}, ensure_ascii=False))
    return 0 if summary['engineering']['execution'] != 'failed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
