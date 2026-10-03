"""Reporting-only adapters, embedded after the frozen benchmark definitions.

They do not alter extraction, prompts, scoring or cache signatures. The adapter's
own hash is recorded separately from the frozen extraction/evaluation contract.
Pandas propagates attrs by deepcopy to each group/column: keep bulky response
ledgers on the original frame, never on the disposable deduplication workspace.
"""
import functools as _reporting_functools
import threading as _reporting_threading
import time as _reporting_time


def _reporting_original(function):
    return getattr(function, '_reporting_original_function', function)


def _reporting_collapse_adapter(function):
    original = _reporting_original(function)

    @_reporting_functools.wraps(original)
    def collapse(pred):
        # DataFrame construction preserves values/index/dtypes without copying attrs.
        # Do not clear pred.attrs: exports still require logs, records and provenance.
        if pred.empty:
            return original(pred)
        return original(pd.DataFrame(pred, copy=True))

    collapse._reporting_original_function = original
    return collapse


def _reporting_provenance():
    return dict(version=1, implementation_sha256=REPORTING_CODE_SHA256,
                source='scripts/benchmark_reporting.py; embedded in notebook',
                scope='attrs-free deduplication workspace and reporting progress only; frozen extraction/scoring/cache contract unchanged')


def _reporting_event(stage, scope, status, elapsed=None, error_type=None):
    event = dict(benchmark_run_id=BENCHMARK_RUN_ID, stage=stage, scope=scope, status=status,
                 at_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=elapsed,
                 reporting_implementation_sha256=REPORTING_CODE_SHA256)
    if error_type:
        event['error_type'] = error_type
    folder = Path(OUTPUT_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'reporting_progress.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')


def _reporting_stage_adapter(function, stage, scope_function, annotate_metadata=False):
    original = _reporting_original(function)

    @_reporting_functools.wraps(original)
    def timed(*args, **kwargs):
        scope = str(scope_function(args, kwargs))
        started = _reporting_time.perf_counter()
        stopped = _reporting_threading.Event()
        print(f'[{scope}] {stage}: started', flush=True)
        _reporting_event(stage, scope, 'started')

        def heartbeat():
            while not stopped.wait(30):
                print(f'[{scope}] {stage}: still running ({_reporting_time.perf_counter() - started:.0f}s)', flush=True)

        worker = _reporting_threading.Thread(target=heartbeat, daemon=True)
        worker.start()
        try:
            result = original(*args, **kwargs)
            if annotate_metadata:
                path = result['run_metadata']
                metadata = json.loads(path.read_text(encoding='utf-8'))
                metadata['reporting_implementation'] = _reporting_provenance()
                path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
            elapsed = _reporting_time.perf_counter() - started
            _reporting_event(stage, scope, 'complete', elapsed)
            print(f'[{scope}] {stage}: complete ({elapsed:.2f}s)', flush=True)
            return result
        except BaseException as exc:
            _reporting_event(stage, scope, 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                             _reporting_time.perf_counter() - started, type(exc).__name__)
            raise
        finally:
            stopped.set()
            worker.join(timeout=1)

    timed._reporting_original_function = original
    return timed


def _reporting_manifest_scope(args, kwargs):
    manifest = args[2] if len(args) > 2 else kwargs['manifest']
    return str(manifest.dataset.iloc[0]) if len(manifest) else 'empty dataset'


def _reporting_dataset_scope(args, kwargs):
    return args[0] if args else kwargs.get('dataset', kwargs.get('dataset_name', 'dataset'))


def _reporting_workbook_scope(args, kwargs):
    return Path(args[0] if args else kwargs['path']).name


collapse_prediction_keys = _reporting_collapse_adapter(collapse_prediction_keys)
evaluate_predictions = _reporting_stage_adapter(evaluate_predictions, 'Evaluate predictions', _reporting_manifest_scope)
export_dataset_outputs = _reporting_stage_adapter(export_dataset_outputs, 'Export CSV / Excel', _reporting_dataset_scope, True)
export_review_workbook = _reporting_stage_adapter(export_review_workbook, 'Write workbook', _reporting_workbook_scope)
if 'export_llm_analysis' in globals():
    export_llm_analysis = _reporting_stage_adapter(export_llm_analysis, 'Paper metrics / cost analysis', _reporting_dataset_scope)
if 'llm_compare_determined' in globals():
    llm_compare_determined = _reporting_stage_adapter(llm_compare_determined, 'Compare determined baseline', _reporting_dataset_scope)
