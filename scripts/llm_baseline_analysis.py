"""Paper diagnostics embedded in the LLM notebook; never used to guide extraction."""
import numpy as np


def paper_name(value):
    text = _clean_text(value).casefold()
    text = re.sub(r'^(?:(?:купить|в корзину|подробнее)\s+)+', '', text)
    return text.rstrip(' .,!?:;—–-')


def paper_evaluate(evaluation, pages):
    """Additional strict metrics; the established baseline CSV contract is unchanged."""
    entities = evaluation.copy()
    if entities.empty:
        entities = pd.DataFrame(columns=EXPORT_EVALUATION_COLUMNS)
    name_gt = entities.gt_name.map(lambda x: bool(paper_name(x)))
    name_pred = entities.pred_name.map(lambda x: bool(paper_name(x)))
    matched = entities.match_status.eq('matched_gt')
    gt_exists = entities.match_status.ne('unmatched_prediction')
    pred_exists = entities.match_status.ne('missing_prediction')
    name_ok = matched & name_gt & entities.gt_name.map(paper_name).eq(entities.pred_name.map(paper_name))
    gt_price = pd.to_numeric(entities.gt_price, errors='coerce')
    pred_price = pd.to_numeric(entities.pred_price, errors='coerce')
    price_target = entities.gt_price_state.eq('present')
    price_ok = matched & price_target & gt_price.eq(pred_price)
    for field, tp, fp, fn in [
        ('name', name_ok, name_pred & (~gt_exists | (name_gt & ~name_ok)), name_gt & ~name_ok),
        ('price', price_ok, pred_price.notna() & (~gt_exists | entities.gt_price_state.eq('absent') | (price_target & ~price_ok)), price_target & ~price_ok),
        ('product_url', matched, ~gt_exists, ~pred_exists),
    ]:
        for label, values in [('TP', tp), ('FP', fp), ('FN', fn)]:
            entities[f'strict_{field}_{label}'] = values.astype(int)
    # Missing-price products remain entities, consistently with the reviewed dataset.
    entities['strict_acceptable_entity'] = (name_ok & (price_ok | (matched & entities.gt_price_state.eq('absent') & pred_price.isna()))).astype(int)
    page_rows, metrics = [], []
    strict_counts = [f'strict_{field}_{count}' for field in ['name', 'price', 'product_url'] for count in ['TP', 'FP', 'FN']]
    for (dataset, variant), group in pages.groupby(['dataset', 'variant'], sort=False):
        scoped_pages = group[group.is_scored.eq(True)].copy()
        ev = entities[entities.dataset.eq(dataset) & entities.variant.eq(variant)]
        grouped = {pid: frame for pid, frame in ev.groupby('page_id')}
        current_pages = []
        for page in scoped_pages.to_dict('records'):
            frame = grouped.get(page['page_id'], ev.iloc[:0])
            status = page.get('extraction_status', 'not_recorded')
            no_error = status in {'ok', 'not_recorded'}
            n_gt = int(page['gt_positive_count'])
            count = int(frame.match_status.ne('missing_prediction').sum())
            acceptable = int(frame.strict_acceptable_entity.sum())
            row = dict(dataset=dataset, variant=variant, page_id=page['page_id'],
                       source=page.get('source'), gt_count=n_gt, predicted_count=count,
                       acceptable_entities=acceptable, extraction_status=status,
                       psr_positive=int(acceptable > 0) if n_gt else None,
                       psr_empty=int(count == 0 and no_error) if not n_gt else None,
                       page_exact=int(no_error and acceptable == n_gt and count == n_gt),
                       **{c: int(frame[c].sum()) for c in strict_counts})
            current_pages.append(row)
        page_rows.extend(current_pages)
        row = dict(dataset=dataset, variant=variant, n_pages=len(scoped_pages),
                   n_positive_pages=sum(x['gt_count'] > 0 for x in current_pages),
                   n_empty_pages=sum(x['gt_count'] == 0 for x in current_pages))
        rng = np.random.default_rng(42)
        samples = rng.integers(0, len(current_pages), size=(1000, len(current_pages))) if current_pages else None
        boot_fields = []
        for field in ['name', 'price', 'product_url']:
            cols = [f'strict_{field}_{c}' for c in ['TP', 'FP', 'FN']]
            tp, fp, fn = [int(ev[c].sum()) for c in cols]
            p, r, f1 = _prf(tp, fp, fn)
            row.update({f'{field}_{c}': v for c, v in zip(['TP', 'FP', 'FN', 'P', 'R', 'F1'], [tp, fp, fn, p, r, f1])})
            if samples is not None:
                counts = np.array([[pg[c] for c in cols] for pg in current_pages], dtype=float)[samples].sum(axis=1)
                denom = 2 * counts[:, 0] + counts[:, 1] + counts[:, 2]
                boot = np.divide(2 * counts[:, 0], denom, out=np.zeros_like(denom), where=denom > 0)
                boot_fields.append(boot)
                row[f'{field}_F1_ci_low'], row[f'{field}_F1_ci_high'] = np.quantile(boot, [.025, .975])
        row['required_macro_F1'] = float(np.mean([row[f'{f}_F1'] for f in ['name', 'price', 'product_url']]))
        if boot_fields:
            row['required_macro_F1_ci_low'], row['required_macro_F1_ci_high'] = np.quantile(np.mean(boot_fields, axis=0), [.025, .975])
        for label, key in [('PSR_positive', 'psr_positive'), ('PSR_empty', 'psr_empty'), ('page_exact_rate', 'page_exact')]:
            values = [pg[key] for pg in current_pages if pg[key] is not None]
            row[label] = float(np.mean(values)) if values else None
        row.update(ci_method='page bootstrap; seed=42; 1000 resamples; exploratory, not a paired significance test',
                   metric_policy='strict normalized name and numeric price; shared URL identity; absent prices allowed')
        metrics.append(row)
    return entities, pd.DataFrame(page_rows), pd.DataFrame(metrics)


