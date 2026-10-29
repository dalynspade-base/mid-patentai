"""Observable isolation, no-overwrite and contract behavior for current tools."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.run_verification import group_tests
from tools.check_semantic_contract import main as check_contract
from tools.smoke_google import main as smoke_google


class VerificationToolTests(unittest.TestCase):
    def test_current_and_historical_suites_are_disjoint(self):
        class NamedCase(unittest.TestCase):
            def __init__(self, identity):
                super().__init__('runTest')
                self.identity = identity

            def id(self):
                return self.identity

            def runTest(self):
                pass

        cases = [NamedCase(name) for name in (
            'test_semantic_skills.Current.test_one',
            'legacy.test_synonym_evaluation.Historical.test_two',
            'tests.legacy.test_basic_contracts.Historical.test_three',
        )]
        grouped = group_tests(unittest.TestSuite([
            unittest.TestSuite(cases[:2]), unittest.TestSuite(cases[2:]),
        ]))
        current = {case.id() for case in grouped['current_engineering']}
        historical = {case.id() for case in grouped['legacy_engineering']}
        self.assertEqual(current, {cases[0].id()})
        self.assertEqual(historical, {case.id() for case in cases[1:]})
        self.assertFalse(current & historical)

    def test_contract_demo_never_overwrites_an_existing_record(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'contract.json'
            output.write_text('preserved', encoding='utf-8')
            with patch('sys.argv', ['check', '--output', str(output)]), \
                 patch('tools.check_semantic_contract.contract_demo') as demo, \
                 redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    check_contract()
                demo.assert_not_called()
            self.assertEqual(output.read_text(encoding='utf-8'), 'preserved')

    def test_contract_demo_is_explicitly_not_a_semantic_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'contract.json'
            with patch('sys.argv', ['check', '--output', str(output)]), redirect_stdout(StringIO()):
                self.assertEqual(check_contract(), 0)
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(report['execution'], 'not_executed')
            self.assertIsNone(report['verdict'])

    def test_smoke_refuses_existing_destination_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('sys.argv', ['smoke', '--output-dir', directory]), \
                 patch('tools.smoke_google.search_patents') as search:
                with self.assertRaises(FileExistsError):
                    smoke_google()
                search.assert_not_called()

    def test_smoke_saves_failure_to_new_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'new-run'
            with patch('sys.argv', ['smoke', '--output-dir', str(output)]), \
                 patch('tools.smoke_google.search_patents', side_effect=RuntimeError('test failure')), \
                 redirect_stdout(StringIO()):
                smoke_google()
            report = json.loads((output / 'live_google_smoke.json').read_text(encoding='utf-8'))
            self.assertFalse(report['search_ok'])
            self.assertEqual(report['error'], 'test failure')
