"""Exercise the embedded original parser without the PriceTracker checkout or npm."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from tests.test_baseline_determined import load_benchmark


class EmbeddedOriginalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = load_benchmark()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.settings = patch.dict(self.ns, OUTPUT_DIR=self.folder / 'results',
                                   ORIGINAL_RUNTIME_DIR=self.folder / 'runtime', RESUME_EXTRACTION=False)
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def manifest(self):
        html = '''<html><body>
        <div class="product-card"><a class="product-card__name" href="https://demo.test/product/1001">Дрель Alpha 500</a><span class="current-price">100 ₽</span></div>
        <div class="product-card"><a class="product-card__name" href="https://demo.test/product/1002">Молоток Beta 700</a><span class="current-price">200 ₽</span></div>
        </body></html>'''
        path = self.folder / 'page.html'
        path.write_text(html, encoding='utf-8')
        return pd.DataFrame([dict(dataset='demo', page_id='p1', html_file='html/page.html', html_key='page.html',
                                  html_path=str(path), source='demo.test', run_id='capture-1', skip_reason='',
                                  page_url='https://demo.test/search', source_url='https://demo.test', query='дрель',
                                  content_sha256=hashlib.sha256(path.read_bytes()).hexdigest())])

    def run_original(self, manifest):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.ns['run_baselines'](manifest, ('original_fast', 'original_fast_query'))

    def test_original_modes_and_resume_are_independent_of_external_checkout(self):
        manifest = self.manifest()
        result = self.run_original(manifest)
        all_rows = result[result.variant.eq('original_fast')]
        self.assertEqual(set(all_rows.pred_name), {'Дрель Alpha 500', 'Молоток Beta 700'})
        self.assertEqual(set(all_rows.pred_price), {100, 200})
        self.assertEqual(result[result.variant.eq('original_fast_query')].pred_name.tolist(), ['Дрель Alpha 500'])
        node, _, runtime, env = self.ns['prepare_original_runtime']()
        modules = json.loads(subprocess.check_output([node, '-e',
            'require(process.argv[1]); console.log(JSON.stringify(Object.keys(require.cache)))',
            str(runtime / 'original_fast.cjs')], cwd=runtime, env=env, text=True))
        self.assertEqual([Path(p).resolve() for p in modules], [(runtime / 'original_fast.cjs').resolve()])
        with patch.dict(self.ns, RESUME_EXTRACTION=True):
            resumed = self.run_original(manifest)
            self.assertTrue(all(r['replay_cached'] for r in resumed.attrs['extraction_log']))
            self.assertTrue(all(r['extract_ms'] is None for r in resumed.attrs['extraction_log']))
            pd.testing.assert_frame_equal(result, resumed)
            manifest.loc[0, 'query'] = 'молоток'
            changed = self.run_original(manifest)
            self.assertEqual(changed[changed.variant.eq('original_fast_query')].pred_name.tolist(), ['Молоток Beta 700'])
            self.assertFalse(any(r['replay_cached'] for r in changed.attrs['extraction_log']))

    def test_changed_html_fails_instead_of_scoring_empty_predictions(self):
        manifest = self.manifest()
        Path(manifest.html_path.iloc[0]).write_text('<html>changed</html>')
        with self.assertRaisesRegex(RuntimeError, 'HTML changed'):
            self.run_original(manifest)

    def test_tampered_embedded_source_is_rejected(self):
        sources = dict(self.ns['ORIGINAL_SOURCE_FILES'])
        sources['src/utils.ts'] += '\n// changed'
        with patch.dict(self.ns, ORIGINAL_SOURCE_FILES=sources):
            with self.assertRaisesRegex(ValueError, 'source failed SHA256'):
                self.ns['prepare_original_runtime']()


if __name__ == '__main__':
    unittest.main()
