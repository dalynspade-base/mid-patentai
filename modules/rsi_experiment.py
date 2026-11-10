"""Bounded text-only RSI experiment; production semantic eval is unchanged."""
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from modules.rsi_limits import MAX_INPUT_CHARACTERS

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
        if (set(case) - {'case_id', 'cutoff', 'text', 'assessment_targets', 'scoring_profile'}
                or not all(isinstance(case.get(k), str) and case[k].strip() for k in ('case_id', 'cutoff', 'text'))):
            raise ValueError('Public cases contain only opaque case_id, cutoff and modified text.')
        date.fromisoformat(case['cutoff'])
        if case['case_id'] in ids:
            raise ValueError('Duplicate case id.')
        if case.get('scoring_profile') not in {None, 'manual-v1.2', 'evidence-v2'}:
            raise ValueError('Unknown scoring profile.')
        ids.add(case['case_id'])
    return deepcopy(inputs)


def validate_gold(inputs, gold):
    if set(gold) != {'schema', 'dataset_id', 'cases'} or gold['schema'] != SCHEMA or gold['dataset_id'] != inputs['dataset_id']:
        raise ValueError('Private gold dataset does not match inputs.')
    public = {c['case_id']: c for c in inputs['cases']}
    if not isinstance(gold['cases'], list) or len(gold['cases']) != len(public) or {c['case_id'] for c in gold['cases']} != set(public):
        raise ValueError('Every case needs exactly one private reference.')
    for case in gold['cases']:
        if (set(case) - {'case_id', 'risk_label', 'source_target_id', 'targets', 'assessment'}
                or not {'case_id', 'risk_label', 'source_target_id', 'targets'} <= set(case)
                or case['risk_label'] not in inputs['risk_levels']):
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
        if public[case['case_id']].get('assessment_targets') or 'assessment' in case:
            if case.get('assessment', {}).get('profile') == 'manual-v1.2':
                from modules.rsi_manual_scoring import validate_manual
                validate_manual(public[case['case_id']], case)
            else:
                from modules.rsi_scoring import validate_assessment
                validate_assessment(public[case['case_id']], case)
            if any(t['publication_date'] and t['publication_date'] >= public[case['case_id']]['cutoff'] for t in targets):
                raise ValueError('Evidence-v2 retrieval targets must be published strictly before the cutoff.')
    profiles = {c.get('assessment', {}).get('profile', 'legacy-label-v1') for c in gold['cases']}
    if len(profiles) != 1:
        raise ValueError('Keep different material scoring profiles in separate experiment runs.')
    return deepcopy(gold)


def match_target(document, target):
    if target['publication_ids']:
        return publication_key(document['publication_id']) in {publication_key(s) for s in target['publication_ids']}
    return title_key(document['title']) in {title_key(s) for s in [target['title'], *target['title_aliases']]}


def score_predictions(inputs, gold, predictions, weights, *, reviews=None):
    from modules.rsi_scoring import finite_weights, score_evidence
    finite_weights(weights)
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
        if case.get('assessment_targets'):
            if reference.get('assessment', {}).get('profile') == 'manual-v1.2':
                from modules.rsi_manual_scoring import score_manual
                rows.append(score_manual(case, reference, prediction, (reviews or {}).get(case['case_id'])))
            else:
                rows.append(score_evidence(case, reference, prediction, weights))
            continue
        found = [t['target_id'] for t in reference['targets'] if any(match_target(d, t) for d in documents)]
        count = len(reference['targets'])
        retrieval = 100 * len(found) / count
        risk = 100.0 if prediction['risk_label'] == reference['risk_label'] else 0.0
        rows.append({'case_id': case['case_id'], 'found': len(found), 'expected': count,
                     'matched_target_ids': found, 'retrieval_score': retrieval, 'risk_score': risk,
                     'source_A_found': reference['source_target_id'] in found})
    retrieval = sum(r['retrieval_score'] for r in rows) / len(rows)
    risk = None if any(r['risk_score'] is None for r in rows) else sum(r['risk_score'] for r in rows) / len(rows)
    profiles = {r.get('scoring_profile', 'legacy-label-v1') for r in rows}
    if len(profiles) != 1:
        raise ValueError('Different scoring profiles cannot be averaged into one adoption signal.')
    profile = profiles.pop()
    manual = profile == 'manual-v1.2'
    components = ({key: None if any(r['components'][key] is None for r in rows) else sum(r['components'][key] for r in rows)/len(rows)
                   for key in ('retrieval', 'document_evidence', 'claim_conclusion')} if manual else
                  {'retrieval': retrieval, 'risk': risk})
    return {'retrieval_score': retrieval, 'risk_score': risk, 'components': components,
            'adoption_eligible': all(r.get('adoption_eligible', True) for r in rows),
            'scoring_profile': profile,
            'total_score': (None if risk is None else sum(components.values())) if manual else
                (None if risk is None else weights[0] * retrieval + weights[1] * risk), 'cases': rows}


