import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts.repair_synthetic_dataset import recovery_plan, restore


class SyntheticRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        clean = '<html><head><link rel="canonical" href="https://synthetic-shop.local/search/syn-0001"></head><body><a href="https://synthetic-shop.local/product/tool-01">Tool</a></body></html>'
        mutation = clean.replace('</head>', '<meta name="synthetic-mutation" content="class_rename:1"></head>')
        self.clean = clean.encode()
        self.mutation = mutation.encode()
        (self.root / 'wrong-clean.html').write_bytes(self.clean)
        (self.root / 'config.json').write_bytes(self.mutation)
        (self.root / 'wrong-config.md').write_text(json.dumps(dict(n_clean_pages=1, entities_per_page=1,
            robustness_mutations_per_clean_page=1, robustness_axes={'class_rename': 1})))
        (self.root / 'wrong-summary.csv').write_text(json.dumps(dict(clean_entities=1, templates={'plain_cards': 1})))
        (self.root / 'wrong-readme.csv').write_text('# Synthetic E-commerce Benchmark\nFixture\n')
        self.gt = pd.DataFrame([dict(mutation_id='syn-0001__class_rename__01', base_page_id='syn-0001',
            entity_id='syn-0001__class_rename__01-e01', base_entity_id='syn-0001-e01',
            name_gt='Tool', price_gt=100, product_url_gt='https://synthetic-shop.local/product/tool-01',
            category='tools', subcategory='tools')])
        self.gt.to_csv(self.root / 'wrong-gt.csv', index=False)
        self.pages = pd.DataFrame([dict(mutation_id='syn-0001__class_rename__01', base_page_id='syn-0001',
            category='tools', base_template='plain_cards', mutation_axis='class_rename', variant_idx=1,
            sha256=hashlib.sha256(self.mutation).hexdigest(), n_entities=1)])
        self.pages.to_csv(self.root / 'wrong-pages.csv', index=False)

    def test_exact_payloads_and_original_gt_are_restored_without_extractor(self):
        plan, report, _ = recovery_plan(self.root)
        self.assertEqual(plan['html/syn-0001.html'], self.clean)
        self.assertEqual(plan['html/syn-0001__class_rename__01.html'], self.mutation)
        self.assertEqual(plan['robustness_ground_truth.csv'], (self.root / 'wrong-gt.csv').read_bytes())
        self.assertEqual(report['gt_urls_verified'], 2)

    def test_bad_hash_fails_before_writing_anything(self):
        self.pages['sha256'] = '0' * 64
        self.pages.to_csv(self.root / 'wrong-pages.csv', index=False)
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with self.assertRaisesRegex(ValueError, 'Missing exact HTML'):
            restore(self.root, write=True)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_inconsistent_labels_are_not_silently_rewritten(self):
        self.gt['product_url_gt'] = 'https://synthetic-shop.local/product/wrong'
        self.gt.to_csv(self.root / 'wrong-gt.csv', index=False)
        with self.assertRaisesRegex(ValueError, 'GT URL is absent'):
            recovery_plan(self.root)


if __name__ == '__main__':
    unittest.main()
