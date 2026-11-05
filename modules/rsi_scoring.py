"""Actual-search scoring with PR #8's per-target evidence validation.

The private reference is read only here. Fixed-pool F1 is never called search recall.
"""
from copy import deepcopy
from datetime import date
import math

from evals.testsets.rsi_scoring import (COVERAGE, FEATURE_STATUS, private_key_paths,
                                       score_pool, validate_judgment)
from modules.novelty_models import PatentDocument

PROFILE = 'evidence-v2'


def validate_assessment(case, reference):
    targets = case.get('assessment_targets', [])
    assessment = reference.get('assessment')
    if not targets or not isinstance(assessment, dict) or set(assessment) != {'profile', 'references'}:
        raise ValueError('Evidence scoring requires public targets and a private assessment reference.')
    if assessment['profile'] != PROFILE or private_key_paths(targets):
        raise ValueError('Unknown assessment profile or private answer fields in public targets.')
    by_target = {}
    for target in targets:
        if set(target) != {'id', 'title', 'features'} or not all(isinstance(target[k], str) and target[k].strip() for k in ('id', 'title')):
            raise ValueError('Public targets need id, title and features.')
        features = target['features']
        if (not isinstance(features, list) or not features or target['id'] in by_target
                or any(set(f) != {'id', 'text'} or any(not isinstance(v, str) or not v.strip() for v in f.values()) for f in features)
                or len({f['id'] for f in features}) != len(features)):
            raise ValueError('Empty/duplicate target or feature identifiers.')
        by_target[target['id']] = target
    refs = assessment['references']
    if not isinstance(refs, list) or not refs:
        raise ValueError('Supply private per-document, per-target labels.')
    identities = set()
    for ref in refs:
        if set(ref) != {'document_id', 'target_id', 'label'}:
            raise ValueError('Assessment references contain document_id, target_id and label only.')
        key = (ref['document_id'], ref['target_id'])
        if not all(isinstance(v, str) and v.strip() for v in key) or key in identities or key[1] not in by_target:
            raise ValueError('Duplicate or unknown assessment reference identity.')
        identities.add(key)
        label = ref['label']
        if set(label) != {'relevance', 'prior_art_at_cutoff', 'coverage', 'feature_status', 'evidence_sections'}:
            raise ValueError('Supply relevance, date, coverage, feature statuses and evidence locations.')
        fids = {f['id'] for f in by_target[key[1]]['features']}
        statuses = label['feature_status']
        if (label['relevance'] not in {'related', 'unrelated'} or type(label['prior_art_at_cutoff']) is not bool
                or label['coverage'] not in COVERAGE or not isinstance(statuses, dict) or set(statuses) != fids
                or any(s not in FEATURE_STATUS for s in statuses.values())):
            raise ValueError('Invalid or incomplete per-target labels.')
        scope = label['evidence_sections']
        if not isinstance(scope, dict) or set(scope) != {fid for fid, s in statuses.items() if s == 'disclosed'}:
            raise ValueError('Every disclosed feature needs independent reference evidence locations.')
        if any(not isinstance(s, list) or not s or any(not isinstance(v, str) or not v for v in s) for s in scope.values()):
            raise ValueError('Reference evidence positions must be nonempty lists.')
        values = list(statuses.values())
        derived = ('not_applicable' if label['relevance'] == 'unrelated' else
                   'insufficient_evidence' if 'unknown' in values else
                   'full_disclosure' if all(s == 'disclosed' for s in values) else
                   'partial_coverage' if 'disclosed' in values else 'local_association')
        if label['coverage'] != derived:
            raise ValueError('Reference coverage contradicts its feature statuses.')
    expected_docs = {r['document_id'] for r in refs}
    if identities != {(pid, tid) for pid in expected_docs for tid in by_target}:
        raise ValueError('Reference must cover every target for every labelled document.')
    for target in reference['targets']:
        aliases = set(target['publication_ids'])
        if not aliases & expected_docs:
            raise ValueError('Every retrieval target needs private evidence labels.')
    return deepcopy(assessment)


def finite_weights(weights):
    if (len(weights) != 2 or any(type(w) not in (int, float) or not math.isfinite(w) or not 0 <= w <= 1 for w in weights)
            or abs(sum(weights) - 1) > 1e-9):
        raise ValueError('Fix two finite nonnegative weights summing to one.')