def _formal_fingerprints(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}


def _make_skill(path, base, additions):
    body = ('---\nname: patent-rsi-trial\ndescription: 在固定检索预算内生成专利检索术语，仅用于隔离实验。\n---\n\n'
            '# 临时 RSI Skill\n\n## 不变的基线指令\n\n'+base+'\n\n## 已保留的补充指令\n\n'
            +'\n\n'.join(additions or ['尚无补充。'])+'\n')
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(body, encoding='utf-8')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _feedback(score):
    # Aggregate feedback only: no target identities, patent A identity or expected risk labels.
    return {key: score[key] for key in ('retrieval_score', 'risk_score', 'total_score')} | {
        'retrieval_gap': any(r['found'] < r['expected'] for r in score['cases']),
        'risk_mismatch': any(r['risk_score'] is None or r['risk_score'] < 100 for r in score['cases']),
        'adoption_eligible': score.get('adoption_eligible', True),
        'unjudged_count': sum(r.get('unjudged', 0) for r in score['cases'])}


def _validate_proposal(proposal, gold, prior):
    if not isinstance(proposal, dict) or set(proposal) != {'addition', 'rationale'}:
        raise ValueError('Optimizer may return only addition and rationale; no paths, edits or tools.')
    if any(not isinstance(proposal[k], str) or not proposal[k].strip() for k in proposal):
        raise ValueError('Empty optimizer addition/rationale.')
    addition = proposal['addition'].strip()
    if len(addition) > 4000 or sum(map(len, prior)) + len(addition) > 12000 or '```' in addition:
        raise ValueError('Only bounded prose additions are allowed, without executable blocks.')
    if re.search(r'https?://|\b(?:CN|US|WO|EP|JP|KR)\s*[- ]?\d{5,}', addition + ' ' + proposal['rationale'], re.I):
        raise ValueError('Use a general term expansion rule without links or publication numbers.')
    text = title_key(addition + ' ' + proposal['rationale'])
    for case in gold['cases']:
        for target in case['targets']:
            identities = [target['title'], *target['title_aliases'], *target['publication_ids']]
            if any(len(title_key(s)) >= 4 and title_key(s) in text for s in identities):
                raise ValueError('An addition hardcodes a private reference identity.')
    return addition


