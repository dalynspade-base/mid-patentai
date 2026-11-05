"""v1.2 handbook scoring. Semantic credits require a separately bound review."""
from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json
import math

from modules.novelty_models import PatentDocument

PROFILE = 'manual-v1.2'
LABELS = {'high', 'partial', 'context', 'insufficient_evidence'}
CRITERIA = ('specific_overlap', 'verifiable_locator', 'material_gap')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_standard(standard):
    if (standard.get('version') != '1.2' or standard.get('assessment_only') is not True
            or standard.get('target', {}).get('claims') != [1, 2]):
        raise ValueError('Expected a v1.2 assessment standard for target claims 1 and 2.')
    docs = standard['documents']
    if not docs or len({d['publication_id'] for d in docs}) != len(docs) or len({d['id'] for d in docs}) != len(docs):
        raise ValueError('Empty/duplicate standard document identities.')
    cutoff = date.fromisoformat(standard['cutoff'])
    for d in docs:
        if (d['relevance'] not in {'R1', 'R2', 'R3'} or type(d['weight']) is not int
                or d['weight'] != int(d['relevance'][1:]) or d.get('in_recall_denominator') is not True
                or date.fromisoformat(d['publication_date']) >= cutoff
                or any(d.get('claim_'+str(c)) not in LABELS for c in (1, 2))):
            raise ValueError('Invalid standard weights, date or per-target labels.')
    counts = standard['counts']
    if (len(docs) != counts['public_relevant'] or sum(d['weight'] for d in docs) != counts['weight_sum']
            or dict(Counter(d['relevance'] for d in docs)) != counts['by_relevance']):
        raise ValueError('Standard counts do not match its document list.')
    scoring = standard['scoring']
    if scoring['weights'] != {'retrieval': 35, 'document_evidence': 45, 'claim_conclusion': 20}:
        raise ValueError('v1.2 weights must remain 35/45/20.')
    retrieval = scoring['retrieval']
    if retrieval != {'weighted_recall_at': 25, 'weighted_recall_fraction': 0.65, 'ndcg_at': 10,
                     'ndcg_fraction': 0.35, 'ndcg_gain': '2^relevance_grade-1'}:
        raise ValueError('Unsupported v1.2 retrieval formula; do not silently substitute weights.')
    evidence = scoring['document_evidence']
    if (evidence['max_per_document'] != 4 or evidence['criteria'] != [*CRITERIA, 'correct_claim_labels']
            or evidence['risk_labels']['points_per_claim'] != 0.5
            or evidence['risk_labels']['require_matching_evidence'] is not True):
        raise ValueError('Unsupported v1.2 document evidence formula.')
    return deepcopy(standard)


def validate_manual(case, reference):
    assessment = reference.get('assessment', {})
    if set(assessment) != {'profile', 'standard'} or assessment.get('profile') != PROFILE:
        raise ValueError('Manual assessment needs profile and original standard.')
    standard = validate_standard(assessment['standard'])
    targets = case.get('assessment_targets', [])
    if (case.get('scoring_profile') != PROFILE or case['cutoff'] != standard['cutoff']
            or {t.get('id') for t in targets} != {'1', '2'} or len(targets) != 2):
        raise ValueError('Manual public input must contain both target claims and the standard cutoff.')
    for target in targets:
        if set(target) != {'id', 'title', 'text'} or any(not isinstance(v, str) or not v.strip() for v in target.values()):
            raise ValueError('Each public claim needs id/title and its complete text, without answers.')
    public_ids = {pid for t in reference['targets'] for pid in t['publication_ids']}
    if public_ids != {d['publication_id'] for d in standard['documents']}:
        raise ValueError('Retrieval targets differ from the sealed handbook reference list.')
    return standard


def _flag(value):
    if (not isinstance(value, dict) or set(value) != {'passed', 'basis'}
            or type(value['passed']) is not bool or not isinstance(value['basis'], str) or not value['basis'].strip()):
        raise ValueError('Review decisions require a boolean and a written basis, never self-reported points.')
    return value['passed']


def _citations_valid(citations, snapshots, allowed=None):
    if not isinstance(citations, list) or not citations:
        return False
    for c in citations:
        if not isinstance(c, dict) or set(c) != {'publication_id', 'section_id', 'quote'}:
            return False
        if allowed is not None and c['publication_id'] not in allowed:
            return False
        doc = snapshots.get(c['publication_id'])
        sections = {s.id: s.text for s in doc.sections} if doc else {}
        quote = c.get('quote')
        if (c.get('section_id') not in sections or not isinstance(quote, str) or not quote.strip()
                or ''.join(quote.split()) not in ''.join(sections[c['section_id']].split())):
            return False
    return True


