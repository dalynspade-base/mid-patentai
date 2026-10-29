"""Delivery binds the completed report after adding actual material context."""
from copy import deepcopy
import unittest

from modules.novelty_report import to_markdown
from modules.skill_contracts import validate_record
from skills.precheck_skill import NoveltyHarness
from skills.report_delivery_skill import run


class ReportDeliveryTests(unittest.TestCase):
    def test_material_context_is_bound_once_without_mutating_execution(self):
        state = NoveltyHarness().run(feature_text='控制器连接冷却泵', cutoff='2024-01-01', documents=[])
        before = deepcopy(state)
        material = {'case_id': 'constructed-case', 'case_sha256': 'test-hash', 'attachments': []}
        payload, markdown = run(state, '构造材料', '2024-01-01', [], material_case=material)
        self.assertEqual(state, before)
        deliveries = [r for r in payload['skill_records'] if r['skill_id'] == 'patent-report-delivery']
        self.assertEqual(len(deliveries), 1)
        record = validate_record(deliveries[0])
        self.assertEqual(record['inputs']['material_case'], material)
        self.assertEqual(record['output']['markdown'], to_markdown(payload, include_skill_audit=False))
        self.assertIn('constructed-case', record['output']['markdown'])
        self.assertNotIn('skill_records', record['inputs'])
        self.assertIn('六个业务技能执行记录', markdown)
