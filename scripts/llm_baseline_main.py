"""Experiment orchestration embedded in baseline_llm.ipynb."""


def llm_selected_datasets():
    selected = list(DATASETS) if RUN_DATASETS is None else list(RUN_DATASETS)
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(DATASETS):
        raise ValueError('Select distinct dataset names from ' + str(list(DATASETS)))
    return selected


def llm_dataset_inputs(dataset):
    config = DATASETS[dataset]
    reference = config.get('reference_file') or discover_dataset_reference(Path(config['root']))
    if reference is None:
        raise FileNotFoundError('GroundTruth is required for benchmark: ' + dataset)
    manifest = build_html_manifest(dataset, Path(config['html_dir']), Path(reference))
    if PAGE_LIMIT is not None:
        if not isinstance(PAGE_LIMIT, int) or PAGE_LIMIT < 1:
            raise ValueError('PAGE_LIMIT must be a positive integer or None')
        manifest = manifest.loc[manifest.skip_reason.eq('')].head(PAGE_LIMIT).copy()
        manifest.attrs['selection'] = 'explicit smoke-test subset; not a full-corpus result'
    gt = read_groundtruth(Path(reference), 'GroundTruth', zero_price_is_missing=(dataset == 'industrial'),
                          annotation_policy=ANNOTATION_POLICY)
    gt = align_groundtruth_to_manifest(gt, manifest)
    return manifest, gt, groundtruth_audit(gt, manifest)


def llm_plan():
    """Entirely offline. No key loading, HTTP request or fake extraction/metrics."""
    pages, audits = [], {}
    for dataset in llm_selected_datasets():
        manifest, gt, audit = llm_dataset_inputs(dataset)
        audits[dataset] = audit
        for page in manifest.to_dict('records'):
            row = {k: page.get(k) for k in ['dataset', 'page_id', 'html_key', 'source', 'query', 'skip_reason', 'content_sha256']}
            if not page['skip_reason']:
                raw = Path(page['html_path']).read_bytes()
                text = raw.decode('utf-8', errors='replace')
                payload = llm_payload(page, text)
                size = sum(len(m['content'].encode('utf-8')) for m in payload['messages'])
                # Size proxies only. The provider tokenizer is authoritative; never gate on these.
                row.update(html_bytes=len(raw), html_characters=len(text),
                           utf8_replacement_characters=text.count('\ufffd'),
                           input_tokens_size_proxy_low=size / 6, input_tokens_size_proxy_high=size / 2,
                           context_risk_by_size_proxy=size / 2 + LLM_CONFIG['max_output_tokens'] > MODEL_SNAPSHOT['context_length'])
            pages.append(row)
        print(f"{dataset}: {len(manifest)} manifest pages; {manifest.skip_reason.eq('').sum()} HTML requests; "
              f"GT entities={int(gt._positive.sum())}")
    table = pd.DataFrame(pages)
    out = OUTPUT_DIR / 'plan'
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / 'llm_input_plan.csv', index=False, encoding='utf-8-sig')
    n = int(table.skip_reason.eq('').sum())
    pricing = MODEL_SNAPSHOT.get('pricing', {})
    plan = dict(status='PLAN_ONLY_NO_INFERENCE', experiment_id=EXPERIMENT_ID, model=LLM_CONFIG['model'],
                provider='RouterAI', selected_datasets=llm_selected_datasets(), planned_page_requests=n,
                maximum_http_attempts=n * LLM_CONFIG['max_attempts'], extraction_contract=extraction_contract(),
                model_snapshot=MODEL_SNAPSHOT, audits=audits,
                estimated_input_cost_rub_size_proxy_low=float(table.input_tokens_size_proxy_low.sum()) * float(pricing.get('prompt', 0)),
                estimated_input_cost_rub_size_proxy_high=float(table.input_tokens_size_proxy_high.sum()) * float(pricing.get('prompt', 0)),
                maximum_output_cost_rub_per_attempt=LLM_CONFIG['max_output_tokens'] * float(pricing.get('completion', 0)),
                cost_note='Input proxies are not tokenizer counts or a spending limit. Output, retries and price changes add cost.',
                next_step='Set RUN_MODE="run" in notebook, or python scripts/run_llm_benchmark.py --run')
    llm_write_json(out / 'run_plan.json', plan)
    print(f"Plan only: {n} pages, no model calls. Report: {out / 'run_plan.json'}")
    return plan


