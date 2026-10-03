import csv
import io
import json
import unittest
from collections import Counter
from bs4 import BeautifulSoup

from scripts.generate_synthetic_dataset import ROOT, SEED_DIR, VERSION, check, generate


class SyntheticV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = generate()
        def rows(name):
            return list(csv.DictReader(io.StringIO(cls.files[name].decode('utf-8-sig'))))
        cls.clean = rows('synthetic_ground_truth.csv')
        cls.mutations = rows('robustness_ground_truth.csv')
        cls.pages = rows('synthetic_pages.csv') + rows('robustness_pages.csv')

    def test_reproducible_bytes_counts_and_balanced_categories(self):
        self.assertEqual(check(ROOT / 'synthetic', self.files), [])
        self.assertEqual(generate(), self.files)
        self.assertEqual((len(self.clean), len(self.mutations), len(self.pages)), (420, 4200, 220))
        sizes = Counter(r['page_id'] for r in self.clean)
        self.assertEqual(Counter(sizes.values()), {n: 2 for n in range(12, 31, 2)})
        self.assertEqual(Counter(r['category'] for r in self.clean),
                         dict(construction_instruments=210, stationery=84, electronics=84, other=42))
        per_template = Counter()
        for p in self.pages[:20]: per_template[p['base_template']] += int(p['n_entities'])
        self.assertEqual(set(per_template.values()), {42})

    def test_original_labels_preserved_and_new_identities_unique(self):
        with (SEED_DIR / 'catalog.csv').open(encoding='utf-8-sig') as f:
            seed = list(csv.DictReader(f))
        current = {r['entity_id']: r for r in self.clean}
        for old in seed:
            self.assertEqual({k: current[old['entity_id']][k] for k in old}, old)
        for rows in [self.clean, self.mutations]:
            self.assertEqual(len({r['entity_id'] for r in rows}), len(rows))
            self.assertEqual(len({(r['page_id'], r['product_url_gt']) for r in rows}), len(rows))
            self.assertEqual(len({(r['page_id'], r['name_gt']) for r in rows}), len(rows))
        for mutation in self.mutations:
            base = current[mutation['base_entity_id']]
            for key in ['base_page_id', 'name_gt', 'price_gt', 'product_url_gt', 'in_stock_gt',
                        'rating_gt', 'delivery_time_gt', 'sku_gt', 'dataset_version']:
                self.assertEqual(mutation[key], base[key])

    def test_all_html_cards_and_structured_data_match_groundtruth(self):
        labels = {}
        for row in self.clean + self.mutations:
            labels.setdefault(row['page_id'], {})[row['sku_gt']] = row
        for page in self.pages:
            soup = BeautifulSoup(self.files[page['benchmark_html_file']], 'lxml')
            cards = soup.select('[data-sku]')
            expected = labels[page['page_id']]
            self.assertEqual(len(cards), int(page['n_entities']))
            self.assertEqual({c['data-sku'] for c in cards}, set(expected))
            for card in cards:
                gt = expected[card['data-sku']]
                link = card.find('a', href=True)
                self.assertEqual(link['href'], gt['product_url_gt'])
                self.assertEqual(link.get_text(), gt['name_gt'])
                price = next(t for t in card.find_all(True) if any(c == 'price' or c.endswith('-price') for c in t.get('class', [])))
                self.assertEqual(price.get_text(), f"{int(gt['price_gt']):,}".replace(',', ' ') + ' ₽')
                if price.has_attr('content'): self.assertEqual(price['content'], gt['price_gt'])
                if card.has_attr('data-product-name'):
                    self.assertEqual(card['data-product-name'], gt['name_gt'])
                    self.assertEqual(card['data-product-price'], gt['price_gt'])
                self.assertIn('В наличии' if gt['in_stock_gt'] == '1' else 'Нет в наличии', card.get_text())
            for script in soup.find_all('script'):
                if script.get('type') == 'application/ld+json':
                    records = json.loads(script.string)['@graph']
                    for r in records:
                        self.assertEqual(r['offers']['price'], int(expected[r['sku']]['price_gt']))
                else:
                    records = json.loads(script.string.split('=', 1)[1].strip().rstrip(';'))['products']
                    for r in records:
                        self.assertEqual(r['price'], int(expected[r['sku']]['price_gt']))
                self.assertEqual(len(records), len(expected))
                for r in records:
                    self.assertEqual(r['name'], expected[r['sku']]['name_gt'])
                    self.assertEqual(r['url'], expected[r['sku']]['product_url_gt'])


if __name__ == '__main__':
    unittest.main()
