"""Reproduce synthetic v2 (12–30 products/page) without any benchmark predictions.

The frozen v1 labels and markup blueprints are under config/synthetic_seed/.
Default: validate checked-in data. --write explicitly regenerates it.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import html
import io
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / 'config/synthetic_seed'
VERSION = 'synthetic-v2-12to30'
AXES = {'class_rename': 3, 'wrapper_insert': 3, 'field_reorder': 2, 'subtree_relocate': 2}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def csv_bytes(rows: list[dict]) -> bytes:
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


def products_for_page(page: dict, catalog: list[dict]) -> list[dict]:
    """Keep the original five labels; add distinct products before variant series."""
    products = [dict(r, product_origin='v1') for r in catalog if r['page_id'] == page['page_id']]
    pool = list({r['name_gt']: r for r in reversed(catalog) if r['category'] == page['category']}.values())
    pool.sort(key=lambda r: r['name_gt'])
    used = {p['name_gt'] for p in products}
    page_number = int(page['page_id'].split('-')[-1])
    candidate = 0
    while len(products) < page['n_entities']:
        base = pool[candidate % len(pool)]
        series = candidate // len(pool) + 1
        candidate += 1
        name = base['name_gt'] + (f' — серия {series}' if series > 1 else '')
        if name in used:
            continue
        used.add(name)
        slot = len(products) + 1
        entity = f"{page['page_id']}-e{slot:02}"
        slug = re.sub(r'-\d+-\d+$', '', base['product_url_gt'].rsplit('/', 1)[-1])
        # Deterministic, small price variation; no extractor feedback or model calls.
        price = round(int(base['price_gt']) * (100 + (page_number * 7 + slot * 3) % 13) / 100)
        products.append(dict(base, page_id=page['page_id'], entity_id=entity,
            name_gt=name, price_gt=price,
            product_url_gt=f'https://synthetic-shop.local/product/{slug}-{page_number:02}-{slot:02}',
            sku_gt=f'SYN-{page_number:02}-{slot:02}', base_page_id=page['page_id'], base_entity_id=entity,
            html_file=f"html/{page['page_id']}.html", mutation_axis='clean', product_origin='expanded'))
    for product in products:
        product['price_gt'] = int(product['price_gt'])
        product['in_stock_gt'] = int(product['in_stock_gt'])
        product['rating_gt'] = float(product['rating_gt'])
        product['dataset_version'] = VERSION
    return products


def render(blueprint: dict, page: dict, products: list[dict]) -> bytes:
    cards = []
    for p in products:
        values = dict(NAME=p['name_gt'], URL=p['product_url_gt'], SKU=p['sku_gt'],
                      PRICE=str(p['price_gt']), PRICE_DISPLAY=f"{p['price_gt']:,}".replace(',', ' ') + ' ₽',
                      STOCK='В наличии' if p['in_stock_gt'] else 'Нет в наличии',
                      RATING=str(p['rating_gt']), DELIVERY=p['delivery_time_gt'])
        card = blueprint['card']
        for key, value in values.items():
            card = card.replace('{{' + key + '}}', html.escape(value, quote=True))
        cards.append(card)
    document = blueprint['document'].replace('{{PAGE_ID}}', page['page_id']).replace('{{CATEGORY}}', page['category'])
    document = document.replace('{{CARDS}}', '\n'.join(cards))
    if blueprint['script_kind'] == 'jsonld':
        structured = {'@context': 'https://schema.org', '@graph': [dict(
            **{'@type': 'Product'}, sku=p['sku_gt'], name=p['name_gt'], url=p['product_url_gt'],
            offers={'@type': 'Offer', 'priceCurrency': 'RUB', 'price': p['price_gt'],
                    'availability': 'https://schema.org/' + ('InStock' if p['in_stock_gt'] else 'OutOfStock')},
            aggregateRating={'@type': 'AggregateRating', 'ratingValue': p['rating_gt'], 'reviewCount': 11 + i}
        ) for i, p in enumerate(products)]}
        script = json.dumps(structured, ensure_ascii=False)
    elif blueprint['script_kind'] == 'state':
        structured = {'products': [{key: p[field] for key, field in dict(sku='sku_gt', name='name_gt',
            price='price_gt', url='product_url_gt', in_stock='in_stock_gt', rating='rating_gt',
            delivery_time='delivery_time_gt', region='region_gt', subcategory='subcategory').items()} for p in products]}
        script = 'window.__INITIAL_STATE__ = ' + json.dumps(structured, ensure_ascii=False) + ';'
    else:
        script = ''
    document = document.replace('{{STRUCTURED_DATA}}', script)
    if re.search(r'\{\{[A-Z_]+\}\}', document):
        raise ValueError('Unresolved blueprint placeholder')
    return (document.rstrip() + '\n').encode('utf-8')


def generate(seed_dir: Path = SEED_DIR) -> dict[str, bytes]:
    with (seed_dir / 'catalog.csv').open(encoding='utf-8-sig', newline='') as f:
        catalog = list(csv.DictReader(f))
    pages = json.loads((seed_dir / 'pages.json').read_text(encoding='utf-8'))
    templates = json.loads((seed_dir / 'templates.json').read_text(encoding='utf-8'))
    files, clean_gt, mutated_gt, clean_pages, mutated_pages = {}, [], [], [], []
    for page in pages:
        products = products_for_page(page, catalog)
        clean_gt.extend(products)
        variants = [('clean', 0)] + [(a, v) for a, n in AXES.items() for v in range(1, n + 1)]
        for axis, v in variants:
            pid = page['page_id'] if axis == 'clean' else f"{page['page_id']}__{axis}__{v:02}"
            filename = f'html/{pid}.html'
            payload = render(templates[f"{page['base_template']}/{axis}/{v}"], page, products)
            files[filename] = payload
            row = dict(page_id=pid, base_page_id=page['page_id'], category=page['category'],
                base_template=page['base_template'], mutation_axis=axis, variant_idx=v,
                artifact_path=filename, benchmark_html_file=filename, n_entities=len(products),
                sha256=sha(payload), source='synthetic-shop.local',
                page_url=f"https://synthetic-shop.local/search/{page['page_id']}", dataset_version=VERSION)
            if axis == 'clean':
                clean_pages.append(row)
            else:
                mutated_pages.append(dict(mutation_id=pid, **row))
                for p in products:
                    mutated_gt.append(dict(mutation_id=pid, **(p | dict(
                        page_id=pid, entity_id=pid + '-e' + p['entity_id'].rsplit('-e', 1)[1],
                        html_file=filename, mutation_axis=axis))))
    files.update({name: csv_bytes(rows) for name, rows in [
        ('synthetic_ground_truth.csv', clean_gt), ('synthetic_pages.csv', clean_pages),
        ('robustness_ground_truth.csv', mutated_gt), ('robustness_pages.csv', mutated_pages)]})
    seed_hashes = {p.name: sha(p.read_bytes()) for p in sorted(seed_dir.iterdir()) if p.suffix in {'.csv', '.json'}}
    config = dict(dataset_version=VERSION, seed=20260928, n_clean_pages=len(pages),
        entities_per_page={'min': 12, 'max': 30, 'by_page': {p['page_id']: p['n_entities'] for p in pages}},
        category_distribution={'construction_instruments': .5, 'stationery': .2, 'electronics': .2, 'other': .1},
        other_subcategories=['household_chemicals', 'ppe', 'containers'],
        robustness_mutations_per_clean_page=10, robustness_axes=AXES, seed_files_sha256=seed_hashes,
        generator='scripts/generate_synthetic_dataset.py', generator_sha256=sha(Path(__file__).read_bytes()))
    summary = dict(dataset_version=VERSION, seed=20260928, clean_pages=len(pages), clean_entities=len(clean_gt),
        robustness_pages=len(mutated_pages), robustness_entities=len(mutated_gt),
        products_per_page_counts=dict(sorted(Counter(p['n_entities'] for p in pages).items())),
        page_category_counts=dict(Counter(p['category'] for p in pages)),
        entity_category_counts=dict(Counter(p['category'] for p in clean_gt)),
        other_subcategory_counts=dict(Counter(p['subcategory'] for p in clean_gt if p['category'] == 'other')),
        mutation_axis_counts=dict(Counter(p['mutation_axis'] for p in mutated_pages)),
        templates=dict(Counter(p['base_template'] for p in pages)))
    files['config.json'] = json_bytes(config)
    files['dataset_summary.json'] = json_bytes(summary)
    return files


def check(root: Path, expected: dict[str, bytes]) -> list[str]:
    differences = [name for name, data in expected.items() if not (root / name).is_file() or (root / name).read_bytes() != data]
    differences += [p.relative_to(root).as_posix() for p in (root / 'html').glob('*.html')
                    if p.relative_to(root).as_posix() not in expected]
    return sorted(set(differences))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT / 'synthetic')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    files = generate()
    if args.write:
        for name, data in files.items():
            destination = args.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_file() and destination.read_bytes() == data:
                continue
            temporary = destination.with_name(destination.name + '.tmp')
            temporary.write_bytes(data)
            temporary.replace(destination)
    differences = check(args.root, files)
    if differences:
        raise SystemExit('Dataset differs from generator: ' + ', '.join(differences))
    print(f'PASS: {VERSION}; 20 clean pages / 420 entities; 200 mutations / 4200 entities; exact bytes verified.')


if __name__ == '__main__':
    main()