def llm_prepare_experiment():
    global MODEL_SNAPSHOT
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', EXPERIMENT_ID) or EXPERIMENT_ID in {'.', '..'}:
        raise ValueError('EXPERIMENT_ID must be a simple directory name')
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    contract = extraction_contract()
    path = OUTPUT_DIR / 'experiment.json'
    if path.is_file():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved['extraction_contract'] != contract:
            raise RuntimeError('Experiment code/settings changed. Use a new EXPERIMENT_ID; old responses remain intact.')
        MODEL_SNAPSHOT = saved['model_snapshot']
    else:
        if RUN_MODE == 'replay':
            raise RuntimeError('No experiment to replay')
        with urllib.request.urlopen(LLM_CONFIG['base_url'].rstrip('/') + '/models', timeout=30) as response:
            catalog = json.load(response)
        matches = [m for m in catalog['data'] if m['id'] == LLM_CONFIG['model']]
        if len(matches) != 1:
            raise RuntimeError('Pinned model not available. No substitute model will be used.')
        MODEL_SNAPSHOT = dict(matches[0], snapshot_date=datetime.now(timezone.utc).isoformat(),
                              snapshot_source=LLM_CONFIG['base_url'].rstrip('/') + '/models', pricing_currency='RUB')
        required = {'temperature', 'max_tokens', 'response_format', 'reasoning'}
        if not required.issubset(MODEL_SNAPSHOT.get('supported_parameters', [])):
            raise RuntimeError('Model catalog does not advertise all required parameters')
        if (MODEL_SNAPSHOT.get('reasoning') or {}).get('mandatory'):
            raise RuntimeError('Model requires reasoning; this protocol requests reasoning disabled. Revise explicitly.')
        llm_write_json(path, dict(extraction_contract=contract, model_snapshot=MODEL_SNAPSHOT,
                                 created_at_utc=datetime.now(timezone.utc).isoformat()))
    if RUN_MODE == 'run':
        llm_api_key()  # Validate locally before creating any attempt ledger entry.
    return path