def llm_optional_fields(evaluation, raw, gt):
    aliases = {'availability': ['in_stock_gt', 'availability_gt'], 'delivery_time': ['delivery_time_gt'],
               'old_price': ['old_price_gt'], 'image_url': ['image_url_gt'], 'reviews_count': ['reviews_count_gt']}
    pred_columns = {'availability': 'pred_in_stock', 'delivery_time': 'pred_delivery_time',
                    'old_price': 'pred_old_price', 'image_url': 'pred_image_url', 'reviews_count': 'pred_reviews_count'}
    truth = gt[gt._positive].set_index('entity_id').to_dict('index')
    predictions = {(r['variant'], r['html_key'], r['product_url']): r for r in raw.to_dict('records')}
    rows = []
    for ev in evaluation.to_dict('records'):
        if _is_blank(ev.get('entity_id')):
            continue  # Optional-field FP cannot be inferred on unannotated extra entities.
        g = truth.get(ev['entity_id'], {})
        p = predictions.get((ev['variant'], ev['html_key'], ev['product_url']), {})
        for field, candidates in aliases.items():
            column = next((c for c in candidates if c in g and not _is_blank(g[c])), None)
            actual, expected = p.get(pred_columns[field]), g.get(column) if column else None
            if field == 'availability':
                expected = _parse_binary_annotation(expected)
            elif field in {'old_price', 'reviews_count'}:
                expected = parse_price(expected)
            elif field == 'image_url':
                expected, actual = _norm_url(expected), _norm_url(actual)
            else:
                expected, actual = _clean_text(expected) or None, _clean_text(actual) or None
            annotated = column is not None and expected is not None
            correct = bool(annotated and actual == expected)
            predicted = actual is not None and not _is_blank(actual)
            rows.append(dict(dataset=ev['dataset'], variant=ev['variant'], page_id=ev['page_id'],
                             entity_id=ev['entity_id'], result_id=ev['result_id'], field=field,
                             annotated=annotated, gt_value=expected, pred_value=actual,
                             TP=int(correct), FP=int(annotated and predicted and not correct),
                             FN=int(annotated and not correct)))
    detail = pd.DataFrame(rows, columns=['dataset', 'variant', 'page_id', 'entity_id', 'result_id', 'field',
                                       'annotated', 'gt_value', 'pred_value', 'TP', 'FP', 'FN'])
    summary = []
    for (dataset, variant, field), group in detail.groupby(['dataset', 'variant', 'field']):
        annotated = int(group.annotated.sum())
        tp, fp, fn = [int(group[c].sum()) for c in ['TP', 'FP', 'FN']]
        p, r, f1 = _prf(tp, fp, fn)
        summary.append(dict(dataset=dataset, variant=variant, field=field, n_gt_entities=len(group),
                            n_annotated=annotated, coverage=annotated / len(group), TP=tp, FP=fp, FN=fn,
                            P=p if annotated else None, R=r if annotated else None, F1=f1 if annotated else None,
                            scope='annotated GT entities only; blank means unknown; extras unscored'))
    return detail, pd.DataFrame(summary)