def score_evidence(case, reference, prediction, weights):
    """Score reviews of actually retrieved documents; retrieval misses reduce recall.

    Missing/invalid returned judgments are unjudged and block adoption. Unretrieved
    reference documents are retrieval misses, not invented model responses.
    """
    from modules.rsi_experiment import match_target
    validate_assessment(case, reference)
    refs = reference['assessment']['references']
    documents = prediction['retrieved']
    if any(d['publication_date'] >= case['cutoff'] for d in documents):
        raise ValueError('Evidence-v2 does not credit same-day or later publications.')
    unique = {d['publication_id']: d for d in documents}
    expected = reference['targets']
    matched = [t['target_id'] for t in expected if any(match_target(d, t) for d in unique.values())]
    # Family aliases count once; extra unlabelled papers remain pending, never false positives by default.
    tp = len(matched)
    labelled_ids = {r['document_id'] for r in refs}
    snapshots = {}
    for raw in prediction.get('source_snapshots', []):
        document = PatentDocument.from_dict(raw)
        if document.id in snapshots:
            raise ValueError('Duplicate frozen source snapshot.')
        snapshots[document.id] = document
    raw_judgments = prediction.get('evidence_judgments', [])
    if not isinstance(raw_judgments, list):
        raise ValueError('Evidence judgments must be a list.')
    returned = {}
    duplicate = set()
    for judgment in raw_judgments:
        if not isinstance(judgment, dict):
            raise ValueError('Invalid evidence judgment.')
        key = (judgment.get('document_id'), judgment.get('target_id'))
        if key in returned:
            duplicate.add(key)
        returned[key] = judgment
    expected_keys = {(pid, target['id']) for pid in unique for target in case['assessment_targets']}
    if set(returned) - expected_keys:
        raise ValueError('A judgment is not bound to an actually retrieved document/target.')
    pools, all_errors = [], []
    pending = sorted(set(unique) - labelled_ids)
    related = {r['document_id'] for r in refs if r['label']['relevance'] == 'related'}
    fp = len(set(unique) & (labelled_ids - related))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / len(expected)
    retrieval = {'precision': precision, 'recall': recall,
                 'f1': 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
                 'tp': tp, 'fp': fp, 'fn': len(expected) - tp, 'unlabelled': pending,
                 'scope': 'actual_search_against_frozen_reference_not_whole_patent_corpus'}
    for target in case['assessment_targets']:
        records = []
        labels = [r for r in refs if r['target_id'] == target['id']]
        label_case = {'candidates': [{'id': r['document_id'], 'label': r['label']} for r in labels],
                      'scoring': {'formula_version': 'actual-search-evidence-v2'}}
        ids = sorted(set(unique) & labelled_ids)
        for pid in ids:
            key = (pid, target['id'])
            errors = []
            normalized = {}
            doc = snapshots.get(pid)
            if not doc or doc.snapshot_hash != unique[pid].get('snapshot_hash'):
                errors.append({'code': 'snapshot_binding', 'message': 'Missing or mismatched source snapshot.'})
            elif (doc.publication_date != unique[pid]['publication_date'] or doc.url != unique[pid]['url']
                  or doc.title != unique[pid]['title']):
                errors.append({'code': 'snapshot_binding', 'message': 'Retrieved metadata differs from its snapshot.'})
            if key in duplicate:
                errors.append({'code': 'duplicate', 'message': 'Duplicate document/target judgment.'})
            if key not in returned:
                errors.append({'code': 'missing', 'message': 'Missing document/target judgment.'})
            if not errors:
                candidate = {'id': pid, 'sections': {s.id: {'text': s.text} for s in doc.sections},
                             'evidence_scope': {'complete_for_target': doc.complete or doc.parsed_complete}}
                normalized, errors = validate_judgment(returned[key], candidate, target)
                # Date eligibility comes from the actual snapshot, never a model assertion.
                if normalized.get('prior_art') != (date.fromisoformat(doc.publication_date) < date.fromisoformat(case['cutoff'])):
                    errors.append({'code': 'date_snapshot', 'message': 'Date assertion contradicts actual source.'})
            records.append({'document_id': pid, 'valid': not errors, 'judgment': normalized, 'errors': errors})
        pool = score_pool(records, ids, label_case)
        pool['target_id'] = target['id']
        pools.append(pool)
        all_errors += [{'target_id': target['id'], 'document_id': r['document_id'], 'errors': r['errors']}
                       for r in records if not r['valid']]
    valid = bool(unique) and not pending and all(p['judgment_coverage'] == 1.0 for p in pools)
    risk = 100 * sum(p['f_signal'] or 0 for p in pools) / len(pools)
    # No retrieved evidence is a valid search miss with zero judgment credit, not a fabricated judgment.
    if not unique:
        valid, risk = True, 0.0
    if not valid:
        risk = None
    return {'case_id': case['case_id'], 'found': tp, 'expected': len(expected),
            'matched_target_ids': matched, 'source_A_found': reference['source_target_id'] in matched,
            'retrieval_score': 100 * recall, 'risk_score': risk, 'retrieval_metrics': retrieval,
            'assessment_pools': pools, 'judgment_errors': all_errors, 'adoption_eligible': valid,
            'unjudged': sum(p['total_items'] - p['valid_items'] for p in pools),
            'pending_reference_documents': pending, 'scoring_profile': PROFILE}
