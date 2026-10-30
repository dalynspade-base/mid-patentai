"""Document loading and stale-context behavior, with no real model calls."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from modules import skill_library
from modules.harness import NoveltyHarness
from modules.patent_context import context_for
from modules.patent_tools import prepare_materials_with_model
from modules.novelty_models import Feature
from modules.semantic_eval import evaluation_packet, evaluate_skill


class DocumentSkillTests(unittest.TestCase):
    def test_model_receives_selected_document_and_explicit_material(self):
        raw='控制器仅在温度超过界限时启动泵。'
        client=Mock()
        client.complete.return_value=json.dumps({'features':[{'id':'F1','text':raw,'evidence':[
            {'source':'idea','location':'原描述','quote':raw}]}],'unknowns':[]})
        _, record=prepare_materials_with_model({'idea':raw},client,True)
        system, payload=client.complete.call_args.args
        self.assertIn(skill_library.load_skill('patent-material-preparation')['instructions'],system)
        self.assertEqual(json.loads(payload)['sources']['idea'],raw)
        self.assertEqual(record['inputs']['skill_document'],skill_library.skill_snapshot('patent-material-preparation'))

    def test_document_context_is_the_evaluation_context(self):
        state=NoveltyHarness().run(feature_text='控制器',cutoff='2024-01-01',documents=[])
        packet=evaluation_packet(state['skill_records'][0])
        self.assertEqual(packet['context_contract'],skill_library.context_contract('patent-material-preparation'))
        self.assertIn('instructions',packet['skill_document'])
        self.assertNotIn('score',packet)

    def test_changed_document_invalidates_context_and_frozen_run(self):
        state=NoveltyHarness().run(feature_text='控制器',cutoff='2024-01-01',documents=[])
        with tempfile.TemporaryDirectory() as directory:
            copied=Path(directory)/'skills'
            shutil.copytree(skill_library.SKILL_ROOT,copied)
            document=copied/'patent-material-preparation/SKILL.md'
            document.write_text(document.read_text(encoding='utf-8')+'\n新增本任务语境限定。\n',encoding='utf-8')
            with patch.object(skill_library,'SKILL_ROOT',copied):
                with self.assertRaises(ValueError):
                    context_for(state['features'],'2024-01-01',state['patent_context'])
                client=Mock()
                with self.assertRaises(ValueError):
                    evaluate_skill(state['skill_records'][0],client,True)
                client.complete.assert_not_called()

    def test_missing_skill_fails_before_any_tool_or_network_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(skill_library,'SKILL_ROOT',Path(directory)):
                search=Mock()
                with self.assertRaises(FileNotFoundError):
                    NoveltyHarness(searcher=search).run(feature_text='控制器',cutoff='2024-01-01')
                search.assert_not_called()

    def test_new_run_uses_current_document_contract_after_an_edit(self):
        from modules.patent_tools import prepare_materials
        with tempfile.TemporaryDirectory() as directory:
            copied=Path(directory)/'skills'
            shutil.copytree(skill_library.SKILL_ROOT,copied)
            document=copied/'patent-material-preparation/SKILL.md'
            text=document.read_text(encoding='utf-8').replace('## 核验\n','## 核验\n\n核验本轮新增的机制限定。\n')
            text=text.replace('required_dimensions: terms_objects,','required_dimensions: functions_effects,terms_objects,')
            document.write_text(text,encoding='utf-8')
            with patch.object(skill_library,'SKILL_ROOT',copied):
                _,record=prepare_materials('控制器',cutoff='2024-01-01')
                packet=evaluation_packet(record)
                self.assertIn('本轮新增的机制限定',packet['context_contract']['check'])
                self.assertIn('functions_effects',packet['required_dimensions'])

    def test_historical_record_remains_readable_without_fabricated_instruction_binding(self):
        from modules.semantic_eval_examples import teaching_records
        record=teaching_records()[0]
        self.assertNotIn('skill_document',record['inputs'])
        packet=evaluation_packet(record)
        self.assertNotIn('skill_document',packet['record']['inputs'])
        self.assertIn('skill_document',packet)

    def test_traversal_is_not_a_skill_name(self):
        with self.assertRaises(ValueError):
            skill_library.load_skill('../patent-material-preparation')

    def test_changed_eval_document_changes_the_evaluation_binding(self):
        from modules.semantic_eval_examples import teaching_records
        from modules.skill_contracts import digest
        record=teaching_records()[0]
        before=evaluation_packet(record)
        with tempfile.TemporaryDirectory() as directory:
            copied=Path(directory)/'skills'
            shutil.copytree(skill_library.SKILL_ROOT,copied)
            document=copied/'patent-semantic-eval/SKILL.md'
            document.write_text(document.read_text(encoding='utf-8')+'\n复核新增争议语境。\n',encoding='utf-8')
            with patch.object(skill_library,'SKILL_ROOT',copied):
                after=evaluation_packet(record)
                self.assertNotEqual(digest(before),digest(after))
                self.assertEqual(before['record'],after['record'])
