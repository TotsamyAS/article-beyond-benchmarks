"""Verify notebook outputs against the original comparison Excel, without extraction."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ('original_fast', 'original_fast_query')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(output: Path, reference: Path):
    metadata = json.loads((output / 'benchmark_run.json').read_text(encoding='utf-8'))
    reference_provenance = pd.read_excel(reference, sheet_name='Происхождение').set_index('key')['value'].to_dict()
    assert metadata['reference_sha256'] == reference_provenance['reference_sha256'], 'GT version differs from reference'
    sources = {k.replace('\\', '/'): v for k, v in json.loads(reference_provenance['source_sha256']).items()}
    for name, fingerprint in metadata['original_parser']['source_sha256'].items():
        assert fingerprint == sources['services/fast-renderer/' + name], f'Original source differs: {name}'
    for item in metadata['files'].values():
        if sha(output / item['file']) != item['sha256']:
            raise ValueError(f"Result file fingerprint changed: {item['file']}")
    expected_metrics = pd.read_excel(reference, sheet_name='Метрики')
    expected_metrics = expected_metrics[expected_metrics.variant.isin(VARIANTS)].set_index('variant').sort_index()
    metrics = pd.read_csv(output / 'baseline_metrics.csv').set_index('variant').loc[list(VARIANTS)].sort_index()
    numeric = [c for c in metrics if c not in ['benchmark_run_id', 'dataset']]
    pd.testing.assert_frame_equal(metrics[numeric], expected_metrics[numeric], check_dtype=False,
                                  check_exact=False, rtol=0, atol=1e-12)

    expected = pd.read_excel(reference, sheet_name='Все_результаты')
    expected = expected[expected.variant.isin(VARIANTS)].copy()
    actual = pd.read_csv(output / 'baseline_predictions.csv', low_memory=False)
    actual = actual[actual.variant.isin(VARIANTS)].copy()
    keys = ['variant', 'result_id']
    if actual.duplicated(keys).any() or expected.duplicated(keys).any():
        raise ValueError('Ambiguous result IDs in comparison')
    expected = expected.set_index(keys).sort_index()
    actual = actual.set_index(keys).sort_index()
    pd.testing.assert_index_equal(actual.index, expected.index)
    numeric_fields = {'gt_id', 'gt_price', 'pred_price', 'name_ok', 'price_ok', 'price_presence_ok',
                      'unexpected_price_FP', 'name_similarity', 'price_error_signed', 'price_error_absolute',
                      'price_error_relative', 'price_error_relative_absolute'}
    numeric_fields.update(f'{field}_{count}' for field in ['entity', 'name', 'price'] for count in ['TP', 'FP', 'FN'])
    compared = []
    for column in actual:
        if column == 'benchmark_run_id':
            continue
        if column in numeric_fields:
            np.testing.assert_allclose(pd.to_numeric(actual[column]), pd.to_numeric(expected[column]),
                                       rtol=0, atol=1e-8 if 'price' in column else 1e-12, equal_nan=True,
                                       err_msg=f'Field mismatch: {column}')
        else:
            pd.testing.assert_series_equal(actual[column].fillna('').astype(str),
                                           expected[column].fillna('').astype(str), check_names=False)
        compared.append(column)
    for variant, row in metrics.iterrows():
        frame = actual.loc[variant]
        for field in ['entity', 'name', 'price']:
            tp, fp, fn = [int(frame[f'{field}_{count}'].sum()) for count in ['TP', 'FP', 'FN']]
            assert (tp, fp, fn) == tuple(row[f'{field}_{c}'] for c in ['TP', 'FP', 'FN'])
            f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0
            assert abs(f1 - row[f'{field}_F1']) < 1e-12

    response_comparison = None
    reference_responses = reference.parent / 'original_pages.jsonl'
    if reference_responses.is_file():
        original = {r['page_id']: r for r in map(json.loads, reference_responses.read_text(encoding='utf-8').splitlines())}
        current = {r['page_id']: r for r in map(json.loads, (output / 'original_parser_responses.jsonl').read_text(encoding='utf-8').splitlines())}
        assert set(current) == set(original)
        for page_id in current:
            for field in ['result', 'queried', 'status']:
                assert current[page_id][field] == original[page_id][field], f'Original JSON response differs: {page_id}/{field}'
        response_comparison = {'pages': len(current), 'all_original_responses_equal': True,
                               'reference_sha256': sha(reference_responses)}
    report = {
        'status': 'PASS', 'benchmark_run_id': metadata['benchmark_run_id'],
        'reference_excel': str(reference), 'reference_excel_sha256': sha(reference),
        'compared_result_rows': len(actual), 'compared_columns': compared,
        'metrics_equal': True, 'entity_identity_and_all_names_prices_equal': True,
        'json_response_comparison': response_comparison,
        'original_parser': metadata.get('original_parser'),
        'metrics': json.loads(metrics.reset_index().to_json(orient='records')),
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'baseline_results/determined/industrial')
    parser.add_argument('--reference', type=Path, default=ROOT / 'baseline_results/validation/original/original_comparison.xlsx')
    args = parser.parse_args()
    report = verify(args.output, args.reference)
    path = args.output.parent / 'original_parity.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS: original notebook predictions and metrics equal the reference Excel.')
    print('Compared rows:', report['compared_result_rows'])
    print('Original JSON responses:', report['json_response_comparison'])
    print('Report:', path)


if __name__ == '__main__':
    main()
