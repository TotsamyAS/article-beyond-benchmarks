"""Dataset selection must agree between the notebook and the CLI, without API calls."""
import unittest
from unittest.mock import patch

from scripts import run_llm_benchmark


class CLISelectionTests(unittest.TestCase):
    def invoke(self, arguments):
        observed = []
        ns = dict(RUN_DATASETS=('synthetic', 'robustness'), EXPERIMENT_ID='v2')
        ns['llm_main'] = lambda: observed.append((ns['RUN_DATASETS'], ns['RUN_MODE']))
        with patch.object(run_llm_benchmark, 'load_notebook', return_value=ns), \
             patch.object(run_llm_benchmark.os, 'chdir'), \
             patch('sys.argv', ['run_llm_benchmark.py', *arguments]):
            run_llm_benchmark.main()
        return observed[0]

    def test_default_cli_respects_notebook_split_selection(self):
        self.assertEqual(self.invoke(['--plan']), (('synthetic', 'robustness'), 'plan'))

    def test_explicit_datasets_override_notebook_selection(self):
        self.assertEqual(self.invoke(['--run', '--datasets', 'industrial']), (('industrial',), 'run'))