def _llm_main_unlocked():
    global OUTPUT_DIR
    OUTPUT_DIR = BENCHMARK_ROOT / 'baseline_results' / 'llm' / EXPERIMENT_ID
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', EXPERIMENT_ID) or EXPERIMENT_ID in {'.', '..'}:
        raise ValueError('Invalid EXPERIMENT_ID')
    if RUN_MODE == 'plan':
        return llm_plan()
    if RUN_MODE not in {'run', 'replay'}:
        raise ValueError('RUN_MODE must be plan, run or replay')
    llm_prepare_experiment()
    all_eval, all_metrics, all_pages, all_raw, strict_parts = [], [], [], [], []
    comparison_eval, comparison_metrics, comparison_pages, comparison_provenance = [], [], [], []
    status_path = OUTPUT_DIR / 'run_status.json'
    state = dict(benchmark_run_id=BENCHMARK_RUN_ID, status='running', mode=RUN_MODE,
                 selected_datasets=llm_selected_datasets(), completed_datasets=[],
                 full_corpus=PAGE_LIMIT is None, model=LLM_CONFIG['model'])
    llm_write_json(status_path, state)
    try:
        for dataset in llm_selected_datasets():
            manifest, gt, audit = llm_dataset_inputs(dataset)
            out = OUTPUT_DIR / dataset
            out.mkdir(parents=True, exist_ok=True)
            # Freeze corpus and GT independently of model results, before the first paid request.
            input_contract = dict(manifest=[{k: r.get(k) for k in ['page_id', 'html_key', 'content_sha256', 'page_url', 'source_url', 'query', 'skip_reason']}
                                            for r in manifest.fillna('').to_dict('records')],
                                  reference_sha256=gt.attrs['reference_sha256'])
            input_path = out / 'input_contract.json'
            if input_path.is_file() and json.loads(input_path.read_text(encoding='utf-8')) != input_contract:
                raise RuntimeError('Dataset changed. Use a new EXPERIMENT_ID: ' + dataset)
            llm_write_json(input_path, input_contract)
            raw = run_llm_baseline(manifest)
            raw.attrs['expected_pages'] = int(manifest.skip_reason.eq('').sum())
            evaluation, metrics = evaluate_predictions(raw, gt, manifest, VARIANTS)
            paths = export_dataset_outputs(dataset, manifest, raw, gt, audit, evaluation, metrics)
            strict = export_llm_analysis(dataset, raw, gt, evaluation, paths)
            pages = pd.read_csv(paths['page_results'])
            all_eval.append(evaluation); all_metrics.append(metrics); all_pages.append(pages); all_raw.append(raw)
            strict_parts.append(strict)
            comparison_eval.append(evaluation); comparison_metrics.append(metrics); comparison_pages.append(pages)
            determined, provenance = llm_compare_determined(dataset, gt, manifest)
            comparison_provenance.append(provenance)
            if determined is not None:
                comparison_eval.append(determined['predictions'])
                comparison_metrics.append(determined['metrics'])
                comparison_pages.append(determined['page_results'])
                strict_parts.append(determined['strict_metrics'])
            state['completed_datasets'].append(dataset)
            llm_write_json(status_path, state)
            print(paper_table(metrics).round(4).to_string(index=False))
        evaluation = pd.concat(all_eval, ignore_index=True)
        metrics = pd.concat(all_metrics, ignore_index=True)
        pages = pd.concat(all_pages, ignore_index=True)
        raw = pd.concat([frame.copy().set_flags(allows_duplicate_labels=True) for frame in all_raw], ignore_index=True)
        for name, frame in [('baseline_predictions_all', evaluation.reindex(columns=EXPORT_EVALUATION_COLUMNS)),
                            ('baseline_metrics_all', metrics), ('baseline_page_results_all', pages),
                            ('baseline_raw_predictions_all', raw)]:
            frame.to_csv(OUTPUT_DIR / (name + '.csv'), index=False, encoding='utf-8-sig')
        export_review_workbook(OUTPUT_DIR / 'baseline_review_all.xlsx', evaluation, metrics, pages,
                              provenance={'experiment': EXPERIMENT_ID, 'contract': extraction_contract()})
        comparison = pd.concat(comparison_eval, ignore_index=True)
        export_review_workbook(OUTPUT_DIR / 'baseline_comparison.xlsx', comparison,
                              pd.concat(comparison_metrics, ignore_index=True), pd.concat(comparison_pages, ignore_index=True),
                              provenance={'determined_results': comparison_provenance, 'llm_contract': extraction_contract()})
        strict = pd.concat(strict_parts, ignore_index=True)
        strict.to_csv(OUTPUT_DIR / 'paper_required_metrics_all.csv', index=False, encoding='utf-8-sig')
        deltas = []
        for variant, group in strict.groupby('variant'):
            values = group.set_index('dataset').required_macro_F1.to_dict()
            for dataset, label in [('industrial', 'synthetic_to_industrial'), ('robustness', 'synthetic_to_robustness')]:
                if 'synthetic' in values and dataset in values:
                    deltas.append(dict(variant=variant, comparison=label, delta_F1=values['synthetic'] - values[dataset],
                                       note='aggregate required-field macro F1; see per-page IDs and mutation columns for paired analysis'))
        pd.DataFrame(deltas, columns=['variant', 'comparison', 'delta_F1', 'note']).to_csv(
            OUTPUT_DIR / 'paper_robustness_deltas.csv', index=False, encoding='utf-8-sig')
        llm_write_json(OUTPUT_DIR / 'comparison_provenance.json', comparison_provenance)
        state.update(status='complete', completed_at_utc=datetime.now(timezone.utc).isoformat(),
                     note='Complete corpus traversal; inspect llm_cost_summary for extraction/API failures.')
        llm_write_json(status_path, state)
        print('Finished. Comparison Excel:', OUTPUT_DIR / 'baseline_comparison.xlsx')
        return state
    except BaseException as exc:
        state.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed', error_type=type(exc).__name__)
        llm_write_json(status_path, state)
        raise


def llm_main():
    """An OS lock prevents simultaneous notebook/CLI invocations from charging twice."""
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', EXPERIMENT_ID) or EXPERIMENT_ID in {'.', '..'}:
        raise ValueError('Invalid EXPERIMENT_ID')
    if RUN_MODE == 'plan':
        return _llm_main_unlocked()
    folder = BENCHMARK_ROOT / 'baseline_results/llm' / EXPERIMENT_ID
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / '.execution.lock').open('a+b') as lock:
        if lock.seek(0, 2) == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('This experiment is already running in another process.') from exc
        try:
            return _llm_main_unlocked()
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)
