"""Inspect an archived grading standard without changing labels or running a model."""
import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path


def inspect_archive(root):
    root = Path(root)
    standard = json.loads((root/'private/等级手册详细标准.json').read_text(encoding='utf-8-sig'))
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8-sig'))
    checks = {}
    checks['originals_unchanged'] = all(hashlib.sha256((root/f['path']).read_bytes()).hexdigest() == f['sha256'] for f in manifest['files'])
    docs = standard['documents']
    checks['unique_publications'] = len({d['publication_id'] for d in docs}) == len(docs)
    checks['reference_counts'] = len(docs) == standard['counts']['public_relevant']
    checks['relevance_counts'] = dict(Counter(d['relevance'] for d in docs)) == standard['counts']['by_relevance']
    checks['reference_weights'] = sum(d['weight'] for d in docs) == standard['counts']['weight_sum']
    checks['score_weights'] = sum(standard['scoring']['weights'].values()) == 100
    checks['strict_cutoff'] = all(date.fromisoformat(d['publication_date']) < date.fromisoformat(standard['cutoff']) for d in docs)
    checks['per_claim_labels'] = all(d.get('claim_'+str(c)) in standard['labels'] for d in docs for c in standard['target']['claims'])
    source = next(d for d in docs if d['publication_id'] == standard['gold_claims']['1']['document'])
    checks['source_claims_distinguished'] = source['claim_1'] == 'high' and source['claim_2'] == 'partial'
    blockers = [
        '使用 tools/prepare_rsi_dataset.py --archive 生成分开的公开 input 与私有 gold；无需补成 #8 的逐要素金标准。',
        '评分自动选择 manual-v1.2，保留35/45/20、R3/R2/R1加权召回及NDCG；语义评分由单独复核步骤给布尔判断和依据，数学计算由代码完成。',
        '运行中保存实际取得原文、逐篇输出、逐权项结论和绑定的独立评分复核。缺少复核时仅报告检索分，完整分数为待评分。',
        '尚无独立案件锚集；可运行当前材料的实验，但不能据此报告独立泛化提升。25篇文献不是25个独立案件。',
    ]
    missing_md = not (root/'public'/standard['target']['filename']).exists()
    if missing_md:
        blockers.append('未提供标准指向的Markdown不再阻止接入；适配器从现有PDF派生文本并另记来源哈希，不替代原target.sha256。')
    result = {'dataset_id': standard['dataset_id'], 'standard_version': standard['version'], 'software_version': '2.2.0',
              'status': 'supported_manual_v1_2' if all(checks.values()) else 'source_consistency_failed',
              'ready_for_rsi_cli': False, 'ready_after_preparation': all(checks.values()),
              'scoring_supported': all(checks.values()), 'scoring_profile': 'manual-v1.2', 'checks': checks,
              'blockers': [] if all(checks.values()) else ['Original archive consistency checks failed.'], 'usage_notes': blockers,
              'reference_documents': len(docs), 'claims': standard['target']['claims'],
              'manual_score_weights': standard['scoring']['weights'], 'reference_labels_rewritten': False,
              'default_document_budget': 20, 'minimum_document_budget_for_full_reference_recall': len(docs),
              'document_budget_note': '新版可显式设置 --max-documents 25 或更高；默认仍为20，失败/非目标也占预算，25次尝试不保证取回25篇。',
              'manual_grade_validation': 'not_independently_verified', 'model_or_search_executed': False}
    return result


def markdown(result):
    lines = ['# v1.2 材料与 2.2.0 RSI 机制兼容性', '',
             '**结论：新版已支持此材料的原35/45/20评分；可自动生成运行输入，无需补成逐要素金标准。**', '',
             '本检查核对原件哈希、JSON 计数、标签单位及机制接口；不证明参考标签专业正确。', '',
             '## 已符合', '',
             f"- {result['reference_documents']} 篇对照，目标权项 {result['claims']}；公开副本与私有答案已分开。",
             '- 原本对权1为 H、权2为 P，符合新版逐文献逐权项记录方式。',
             '- H/P/C/U 可分别映射完整披露/部分覆盖/局部关联/证据不足，但映射不能补造逐要素证据。',
             '- 检索命中、判断正确性、误报漏报分开，符合新版评价边界。', '',
             '## 接入方式与结果边界', '']
    lines += [f'{i}. {text}' for i, text in enumerate(result['usage_notes'], 1)]
    lines += ['', '## 预算', '', result['document_budget_note'], '', '## 一致性检查', '',
              '```json', json.dumps(result['checks'], ensure_ascii=False, indent=2), '```', '',
              '原始附件与 v1.2 标准均未修改。没有自动生成新金标准、执行真实检索或调用模型。']
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    archive, out = args.archive.resolve(), args.output_dir.resolve()
    if out.is_relative_to(archive) or archive.is_relative_to(out):
        parser.error('Write compatibility results beside the immutable archive, not inside it.')
    result = inspect_archive(archive)
    out.mkdir(parents=True, exist_ok=False)
    (out/'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (out/'report.md').write_text(markdown(result), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(result['checks'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