def run_experiment(inputs, gold, output, predictor, proposer, *, formal_root, rounds=5,
                   budget=None, weights=(0.5, 0.5), mode='live', anchor=None, runtime=None, assessor=None):
    """P: prose proposal; F: fresh paired trials; R: strict Pareto improvement.

    Callbacks receive data/text only. The controller never exposes a filesystem edit tool.
    """
    inputs = validate_inputs(inputs)
    gold = validate_gold(inputs, gold)
    if type(rounds) is not int or not 1 <= rounds <= 5:
        raise ValueError('This small experiment permits one to five improvement rounds.')
    from modules.rsi_scoring import finite_weights
    finite_weights(weights)
    budget = deepcopy(budget or {'max_queries': 4, 'max_documents': 20})
    weights = tuple(weights)
    if (set(budget) != {'max_queries', 'max_documents'} or type(budget['max_queries']) is not int
            or not 1 <= budget['max_queries'] <= 20 or type(budget['max_documents']) is not int
            or not 1 <= budget['max_documents'] <= 100):
        raise ValueError('Query budget must be 1-20; document budget must be 1-100, fixed before running.')
    formal_root, output = Path(formal_root).resolve(), Path(output).resolve()
    if output.is_relative_to(formal_root) or formal_root.is_relative_to(output):
        raise ValueError('Experiment output cannot overlap the production Skill directory.')
    if mode not in {'live', 'teaching_demo'} or (mode == 'live' and inputs['provenance'] != 'provided_dataset'):
        raise ValueError('Teaching data cannot be presented as live benchmark evidence.')
    base_path = formal_root/'patent-synonym-expansion/SKILL.md'
    text = base_path.read_text(encoding='utf-8-sig')
    base = text.split('---', 2)[2].strip()
    protected = _formal_fingerprints(formal_root)
    guard = lambda: _guard(formal_root, protected)
    output.mkdir(parents=True, exist_ok=False)
    configuration = {'schema': SCHEMA, 'rounds': rounds, 'budget': budget, 'weights': list(weights),
                     'target_skill': 'patent-synonym-expansion', 'proposal_policy': 'seal_before_trials',
                     'max_input_characters': MAX_INPUT_CHARACTERS,
                     'selection': 'strict_total_gain_and_no_component_regression', 'mode': mode,
                     'input_hash': digest(inputs), 'gold_hash': digest(gold), 'formal_skills': protected,
                     'reference': 'https://share.onorca.dev/a/akZtQQ86ZK-C'}
    configuration['scorer_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    scorer_paths = [Path(__file__), Path(__file__).with_name('rsi_scoring.py'),
                    Path(__file__).with_name('rsi_limits.py'),
                    Path(__file__).with_name('rsi_manual_scoring.py'), Path(__file__).with_name('rsi_rubric_review.py'),
                    Path(__file__).resolve().parents[1]/'evals/testsets/rsi_scoring.py']
    configuration['scoring_profiles'] = sorted({c.get('assessment', {}).get('profile', 'legacy-label-v1') for c in gold['cases']})
    configuration['component_weights'] = {'retrieval': 35, 'document_evidence': 45, 'claim_conclusion': 20} if configuration['scoring_profiles'] == ['manual-v1.2'] else {'retrieval': weights[0], 'risk': weights[1]}
    scorer_hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in scorer_paths}
    configuration['scoring_implementation'] = {p.name+':'+str(i): scorer_hashes[str(p)] for i, p in enumerate(scorer_paths)}
    runtime = runtime or {}
    if set(runtime)-{'adapter', 'model', 'provider_host', 'temperature', 'search_provider', 'rubric_model'}:
        raise ValueError('Runtime metadata must not contain credentials or arbitrary settings.')
    if runtime.get('search_provider', 'patsnap') != 'patsnap':
        raise ValueError('RSI live search requires Patsnap.')
    configuration['runtime'] = deepcopy(runtime)
    save(output/'config.json', configuration)
    manifest_hash = hashlib.sha256((output/'config.json').read_bytes()).hexdigest()

    def trial(skill, cases, folder):
        guard()
        if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in scorer_hashes.items()):
            raise ValueError('Frozen scoring implementation changed.')
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
        if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in scorer_hashes.items()):
            raise ValueError('Frozen scoring implementation changed during a trial.')
        folder.mkdir(parents=True, exist_ok=False)
        save(folder/'predictions.json', predictions)  # Freeze predictions before scoring.
        return predictions

    def evaluate(cases, references, predictions, folder):
        frozen = digest(predictions)
        reviews = {}
        if assessor is not None:
            for case in cases['cases']:
                reference = next(r for r in references['cases'] if r['case_id'] == case['case_id'])
                prediction = next(p for p in predictions if p['case_id'] == case['case_id'])
                if reference.get('assessment', {}).get('profile') == 'manual-v1.2':
                    review_folder = folder/('review-'+str(len(reviews)+1))
                    review_folder.mkdir(exist_ok=False)
                    reviews[case['case_id']] = assessor(deepcopy(case), deepcopy(reference), deepcopy(prediction), review_folder)
            guard()
            if digest(predictions) != frozen:
                raise ValueError('The review modified frozen predictions.')
            if hashlib.sha256((output/'config.json').read_bytes()).hexdigest() != manifest_hash or any(
                    hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in scorer_hashes.items()):
                raise ValueError('The review changed frozen scoring configuration or code.')
            save(folder/'rubric_reviews.json', reviews)
        return score_predictions(cases, references, predictions, weights, reviews=reviews)

    history, additions = [], []
    initial = output/'baseline/patent-rsi-trial/SKILL.md'
    initial_binding = _make_skill(initial, base, additions)
    try:
        proposals, proposed_additions = [], []
        for number in range(1, rounds+1):
            proposal = proposer({
                'skill_id': 'patent-synonym-expansion', 'current_skill': base, 'round': number,
                'development_examples': [
                    '合成例：压力传感器的规范译名可供查询，但“测量装置”是上位概念。',
                    '合成例：扩展连接器术语时，保持原要素里的连接方向、否定和参数。'],
                'earlier_proposals': list(proposed_additions),
            })
            guard()
            proposals.append(deepcopy(proposal))
            proposed_additions.append(_validate_proposal(proposal, {'cases': []}, []))
        for proposal in proposals:
            _validate_proposal(proposal, gold, [])
        save(output/'sealed_proposals.json', proposals)
        predictions = trial(initial, inputs, output/'baseline/trial')
        initial_score = evaluate(inputs, gold, predictions, output/'baseline/trial')
        save(output/'baseline/score.json', initial_score)
        incumbent, last_feedback = initial, _feedback(initial_score)
        selected = 0
        for number, (proposal, addition) in enumerate(zip(proposals, proposed_additions), 1):
            folder = output/f'round-{number:02d}'
            _validate_proposal(proposal, gold, additions)
            candidate = folder/'patent-rsi-trial/SKILL.md'
            binding = _make_skill(candidate, base, additions+[addition])
            save(folder/'proposal.json', proposal)
            control_predictions = trial(incumbent, inputs, folder/'control')
            control_score = evaluate(inputs, gold, control_predictions, folder/'control')
            candidate_predictions = trial(candidate, inputs, folder/'candidate')
            candidate_score = evaluate(inputs, gold, candidate_predictions, folder/'candidate')
            accepted = (candidate_score.get('adoption_eligible', True) and control_score.get('adoption_eligible', True)
                        and candidate_score['total_score'] > control_score['total_score']
                        and candidate_score['retrieval_score'] >= control_score['retrieval_score']
                        and candidate_score['risk_score'] >= control_score['risk_score'])
            accepted = accepted and all(candidate_score['components'][key] is not None
                and candidate_score['components'][key] >= value for key, value in control_score['components'].items())
            query_changed = any(before['queries'] != after['queries']
                                for before, after in zip(control_predictions, candidate_predictions)) if runtime.get('search_provider') == 'patsnap' else None
            if query_changed is False:
                accepted = False
                reason = 'no_effective_query_change'
            else:
                reason = 'strict_gain_without_component_regression' if accepted else 'no_strict_gain_or_component_regression'
            record = {'round': number, 'parent_skill': str(incumbent), 'candidate_skill': binding,
                'control_score': control_score, 'candidate_score': candidate_score, 'accepted': accepted,
                'effective_query_changed': query_changed, 'reason': reason}
            record['parent_sha256'] = hashlib.sha256(incumbent.read_bytes()).hexdigest()
            save(folder/'evaluation.json', record)
            history.append(record)
            if accepted:
                incumbent, selected = candidate, number
                additions.append(addition)
                last_feedback = _feedback(candidate_score)
            else:
                last_feedback = _feedback(control_score)
            save(folder/'feedback.json', {'candidate': _feedback(candidate_score), 'accepted': accepted,
                 'next_parent': str(incumbent), 'incumbent': last_feedback})
        result = {'schema': SCHEMA, 'execution': 'completed', 'mode': mode, 'initial_skill': initial_binding,
            'initial_score': initial_score, 'rounds': history, 'selected_round': selected, 'best_skill': str(incumbent),
            'accepted_rounds': [r['round'] for r in history if r['accepted']], 'last_incumbent_score': last_feedback,
            'generalization': 'not_measured_no_anchor', 'anchor': None}
        result.update(score_weights=list(weights), trial_budget=deepcopy(budget))
        result['target_skill'] = configuration['target_skill']
        result.update(scoring_profile=initial_score['scoring_profile'], component_weights=configuration['component_weights'])
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
            if {c.get('assessment', {}).get('profile', 'legacy-label-v1') for c in anchor_gold['cases']} != set(configuration['scoring_profiles']):
                raise ValueError('Anchor must use the same frozen scoring profile.')
            before = evaluate(anchor_inputs, anchor_gold, trial(initial, anchor_inputs, output/'anchor/baseline'), output/'anchor/baseline')
            after = evaluate(anchor_inputs, anchor_gold, trial(incumbent, anchor_inputs, output/'anchor/selected'), output/'anchor/selected')
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
    def number(value):
        return '未判/不可计算' if value is None else f'{value:.2f}'
    label = '教学模拟：预设响应，不是模型提升结果。' if result['mode'] == 'teaching_demo' else '真实接口运行：分数仅针对当前标注集。'
    initial = result['initial_score']
    lines = ['# 专利临时 Skill RSI 结果', '', label, '',
        f"基线：检索 {number(initial['retrieval_score'])} / 风险 {number(initial['risk_score'])} / 总分 {number(initial['total_score'])}", '',
        f"评分协议：{result.get('scoring_profile', 'legacy-label-v1')}；分项权重：{json.dumps(result.get('component_weights', result['score_weights']),ensure_ascii=False)}。每次试跑预算：{result['trial_budget']['max_queries']} 条查询、{result['trial_budget']['max_documents']} 次正文获取尝试。", '',
        '|轮次|当前版总分（重测）|候选找回/应找回|A找回|候选检索分|候选风险分|候选总分|采纳|', '|---|---:|---|---|---:|---:|---:|---|']
    for row in result['rounds']:
        score = row['candidate_score']
        retrieved = '；'.join(f"{r['found']}/{r['expected']}" for r in score['cases'])
        source_a = '；'.join('是' if r['source_A_found'] else '否' for r in score['cases'])
        lines.append(f"|{row['round']}|{number(row['control_score']['total_score'])}|{retrieved}|{source_a}|{number(score['retrieval_score'])}|{number(score['risk_score'])}|{number(score['total_score'])}|{'是' if row['accepted'] else '否'}|")
    lines += ['', f"保留版本：第 {result['selected_round']} 轮（0 为基线）。", f"临时 Skill：{result['best_skill']}",
        '', '每轮均重新测试当前版与候选版；分数相同不采纳，任一分项退步不采纳。正式 Skill 未自动替换。',
        '', 'legacy-label-v1 使用整案等级一致分；evidence-v2 使用逐文献/逐权项的相关性 F1 与证据判断正确率；manual-v1.2 使用35/45/20原始手册和独立复核。各协议分开报告，不跨协议比较。逐项分数及错误见 score.json。',
        '', '同一副本反复优化只反映该集合拟合；约二十篇参考文献不是二十个独立风险样本。']
    if result['anchor'] is None:
        lines.append('未提供独立锚集，不能据此声称对新案件普遍提高。')
    else:
        lines.append('独立锚集只在选定版本后对账，未用于提议或采纳；详见 result.json。')
    return '\n'.join(lines)+'\n'
