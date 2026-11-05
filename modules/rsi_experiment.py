"""Bounded text-only RSI experiment; production semantic eval is unchanged."""
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import unicodedata

SCHEMA = 'patent-rsi-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def title_key(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def publication_key(text):
    return re.sub(r'[^A-Z0-9]', '', text.upper())


def validate_inputs(inputs):
    if set(inputs) != {'schema', 'dataset_id', 'provenance', 'risk_levels', 'abstain_label', 'cases'}:
        raise ValueError('Input fields must be schema, dataset_id, provenance, risk_levels, abstain_label, cases.')
    if inputs['schema'] != SCHEMA or inputs['provenance'] not in {'provided_dataset', 'teaching_demo'}:
        raise ValueError('Use a completed provided dataset or explicit teaching demo, not an unfilled template.')
    levels = inputs['risk_levels']
    if not isinstance(levels, list) or len(levels) < 2 or any(not isinstance(s, str) or not s for s in levels) or len(set(levels)) != len(levels):
        raise ValueError('Risk levels must be distinct strings.')
    if inputs['abstain_label'] not in levels or not inputs['cases']:
        raise ValueError('Supply cases and an explicit abstention label.')
    ids = set()
    for case in inputs['cases']:
        if set(case) != {'case_id', 'cutoff', 'text'} or not all(isinstance(v, str) and v.strip() for v in case.values()):
            raise ValueError('Public cases contain only opaque case_id, cutoff and modified text.')
        date.fromisoformat(case['cutoff'])
        if case['case_id'] in ids:
            raise ValueError('Duplicate case id.')
        ids.add(case['case_id'])
    return deepcopy(inputs)


def validate_gold(inputs, gold):
    if set(gold) != {'schema', 'dataset_id', 'cases'} or gold['schema'] != SCHEMA or gold['dataset_id'] != inputs['dataset_id']:
        raise ValueError('Private gold dataset does not match inputs.')
    public = {c['case_id']: c for c in inputs['cases']}
    if not isinstance(gold['cases'], list) or len(gold['cases']) != len(public) or {c['case_id'] for c in gold['cases']} != set(public):
        raise ValueError('Every case needs exactly one private reference.')
    for case in gold['cases']:
        if set(case) != {'case_id', 'risk_label', 'source_target_id', 'targets'} or case['risk_label'] not in inputs['risk_levels']:
            raise ValueError('Invalid private reference fields or risk label.')
        targets = case['targets']
        if not isinstance(targets, list) or not targets or len({t['target_id'] for t in targets}) != len(targets):
            raise ValueError('Provide a nonempty, deduplicated list of all patents that should be retrieved.')
        if case['source_target_id'] not in {t['target_id'] for t in targets}:
            raise ValueError('Original patent A must be one of the expected targets.')
        identities = set()
        for target in targets:
            if set(target) != {'target_id', 'title', 'publication_ids', 'title_aliases', 'publication_date'}:
                raise ValueError('Invalid target fields.')
            if not isinstance(target['title'], str) or not title_key(target['title']):
                raise ValueError('Every target needs a patent title.')
            for key in ('publication_ids', 'title_aliases'):
                if not isinstance(target[key], list) or any(not isinstance(s, str) or not s.strip() for s in target[key]):
                    raise ValueError('Aliases must be explicit nonempty strings.')
            if target['publication_date'] and date.fromisoformat(target['publication_date']) > date.fromisoformat(public[case['case_id']]['cutoff']):
                raise ValueError('A target postdates the historical cutoff; correct the reference before running.')
            keys = {'id:'+publication_key(s) for s in target['publication_ids']}
            keys.update('title:'+title_key(s) for s in [target['title'], *target['title_aliases']])
            if identities & keys:
                raise ValueError('Target aliases overlap; merge duplicate/family entries explicitly.')
            identities.update(keys)
    return deepcopy(gold)