def score_manual(case, reference, prediction, review=None):
    standard = validate_manual(case, reference)
    gold = {d['publication_id']: d for d in standard['documents']}
    # Preserve the actual first-seen search order. Duplicates never fill extra ranking slots.
    ordered = list(dict.fromkeys(d['publication_id'] for d in prediction['retrieved']))
    actual = {d['publication_id']: d for d in prediction['retrieved']}
    if any(d['publication_date'] >= case['cutoff'] for d in actual.values()):
        raise ValueError('Same-day/future documents do not qualify for this standard.')
    matched = set(ordered) & set(gold)
    weighted_recall = sum(gold[pid]['weight'] for pid in ordered[:25] if pid in gold) / standard['counts']['weight_sum']
    gains = {pid: 2**int(d['relevance'][1:])-1 for pid, d in gold.items()}
    dcg = sum(gains.get(pid, 0)/math.log2(rank+2) for rank, pid in enumerate(ordered[:10]))
    idcg = sum(gain/math.log2(rank+2) for rank, gain in enumerate(sorted(gains.values(), reverse=True)[:10]))
    ndcg = dcg/idcg if idcg else 0.0
    retrieval_points = 35 * (0.65 * weighted_recall + 0.35 * ndcg)
    result = {'case_id': case['case_id'], 'scoring_profile': PROFILE, 'found': len(matched), 'expected': len(gold),
        'matched_target_ids': [d['id'] for d in standard['documents'] if d['publication_id'] in matched],
        'source_A_found': standard['gold_claims']['1']['document'] in matched,
        'retrieval_score': retrieval_points/35*100, 'risk_score': None, 'total_score': None,
        'components': {'retrieval': retrieval_points, 'document_evidence': None, 'claim_conclusion': None},
        'component_maxima': {'retrieval': 35, 'document_evidence': 45, 'claim_conclusion': 20},
        'retrieval_metrics': {'weighted_recall_at_25': weighted_recall, 'ndcg_at_10': ndcg,
                              'returned_unique': len(ordered), 'reference_count': len(gold)},
        'pending_reference_documents': sorted(set(ordered)-set(gold)), 'unjudged': 0,
        'adoption_eligible': False, 'review_status': 'pending_independent_review',
        'standard_hash': digest(standard)}
    # The executor cannot grade itself by adding a review field to its prediction.
    if review is None:
        return result
    if (review.get('method') not in {'model_rubric_review', 'human_rubric_review'}
            or not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip()
            or review.get('prediction_hash') != digest(prediction) or review.get('standard_hash') != digest(standard)
            or review.get('case_hash') != digest(case)):
        raise ValueError('Independent review identity/input binding is missing or stale.')
    snapshots = {}
    for raw in prediction.get('source_snapshots', []):
        doc = PatentDocument.from_dict(raw)
        if doc.id in snapshots:
            raise ValueError('Duplicate source snapshot.')
        snapshots[doc.id] = doc
    reviews = prediction.get('handbook_documents', [])
    by_document = {}
    duplicates = set()
    for doc in reviews:
        if doc['publication_id'] in by_document: duplicates.add(doc['publication_id'])
        by_document[doc['publication_id']] = doc
    decisions = review.get('documents', [])
    if (not isinstance(decisions, list) or len({d.get('publication_id') for d in decisions}) != len(decisions)
            or {d.get('publication_id') for d in decisions} != matched):
        raise ValueError('Independent review must cover every retrieved reference document exactly once.')
    decisions = {d['publication_id']: d for d in decisions}
    conclusion_reviews = review.get('claims', [])
    if len(conclusion_reviews) != 2 or {c.get('target_id') for c in conclusion_reviews} != {'1', '2'}:
        raise ValueError('Independent review must cover both target conclusions exactly once.')
    conclusion_reviews = {c['target_id']: c for c in conclusion_reviews}
    points, rows = 0.0, []
    false_high = missed_high = confusion = unjudged = assessed_non_high = 0
    for pid, expected in gold.items():
        if pid not in matched:
            rows.append({'publication_id': pid, 'retrieved': False, 'points': 0, 'status': 'retrieval_miss'})
            continue
        item = by_document.get(pid, {})
        doc = snapshots.get(pid)
        bound = (pid not in duplicates and doc is not None and actual[pid].get('snapshot_hash') == doc.snapshot_hash
                 and all(actual[pid][k] == getattr(doc, k if k != 'publication_id' else 'id') for k in ('title', 'url', 'publication_date')))
        decision = decisions[pid]
        if set(decision) != {'publication_id', 'criteria', 'claim_evidence'} or set(decision['criteria']) != set(CRITERIA) or set(decision['claim_evidence']) != {'1', '2'}:
            raise ValueError('Incomplete independent document rubric.')
        semantic = {name: _flag(decision['criteria'][name]) for name in CRITERIA}
        evidence_flags = {c: _flag(decision['claim_evidence'][c]) for c in ('1', '2')}
        citation_ok = bound and _citations_valid(item.get('citations'), snapshots, {pid})
        overlap = item.get('overlap'); gap = item.get('gap')
        earned = (int(bound and isinstance(overlap, str) and bool(overlap.strip()) and semantic['specific_overlap'])
                  + int(citation_ok and semantic['verifiable_locator'])
                  + int(bound and isinstance(gap, str) and bool(gap.strip()) and semantic['material_gap']))
        for claim in ('1', '2'):
            label = item.get('claim_labels', {}).get(claim)
            evidence_ok = bound and _citations_valid(item.get('claim_citations', {}).get(claim), snapshots, {pid})
            expected_label = expected['claim_'+claim]
            assessed = isinstance(label, str) and label in LABELS - {'insufficient_evidence'} and evidence_ok
            if not assessed: unjudged += 1
            if assessed and expected_label != 'high': assessed_non_high += 1
            if assessed and label == 'high' and expected_label in {'partial', 'context'}: false_high += 1
            if assessed and expected_label == 'high' and label in {'partial', 'context'}: missed_high += 1
            if assessed and {label, expected_label} == {'partial', 'context'}: confusion += 1
            if label == expected_label and evidence_ok and evidence_flags[claim]: earned += 0.5
        points += earned * expected['weight']
        rows.append({'publication_id': pid, 'retrieved': True, 'points': earned, 'max_points': 4,
                     'snapshot_bound': bound, 'citation_valid': citation_ok})
    evidence_points = 11.25 * points / standard['counts']['weight_sum']
    conclusions = prediction.get('claim_conclusions', [])
    if not isinstance(conclusions, list) or len(conclusions) != 2 or {c.get('target_id') for c in conclusions} != {'1', '2'}:
        conclusions = []
    conclusion_points = 0
    conclusion_rows = []
    for claim in ('1', '2'):
        accepted = _flag({k: conclusion_reviews[claim][k] for k in ('passed', 'basis')})
        conclusion = next((c for c in conclusions if c['target_id'] == claim), {})
        expected = standard['gold_claims'][claim]['result']
        supported = (_citations_valid(conclusion.get('citations'), snapshots, matched)
                     and isinstance(conclusion.get('basis'), str) and bool(conclusion['basis'].strip())
                     and conclusion.get('result') == expected)
        if claim == '1':
            source = standard['gold_claims']['1']['document']
            supported = supported and source in matched and any(c['publication_id'] == source for c in conclusion.get('citations', []))
        else:
            supported = supported and bool(conclusion.get('key_gaps')) and not any(
                d.get('claim_labels', {}).get(claim) == 'high' for d in by_document.values())
        value = 10 if accepted and supported else 0
        conclusion_points += value
        conclusion_rows.append({'target_id': claim, 'points': value, 'review_passed': accepted, 'supported': bool(supported)})
    non_high = sum(d['claim_'+c] != 'high' for d in gold.values() for c in ('1', '2'))
    result.update(risk_score=(evidence_points+conclusion_points)/65*100,
        total_score=retrieval_points+evidence_points+conclusion_points,
        components={'retrieval': retrieval_points, 'document_evidence': evidence_points, 'claim_conclusion': conclusion_points},
        document_scores=rows, conclusion_scores=conclusion_rows,
        errors={'false_high': false_high, 'missed_high': missed_high, 'partial_context_confusion': confusion,
                'unjudged': unjudged, 'retrieval_misses': len(gold)-len(matched)},
        judgment_coverage=(2*len(matched)-unjudged)/(2*len(gold)),
        false_high_rate={'numerator': false_high, 'denominator': assessed_non_high,
                         'value': false_high/assessed_non_high if assessed_non_high else None,
                         'full_reference_denominator': non_high,
                         'non_high_assessment_coverage': assessed_non_high/non_high if non_high else None},
        unjudged=unjudged, review_status='completed',
        adoption_eligible=not unjudged and not duplicates and not result['pending_reference_documents'] and len(conclusions) == 2)
    return result
