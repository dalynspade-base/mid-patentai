"""Make separate public inputs/private references from either supplied material format."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from modules.rsi_manual_scoring import PROFILE, validate_standard


def _envelope(dataset_id, case, reference, provenance='provided_dataset'):
    from modules.rsi_experiment import SCHEMA, validate_inputs, validate_gold
    inputs = {'schema': SCHEMA, 'dataset_id': dataset_id, 'provenance': provenance,
              'risk_levels': ['high', 'partial', 'context', 'insufficient_evidence'],
              'abstain_label': 'insufficient_evidence', 'cases': [case]}
    gold = {'schema': SCHEMA, 'dataset_id': dataset_id, 'cases': [reference]}
    return validate_inputs(inputs), validate_gold(inputs, gold)


def handbook_dataset(standard, text, claim_texts):
    standard = validate_standard(standard)
    if not isinstance(text, str) or not text.strip() or set(claim_texts) != {'1', '2'}:
        raise ValueError('Supply the actual public copy and both complete target claim texts.')
    case = {'case_id': 'copy-A-01', 'cutoff': standard['cutoff'], 'text': text,
            'scoring_profile': PROFILE, 'assessment_targets': [
                {'id': cid, 'title': '目标权项'+cid, 'text': claim_texts[cid]} for cid in ('1', '2')]}
    source = standard['gold_claims']['1']['document']
    reference = {'case_id': case['case_id'], 'risk_label': 'insufficient_evidence',
        'source_target_id': next(d['id'] for d in standard['documents'] if d['publication_id'] == source),
        'targets': [{'target_id': d['id'], 'title': d['title'], 'publication_ids': [d['publication_id']],
                     'title_aliases': [], 'publication_date': d['publication_date']} for d in standard['documents']],
        'assessment': {'profile': PROFILE, 'standard': standard}}
    return _envelope(standard['dataset_id'], case, reference)


def extract_claim_texts(text):
    starts = list(re.finditer(r'(?m)^\s*([12])\s*[.．]\s*一种', text))
    if len(starts) != 2 or [m[1] for m in starts] != ['1', '2']:
        raise ValueError('Cannot unambiguously extract claims 1 and 2; supply verified public claim text.')
    end = re.search(r'(?m)^\s*说明书\s*$', text[starts[1].start():])
    end_pos = starts[1].start()+end.start() if end else len(text)
    return {'1': re.sub(r'权利要求书\s*\d+\s*$', '', text[starts[0].start():starts[1].start()].strip()).strip(),
            '2': text[starts[1].start():end_pos].strip()}


def archive_dataset(root, text=None):
    root = Path(root)
    standard = json.loads((root/'private/等级手册详细标准.json').read_text(encoding='utf-8-sig'))
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8-sig'))
    for f in manifest['files']:
        if hashlib.sha256((root/f['path']).read_bytes()).hexdigest() != f['sha256']:
            raise ValueError('Archived original changed: '+f['path'])
    pdf = root/'public/副本A.pdf'
    if text is None:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError('PDF extraction needs optional requirements-data.txt, or --input-text with extracted public text.') from exc
        text = '\n\n'.join(page.extract_text() or '' for page in PdfReader(pdf).pages)
    inputs, gold = handbook_dataset(standard, text, extract_claim_texts(text))
    provenance = {'source': 'user_archive_v1.2', 'input_pdf_sha256': hashlib.sha256(pdf.read_bytes()).hexdigest(),
                  'standard_sha256': hashlib.sha256((root/'private/等级手册详细标准.json').read_bytes()).hexdigest(),
                  'input_text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                  'target_md_hash_not_substituted': True, 'originals_modified': False,
                  'note': 'Derived from public PDF/text; original Markdown was not supplied. No private labels in public input.'}
    return inputs, gold, provenance


def pr8_dataset(root):
    root = Path(root)
    raw = json.loads((root/'rsi_case.json').read_text(encoding='utf-8-sig'))
    patents = json.loads((root/'patents.json').read_text(encoding='utf-8-sig'))['patents']
    public = raw['public_case']
    case = {'case_id': public['case_id'], 'cutoff': public['cutoff'], 'text': public['description'],
            'assessment_targets': deepcopy(public['targets'])}
    if len(case['assessment_targets']) != 1:
        raise ValueError('PR8 adapter expects the verified single-target dataset.')
    targets, refs, aliases = [], [], {}
    train = [c for c in raw['candidates'] if c['usage'] == 'train']
    for cand in train:
        pid = cand['id']; label = deepcopy(cand['label']); meta = patents[pid]
        # An explicit earlier A version must be retrieved itself; the late B is not credited.
        source_a = next((sid.split(':')[0] for ids in label.get('evidence_sections', {}).values()
                         for sid in ids if sid.split(':')[0] != pid), None)
        actual_id = source_a if label['prior_art_at_cutoff'] and source_a and meta.get('publication_date_A') else pid
        publication_date = meta.get('publication_date_A') if actual_id != pid else meta['publication_date']
        aliases[pid] = actual_id
        evidence = {}
        for fid, ids in label.get('evidence_sections', {}).items():
            mapped = []
            for sid in ids:
                kind = sid.split(':', 1)[-1]
                if re.fullmatch(r'claim\d+', kind): mapped.append('claim-'+kind[5:])
                elif kind == 'abstract': mapped.append('abstract-1')
            if not mapped:
                raise ValueError('Reference requires independently mapped source positions: '+pid+'/'+fid)
            evidence[fid] = sorted(set(mapped))
        refs.append({'document_id': actual_id, 'target_id': case['assessment_targets'][0]['id'], 'label': {
            k: label[k] for k in ('relevance', 'prior_art_at_cutoff', 'coverage', 'feature_status')} | {'evidence_sections': evidence}})
        if label['relevance'] == 'related' and label['prior_art_at_cutoff'] and publication_date < case['cutoff']:
            targets.append({'target_id': pid, 'title': meta['title'], 'publication_ids': [actual_id],
                            'title_aliases': [], 'publication_date': publication_date})
    source = next((c['id'] for c in train if c['label']['coverage'] == 'full_disclosure' and c['label']['prior_art_at_cutoff']), None)
    if source is None: raise ValueError('No independently labelled full-disclosure source in the training material.')
    reference = {'case_id': case['case_id'], 'risk_label': 'high', 'source_target_id': source,
                 'targets': targets, 'assessment': {'profile': 'evidence-v2', 'references': refs}}
    inputs, gold = _envelope('pr8-train-evidence-v2', case, reference)
    return inputs, gold, {'source': 'PR8', 'usage': 'train_only', 'train_documents': len(train),
                         'anchor_documents_archived_only': sum(c['usage'] == 'anchor' for c in raw['candidates']),
                         'explicit_version_mapping': aliases, 'original_materials_modified': False,
                         'note': 'Only numbered claims/abstract positions are mapped. No anchor feedback or fixed-pool retrieval is imported.'}