def match_target(document, target):
    if target['publication_ids']:
        return publication_key(document['publication_id']) in {publication_key(s) for s in target['publication_ids']}
    return title_key(document['title']) in {title_key(s) for s in [target['title'], *target['title_aliases']]}


def score_predictions(inputs, gold, predictions, weights):
    references = {c['case_id']: c for c in gold['cases']}
    if len(predictions) != len(inputs['cases']) or {p['case_id'] for p in predictions} != set(references):
        raise ValueError('Missing or duplicate predictions.')
    rows = []
    for case in inputs['cases']:
        reference = references[case['case_id']]
        prediction = next(p for p in predictions if p['case_id'] == case['case_id'])
        if prediction['execution'] != 'completed' or prediction['risk_label'] not in inputs['risk_levels']:
            raise ValueError('Incomplete/failed trials are not scored or accepted.')
        documents = prediction['retrieved']
        for doc in documents:
            if not all(isinstance(doc.get(k), str) and doc[k].strip() for k in ('publication_id', 'title', 'publication_date', 'url')):
                raise ValueError('Retrieved papers need identity, title, URL and actual publication date.')
            if date.fromisoformat(doc['publication_date']) > date.fromisoformat(case['cutoff']):
                raise ValueError('Retrieved future evidence cannot earn historical retrieval credit.')
        found = [t['target_id'] for t in reference['targets'] if any(match_target(d, t) for d in documents)]
        count = len(reference['targets'])
        retrieval = 100 * len(found) / count
        risk = 100.0 if prediction['risk_label'] == reference['risk_label'] else 0.0
        rows.append({'case_id': case['case_id'], 'found': len(found), 'expected': count,
                     'matched_target_ids': found, 'retrieval_score': retrieval, 'risk_score': risk,
                     'source_A_found': reference['source_target_id'] in found})
    retrieval = sum(r['retrieval_score'] for r in rows) / len(rows)
    risk = sum(r['risk_score'] for r in rows) / len(rows)
    return {'retrieval_score': retrieval, 'risk_score': risk,
            'total_score': weights[0] * retrieval + weights[1] * risk, 'cases': rows}


def _formal_fingerprints(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}


def _make_skill(path, base, additions, feedback):
    body = ('---\nname: patent-rsi-trial\ndescription: 在固定检索预算内分析修改副本的新颖性风险，仅用于本轮隔离实验。\n---\n\n'
            '# 临时 RSI Skill\n\n## 不变的基线指令\n\n'+base+'\n\n## 已保留的补充指令\n\n'
            +'\n\n'.join(additions or ['尚无补充。'])+'\n\n## 先前训练反馈\n\n'
            +json.dumps(feedback, ensure_ascii=False, sort_keys=True)+'\n')
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(body, encoding='utf-8')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _feedback(score):
    # Aggregate feedback only: no target identities, patent A identity or expected risk labels.
    return {key: score[key] for key in ('retrieval_score', 'risk_score', 'total_score')} | {
        'retrieval_gap': any(r['found'] < r['expected'] for r in score['cases']),
        'risk_mismatch': any(r['risk_score'] == 0 for r in score['cases'])}


def _optimizer_predictions(predictions):
    # Full source snapshots stay in the trial files; the optimizer sees actual summaries/quotes.
    return [{k: deepcopy(v) for k, v in p.items() if k not in {'source_snapshots', 'document_reviews'}} for p in predictions]


def _validate_proposal(proposal, gold, prior):
    if not isinstance(proposal, dict) or set(proposal) != {'addition', 'rationale'}:
        raise ValueError('Optimizer may return only addition and rationale; no paths, edits or tools.')
    if any(not isinstance(proposal[k], str) or not proposal[k].strip() for k in proposal):
        raise ValueError('Empty optimizer addition/rationale.')
    addition = proposal['addition'].strip()
    if len(addition) > 4000 or sum(map(len, prior)) + len(addition) > 12000 or '```' in addition:
        raise ValueError('Only bounded prose additions are allowed, without executable blocks.')
    text = title_key(addition)
    for case in gold['cases']:
        for target in case['targets']:
            identities = [target['title'], *target['title_aliases'], *target['publication_ids']]
            if any(len(title_key(s)) >= 4 and title_key(s) in text for s in identities):
                raise ValueError('An addition hardcodes a private reference identity.')
    return addition


