"""Guard against copying whole response ledgers per prediction group/column."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from tests.test_baseline_determined import load_benchmark


class NeverCopyLedger:
    def __deepcopy__(self, memo):
        raise AssertionError('Response ledger must not be deep-copied during deduplication')


class ReportingPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = load_benchmark()

    def test_duplicate_selection_and_order_match_frozen_implementation_without_copying_attrs(self):
        raw = pd.DataFrame([
            dict(variant='original_fast', html_key='a.html', product_url='https://www.example.test/p/',
                 pred_name='Tool', pred_price=None, extract_notes='first'),
            dict(variant='original_fast', html_key='a.html', product_url='https://example.test/p',
                 pred_name='Tool', pred_price=12, extract_notes='complete'),
            dict(variant='original_fast', html_key='a.html', product_url='https://example.test/p',
                 pred_name='Tool', pred_price=12, extract_notes='tied later'),
            dict(variant='original_fast', html_key='a.html', product_url=None,
                 pred_name='Missing URL', pred_price=1, extract_notes='invalid'),
        ])
        collapse = self.ns['collapse_prediction_keys']
        expected = collapse._reporting_original_function(raw)
        ledger = NeverCopyLedger()
        raw.attrs['llm_records'] = ledger
        actual = collapse(raw)
        pd.testing.assert_frame_equal(actual, expected)
        self.assertEqual(actual.extract_notes.tolist(), ['complete'])
        self.assertIs(raw.attrs['llm_records'], ledger)
        self.assertEqual(pd.DataFrame(raw, copy=True).product_url.iloc[0], 'https://www.example.test/p/')

    def test_reexecuting_adapter_does_not_stack_wrappers(self):
        collapse = self.ns['collapse_prediction_keys']
        again = self.ns['_reporting_collapse_adapter'](collapse)
        self.assertIs(again._reporting_original_function, collapse._reporting_original_function)

    def test_progress_records_stage_failure_without_swallowing_error(self):
        def fail():
            raise ValueError('fixture')
        with tempfile.TemporaryDirectory() as folder, patch.dict(self.ns, OUTPUT_DIR=Path(folder)):
            wrapper = self.ns['_reporting_stage_adapter'](fail, 'Test stage', lambda a, kw: 'test')
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'fixture'):
                wrapper()
            records = [json.loads(s) for s in (Path(folder) / 'reporting_progress.jsonl').read_text().splitlines()]
            self.assertEqual([r['status'] for r in records], ['started', 'failed'])
            self.assertEqual(records[-1]['error_type'], 'ValueError')


if __name__ == '__main__':
    unittest.main()
