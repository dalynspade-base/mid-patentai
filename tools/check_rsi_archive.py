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
        '这是评分手册格式，不是 RSI input/gold 接口；需要另存适配文件，不能直接作为 --gold。',
        '需要从副本提取并核对两条完整目标权项及逐要素 ID/文本，形成公开 assessment_targets。',
        '需要逐文献、逐权项的 feature_status 和 evidence_sections，绑定实际原文快照位置；概括性的 overlap/gap/evidence_location 不能自动变成已核验要素证据。',
        '评分手册的 35/45/20、R3/R2/R1 加权召回和 NDCG，与 evidence-v2 的检索比例和证据分不是同一公式；保留手册做独立评分，转换评分口径需明确标记并另定版本。',
        '尚无独立案件锚集；两条权项与 25 篇对照属于同一副本，不能当成 25 个独立案件，也不能用同案文献分池替代本地锚集。',
    ]
    missing_md = not (root/'public'/standard['target']['filename']).exists()
    if missing_md:
        blockers.append('标准中的 target.sha256 指向未提供的 Markdown；PDF 原件哈希已核对，但二者不能互相替代，派生输入须另记 PDF 来源哈希。')
    result = {'dataset_id': standard['dataset_id'], 'standard_version': standard['version'], 'software_version': '2.2.0',
              'status': 'compatible_source_requires_explicit_adapter' if all(checks.values()) else 'source_consistency_failed',
              'ready_for_rsi_cli': False, 'checks': checks, 'blockers': blockers,
              'reference_documents': len(docs), 'claims': standard['target']['claims'],
              'manual_score_weights': standard['scoring']['weights'], 'reference_labels_rewritten': False,
              'default_document_budget': 20, 'minimum_document_budget_for_full_reference_recall': len(docs),
              'document_budget_note': '新版可显式设置 --max-documents 25 或更高；默认仍为20，失败/非目标也占预算，25次尝试不保证取回25篇。',
              'manual_grade_validation': 'not_independently_verified', 'model_or_search_executed': False}
    return result


def markdown(result):
    lines = ['# v1.2 材料与 2.2.0 RSI 机制兼容性', '',
             '**结论：材料可继续使用，原评分标准保留；尚不能直接作为新版自动评测输入。**', '',
             '本检查核对原件哈希、JSON 计数、标签单位及机制接口；不证明参考标签专业正确。', '',
             '## 已符合', '',
             f"- {result['reference_documents']} 篇对照，目标权项 {result['claims']}；公开副本与私有答案已分开。",
             '- 原本对权1为 H、权2为 P，符合新版逐文献逐权项记录方式。',
             '- H/P/C/U 可分别映射完整披露/部分覆盖/局部关联/证据不足，但映射不能补造逐要素证据。',
             '- 检索命中、判断正确性、误报漏报分开，符合新版评价边界。', '',
             '## 正式接入前需要', '']
    lines += [f'{i}. {text}' for i, text in enumerate(result['blockers'], 1)]
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