def llm_cost_summary(records):
    attempts = llm_attempt_table(records)
    n = len(records)
    result = dict(pages=n, logical_requests=n, http_attempts=len(attempts),
                  new_http_attempts=sum(r['new_attempts'] for r in records),
                  replayed_pages=sum(r['reused'] for r in records),
                  failed_pages=sum(r['status'] != 'ok' for r in records),
                  statuses=pd.Series([r['status'] for r in records]).value_counts().to_dict(),
                  estimates_currency='RUB', estimated_cost_source='saved RouterAI /models snapshot; not invoice',
                  pricing_snapshot=MODEL_SNAPSHOT.get('pricing'),
                  latency_interpretation='diagnostics on this machine/network; not a controlled benchmark')
    for field in ['prompt_tokens', 'completion_tokens', 'reasoning_tokens', 'estimated_cost_rub', 'latency_ms']:
        series = pd.to_numeric(attempts[field], errors='coerce') if field in attempts else pd.Series(dtype=float)
        result[field] = dict(known_total=float(series.sum()) if series.notna().any() else None,
                             unknown_attempts=int(series.isna().sum()), known_attempts=int(series.notna().sum()))
    result['mean_attempts_per_page'] = len(attempts) / n if n else None
    result['p95_attempts_per_page'] = float(np.quantile([len(r['attempts']) for r in records], .95)) if n else None
    costs = []
    for record in records:
        frame = llm_attempt_table([record])
        if not frame.empty and frame.estimated_cost_rub.notna().all():
            costs.append(float(frame.estimated_cost_rub.sum()))
    result['estimated_cost_rub_per_page'] = dict(complete_cost_pages=len(costs),
        mean=float(np.mean(costs)) if costs else None, p95=float(np.quantile(costs, .95)) if costs else None)
    return result


def export_llm_analysis(dataset, raw, gt, evaluation, paths):
    out = OUTPUT_DIR / dataset
    pages = pd.read_csv(paths['page_results'])
    entities, strict_pages, strict_metrics = paper_evaluate(evaluation, pages)
    optional_detail, optional_metrics = llm_optional_fields(evaluation, raw, gt)
    for name, frame in [('paper_entity_results', entities), ('paper_page_results', strict_pages),
                        ('paper_required_metrics', strict_metrics), ('paper_optional_results', optional_detail),
                        ('paper_optional_metrics', optional_metrics)]:
        target = out / (name + '.csv')
        frame.to_csv(target, index=False, encoding='utf-8-sig')
        paths[name] = target
    cost_path = out / 'llm_cost_summary.json'
    llm_write_json(cost_path, llm_cost_summary(raw.attrs['llm_records']))
    paths['llm_cost_summary'] = cost_path
    for name in ['llm_attempts', 'llm_rejected_rows']:
        paths[name] = out / (name + '.csv')
    paths['llm_page_responses'] = out / 'llm_page_responses.json'
    metadata_path = paths['run_metadata']
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    metadata.update(extraction_contract=extraction_contract(), model_snapshot=MODEL_SNAPSHOT,
                    experiment_id=EXPERIMENT_ID,
                    llm_complete=len(raw.attrs['llm_records']) == int(raw.attrs.get('expected_pages', len(raw.attrs['llm_records']))),
                    extraction_errors=sum(r['status'] != 'ok' for r in raw.attrs['llm_records']),
                    calls_directory='llm_calls; request bodies compressed, response fingerprints in result.json')
    metadata['files'].update({k: dict(file=p.name, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                              for k, p in paths.items() if k != 'run_metadata'})
    llm_write_json(metadata_path, metadata)
    return strict_metrics


def llm_compare_determined(dataset, gt, manifest):
    """Read-only: refuse comparisons with changed GT/HTML or edited baseline outputs."""
    folder = BENCHMARK_ROOT / 'baseline_results/determined' / dataset
    meta_path = folder / 'benchmark_run.json'
    if not meta_path.is_file():
        return None, {'dataset': dataset, 'status': 'missing_determined_results'}
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    if meta['reference_sha256'] != gt.attrs['reference_sha256']:
        return None, {'dataset': dataset, 'status': 'different_gt'}
    for key in ['predictions', 'metrics', 'page_results', 'manifest']:
        item = meta['files'][key]
        if hashlib.sha256((folder / item['file']).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Modified deterministic result: ' + key)
    saved_manifest = pd.read_csv(folder / meta['files']['manifest']['file'])
    current = manifest.set_index('page_id').content_sha256.fillna('').to_dict()
    previous = saved_manifest.set_index('page_id').content_sha256.fillna('').to_dict()
    if current != previous:
        return None, {'dataset': dataset, 'status': 'different_page_or_html_scope'}
    if meta.get('name_threshold') != NAME_THRESHOLD or meta.get('price_relative_tolerance') != PRICE_REL_TOL:
        return None, {'dataset': dataset, 'status': 'different_evaluation_thresholds'}
    tables = {k: pd.read_csv(folder / meta['files'][k]['file'], low_memory=False) for k in ['predictions', 'metrics', 'page_results']}
    _, _, strict = paper_evaluate(tables['predictions'], tables['page_results'])
    tables['strict_metrics'] = strict
    return tables, {'dataset': dataset, 'status': 'verified', 'benchmark_run_id': meta['benchmark_run_id'],
                    'metadata_sha256': hashlib.sha256(meta_path.read_bytes()).hexdigest()}
