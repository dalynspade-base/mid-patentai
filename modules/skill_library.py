"""Read standard SKILL.md instructions; project context headings are a local convention."""
from pathlib import Path
import hashlib
import re

import yaml

SKILL_ROOT = Path(__file__).resolve().parents[1] / '.agents/skills'
BUSINESS_SKILLS = (
    'patent-material-preparation', 'patent-synonym-expansion',
    'patent-candidate-search', 'patent-evidence-alignment',
    'patent-novelty-assessment', 'patent-report-delivery',
)


def load_skill(name):
    """Load only the selected document; never execute its optional scripts implicitly."""
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
        raise ValueError('Invalid skill name.')
    path = SKILL_ROOT / name / 'SKILL.md'
    raw = path.read_bytes()
    match = re.fullmatch(r'---\r?\n(.*?)\r?\n---\r?\n(.*)', raw.decode('utf-8-sig'), re.S)
    if not match:
        raise ValueError(f'{name}: missing YAML frontmatter.')
    metadata = yaml.safe_load(match[1])
    if (not isinstance(metadata, dict) or metadata.get('name') != name
            or not isinstance(metadata.get('description'), str) or not metadata['description'].strip()):
        raise ValueError(f'{name}: name and description are required.')
    return {'name': name, 'description': metadata['description'],
            'metadata': metadata.get('metadata', {}), 'instructions': match[2].replace('\r\n', '\n').strip(),
            'path': f'.agents/skills/{name}/SKILL.md', 'sha256': hashlib.sha256(raw).hexdigest()}


def skill_snapshot(name, *, include_instructions=False):
    document = load_skill(name)
    keys = ('name', 'path', 'sha256', 'instructions') if include_instructions else ('name', 'path', 'sha256')
    return {key: document[key] for key in keys}


def skill_prompt(name, output_protocol):
    return load_skill(name)['instructions'] + '\n\n## 工具返回协议\n\n' + output_protocol


def business_skills():
    return BUSINESS_SKILLS


def _sections(name):
    text = load_skill(name)['instructions']
    return {m[0]: m[1].strip() for m in re.findall(r'^## ([^\n]+)\n([\s\S]*?)(?=^## |\Z)', text, re.M)}


def context_contract(name):
    sections = _sections(name)
    return {'needs': re.findall(r'^- (.+)$', sections['上下文'], re.M),
            'check': sections['核验'], 'boundary': sections['边界']}


def business_specs():
    specs = {}
    for name in BUSINESS_SKILLS:
        metadata = load_skill(name)['metadata']
        context = context_contract(name)
        specs[name] = {'name': metadata['display_name'],
                       'required': tuple(metadata['required_dimensions'].split(',')),
                       'obligations': context['check'] + context['boundary']}
    return specs


def library_hash():
    hashes = ''.join(skill_snapshot(name)['sha256'] for name in BUSINESS_SKILLS)
    return hashlib.sha256(hashes.encode('ascii')).hexdigest()