def run_experiment(inputs, gold, output, predictor, proposer, *, formal_root, rounds=5,
                   budget=None, weights=(0.5, 0.5), mode='live', anchor=None, runtime=None):
    """P: prose proposal; F: fresh paired trials; R: strict Pareto improvement.

    Callbacks receive data/text only. The controller never exposes a filesystem edit tool.
    """
    inputs = validate_inputs(inputs)
    gold = validate_gold(inputs, gold)
    if type(rounds) is not int or not 1 <= rounds <= 5:
        raise ValueError('This small experiment permits one to five improvement rounds.')
    if len(weights) != 2 or any(type(w) not in (int, float) or not 0 <= w <= 1 for w in weights) or abs(sum(weights)-1) > 1e-9:
        raise ValueError('Fix two nonnegative weights summing to one before running.')
    budget = deepcopy(budget or {'max_queries': 4, 'max_documents': 20})
    weights = tuple(weights)
    if set(budget) != {'max_queries', 'max_documents'} or any(type(v) is not int or not 1 <= v <= 20 for v in budget.values()):
        raise ValueError('Fixed query/document budgets must be between one and twenty.')
    formal_root, output = Path(formal_root).resolve(), Path(output).resolve()
    if output.is_relative_to(formal_root) or formal_root.is_relative_to(output):
        raise ValueError('Experiment output cannot overlap the production Skill directory.')
    if mode not in {'live', 'teaching_demo'} or (mode == 'live' and inputs['provenance'] != 'provided_dataset'):
        raise ValueError('Teaching data cannot be presented as live benchmark evidence.')
    base_path = formal_root/'patent-precheck/SKILL.md'
    text = base_path.read_text(encoding='utf-8-sig')
    base = text.split('---', 2)[2].strip()
    protected = _formal_fingerprints(formal_root)
    guard = lambda: _guard(formal_root, protected)
    output.mkdir(parents=True, exist_ok=False)
    configuration = {'schema': SCHEMA, 'rounds': rounds, 'budget': budget, 'weights': list(weights),
                     'selection': 'strict_total_gain_and_no_component_regression', 'mode': mode,
                     'input_hash': digest(inputs), 'gold_hash': digest(gold), 'formal_skills': protected,
                     'reference': 'https://share.onorca.dev/a/akZtQQ86ZK-C'}
    configuration['scorer_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    runtime = runtime or {}
    if set(runtime)-{'adapter', 'model', 'provider_host', 'temperature'}:
        raise ValueError('Runtime metadata must not contain credentials or arbitrary settings.')
    configuration['runtime'] = deepcopy(runtime)
    save(output/'config.json', configuration)
    manifest_hash = hashlib.sha256((output/'config.json').read_bytes()).hexdigest()

    def trial(skill, cases, folder):
        guard()
        if hashlib.sha256((output/'config.json').read_bytes()).hexdigest() != manifest_hash:
            raise ValueError('Frozen scorer configuration changed.')
        before = hashlib.sha256(skill.read_bytes()).hexdigest()
        predictions = []
        for case in cases['cases']:
            prediction = predictor(skill, deepcopy(case), {'risk_levels': cases['risk_levels'],
                'abstain_label': cases['abstain_label'], 'budget': deepcopy(budget)})
            guard()
            predictions.append(prediction)
        if hashlib.sha256(skill.read_bytes()).hexdigest() != before:
            raise ValueError('The trial changed its frozen Skill document.')
        if hashlib.sha256((output/'config.json').read_bytes()).hexdigest() != manifest_hash:
            raise ValueError('Frozen scorer configuration changed during a trial.')
        folder.mkdir(parents=True, exist_ok=False)
        save(folder/'predictions.json', predictions)  # Freeze predictions before scoring.
        return predictions

    history, additions = [], []
    initial = output/'baseline/patent-rsi-trial/SKILL.md'
    initial_binding = _make_skill(initial, base, additions, {})
    try:
        predictions = trial(initial, inputs, output/'baseline/trial')
        initial_score = score_predictions(inputs, gold, predictions, weights)
        save(output/'baseline/score.json', initial_score)
        incumbent, last_feedback = initial, _feedback(initial_score)
        selected, previous_attempt = 0, None
        for number in range(1, rounds+1):
            folder = output/f'round-{number:02d}'
            proposal = proposer({'current_skill': incumbent.read_text(encoding='utf-8'),
                'feedback': deepcopy(last_feedback), 'public_cases': deepcopy(inputs['cases']),
                'previous_predictions': _optimizer_predictions(predictions), 'previous_attempt': deepcopy(previous_attempt),
                'budget': deepcopy(budget)})
            guard()
            addition = _validate_proposal(proposal, gold, additions)
            candidate = folder/'patent-rsi-trial/SKILL.md'
            binding = _make_skill(candidate, base, additions+[addition], last_feedback)
            save(folder/'proposal.json', proposal)
            control_predictions = trial(incumbent, inputs, folder/'control')
            control_score = score_predictions(inputs, gold, control_predictions, weights)
            candidate_predictions = trial(candidate, inputs, folder/'candidate')
            candidate_score = score_predictions(inputs, gold, candidate_predictions, weights)
            accepted = (candidate_score['total_score'] > control_score['total_score']
                        and candidate_score['retrieval_score'] >= control_score['retrieval_score']
                        and candidate_score['risk_score'] >= control_score['risk_score'])
            record = {'round': number, 'parent_skill': str(incumbent), 'candidate_skill': binding,
                'control_score': control_score, 'candidate_score': candidate_score, 'accepted': accepted,
                'reason': 'strict_gain_without_component_regression' if accepted else 'no_strict_gain_or_component_regression'}
            record['parent_sha256'] = hashlib.sha256(incumbent.read_bytes()).hexdigest()
            save(folder/'evaluation.json', record)
            history.append(record)
            if accepted:
                incumbent, selected = candidate, number
                additions.append(addition)
                predictions = candidate_predictions
                last_feedback = _feedback(candidate_score)
            else:
                predictions = control_predictions
                last_feedback = _feedback(control_score)
            save(folder/'feedback.json', {'candidate': _feedback(candidate_score), 'accepted': accepted,
                 'next_parent': str(incumbent), 'incumbent': last_feedback})
            previous_attempt = {'proposal': proposal, 'candidate_feedback': _feedback(candidate_score),
                                'accepted': accepted, 'predictions': _optimizer_predictions(candidate_predictions)}
        result = {'schema': SCHEMA, 'execution': 'completed', 'mode': mode, 'initial_skill': initial_binding,
            'initial_score': initial_score, 'rounds': history, 'selected_round': selected, 'best_skill': str(incumbent),
            'accepted_rounds': [r['round'] for r in history if r['accepted']], 'last_incumbent_score': last_feedback,
            'generalization': 'not_measured_no_anchor', 'anchor': None}
        result.update(score_weights=list(weights), trial_budget=deepcopy(budget))
        result['formal_skills_unchanged'] = _formal_fingerprints(formal_root) == protected
        result['best_skill_sha256'] = hashlib.sha256(incumbent.read_bytes()).hexdigest()
        # Anchor data is read only after selection, and never sent to the optimizer.
        if anchor is not None:
            anchor_inputs, anchor_gold = anchor()
            anchor_inputs = validate_inputs(anchor_inputs)
            anchor_gold = validate_gold(anchor_inputs, anchor_gold)
            if {c['case_id'] for c in anchor_inputs['cases']} & {c['case_id'] for c in inputs['cases']}:
                raise ValueError('Anchor cases overlap the optimization set.')
            if {digest((c['cutoff'], c['text'].strip())) for c in anchor_inputs['cases']} & {digest((c['cutoff'], c['text'].strip())) for c in inputs['cases']}:
                raise ValueError('Renaming a training case does not create an independent anchor.')
            before = score_predictions(anchor_inputs, anchor_gold, trial(initial, anchor_inputs, output/'anchor/baseline'), weights)
            after = score_predictions(anchor_inputs, anchor_gold, trial(incumbent, anchor_inputs, output/'anchor/selected'), weights)
            result.update(anchor={'baseline': before, 'selected': after}, generalization='separate_anchor_observation_not_statistical_proof')
        guard()
        save(output/'result.json', result)
        (output/'result.md').write_text(report_markdown(result), encoding='utf-8')
        return result
    except Exception as exc:
        save(output/'failure.json', {'execution': 'failed', 'completed_rounds': len(history), 'error_type': type(exc).__name__,
             'note': 'No complete RSI result; completed evidence retained. No production Skill was promoted.'})
        (output/'failure.md').write_text('# RSI 未完成\n\n有效循环完成数：'+str(len(history))+'。\n\n错误类型：'+type(exc).__name__+'。未生成完整提升结论，已完成记录保留，正式 Skill 未自动替换。\n', encoding='utf-8')
        raise


def _guard(root, expected):
    if _formal_fingerprints(root) != expected:
        raise ValueError('Production Skill files changed; stop the experiment without writing or restoring them.')


def report_markdown(result):
    label = '教学模拟：预设响应，不是模型提升结果。' if result['mode'] == 'teaching_demo' else '真实接口运行：分数仅针对当前标注集。'
    initial = result['initial_score']
    lines = ['# 专利临时 Skill RSI 结果', '', label, '',
        f"基线：检索 {initial['retrieval_score']:.2f} / 风险 {initial['risk_score']:.2f} / 总分 {initial['total_score']:.2f}", '',
        f"总分权重：检索 {result['score_weights'][0]:g} / 风险 {result['score_weights'][1]:g}；每次试跑预算：{result['trial_budget']['max_queries']} 条查询、{result['trial_budget']['max_documents']} 次正文获取尝试。", '',
        '|轮次|当前版总分（重测）|候选找回/应找回|A找回|候选检索分|候选风险分|候选总分|采纳|', '|---|---:|---|---|---:|---:|---:|---|']
    for row in result['rounds']:
        score = row['candidate_score']
        retrieved = '；'.join(f"{r['found']}/{r['expected']}" for r in score['cases'])
        source_a = '；'.join('是' if r['source_A_found'] else '否' for r in score['cases'])
        lines.append(f"|{row['round']}|{row['control_score']['total_score']:.2f}|{retrieved}|{source_a}|{score['retrieval_score']:.2f}|{score['risk_score']:.2f}|{score['total_score']:.2f}|{'是' if row['accepted'] else '否'}|")
    lines += ['', f"保留版本：第 {result['selected_round']} 轮（0 为基线）。", f"临时 Skill：{result['best_skill']}",
        '', '每轮均重新测试当前版与候选版；分数相同不采纳，任一分项退步不采纳。正式 Skill 未自动替换。',
        '', '全部列出的专利均为应找回目标；每篇去重计一次。风险分为与预置标签完全一致得 100，否则得 0。总分使用运行前冻结的权重。',
        '', '同一副本反复优化只反映该集合拟合；约二十篇参考文献不是二十个独立风险样本。']
    if result['anchor'] is None:
        lines.append('未提供独立锚集，不能据此声称对新案件普遍提高。')
    else:
        lines.append('独立锚集只在选定版本后对账，未用于提议或采纳；详见 result.json。')
    return '\n'.join(lines)+'\n'
