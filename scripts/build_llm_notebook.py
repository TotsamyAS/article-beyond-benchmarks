"""Build the self-contained LLM notebook from the verified shared evaluation cells."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    original = json.loads((ROOT / 'baseline_determined.ipynb').read_text(encoding='utf-8'))
    sources = {i: ''.join(cell['source']) for i, cell in enumerate(original['cells'])}
    scripts = [ROOT / 'scripts' / name for name in ['llm_baseline_runtime.py', 'llm_baseline_analysis.py', 'llm_baseline_main.py']]
    implementations = [p.read_text(encoding='utf-8') for p in scripts]
    reporting = (ROOT / 'scripts/benchmark_reporting.py').read_text(encoding='utf-8')
    model = json.loads((ROOT / 'config/llm_model_snapshot.json').read_text(encoding='utf-8'))
    shared = {str(i): hashlib.sha256(sources[i].encode()).hexdigest() for i in [6, 8, 14, 16]}
    config = sources[4].split('# Exact original parser modes')[0]
    config = config.replace('"baseline_results" / "determined"', '"baseline_results" / "llm"')
    config = config.replace('# None runs all datasets. To rerun only the corrected synthetic corpus use:\n# RUN_DATASETS = ("synthetic", "robustness")\n# Other datasets\' verified saved results are retained in the combined CSVs.', '# None runs all three datasets. Or set ("industrial",).')
    config += '''
# A stable experiment name resumes saved responses. Use a new name for an independent repetition.
EXPERIMENT_ID = "qwen38-omni-flash-synthetic-v2"
RUN_MODE = "plan"  # "plan": offline audit; "run": paid API calls; "replay": saved responses only
PAGE_LIMIT = None  # Small integer for a smoke test; use a separate EXPERIMENT_ID for it.
RETRY_ERRORS = False  # Resume normally skips completed errors; True explicitly retries those pages.
RETRY_UNCERTAIN = False  # A killed in-flight request may have been billed; retry only explicitly.
VARIANTS = ("llm_full_html",)
LLM_CONFIG = {
    "provider": "RouterAI",
    "base_url": "https://routerai.ru/api/v1",
    "model": "qwen/qwen3.8-omni-flash",
    "temperature": 0.0,
    "max_output_tokens": 65536,
    "timeout_seconds": 180,
    "max_attempts": 3,  # Transport/429/5xx only. No JSON repair or context-shortening retries.
}
OUTPUT_DIR = OUTPUT_DIR / EXPERIMENT_ID
NAME_THRESHOLD = 0.85
PRICE_REL_TOL = 0.01
MAX_CANDIDATES_PER_HTML = 5000  # Shared metadata only; LLM output is not capped by card count.
EXCLUDE_DERIVED_ARTIFACT_HTML = True
ANNOTATION_POLICY = "labeled"
PARSER_CONTRACT_FILE = None
BENCHMARK_RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
'''
    config += '\nMODEL_SNAPSHOT = ' + repr(model) + '\n'
    config += 'LLM_CODE_SHA256 = ' + repr(hashlib.sha256('\n'.join(implementations).encode()).hexdigest()) + '\n'
    config += 'SHARED_CONTRACT_SHA256 = ' + repr(shared) + '\n'
    adapter = sources[12].split('def run_legacy_baselines')[0]
    exports = sources[16].replace('original_parser_contract', 'extraction_contract')
    exports = exports.replace('"original_parser"', '"llm_contract"').replace('"baseline_determined.ipynb"', '"baseline_llm.ipynb"')
    exports = exports.replace('DISPLAY_NAMES = {', 'DISPLAY_NAMES = {\n    "llm_full_html": "LLM-only full HTML",')
    cells = []

    def add(kind, source, tag=None):
        cell = dict(cell_type=kind, metadata={'tags': [tag]} if tag else {}, source=source.splitlines(keepends=True),
                    id=hashlib.sha256((str(len(cells)) + source).encode()).hexdigest()[:12])
        if kind == 'code':
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)

    add('markdown', '''# LLM-only baseline: Qwen3.8 Omni Flash / RouterAI

Paper §4.3 P_llm: **one complete saved HTML document per logical request**, eight product fields,
no DOM distillation, candidate selection, preflight gate, browser, screenshot or deterministic fallback.
This notebook embeds its full implementation and the verified GT/evaluation/Excel contract.
No PriceTracker checkout, Node.js or companion Python modules are required at runtime.

Default `RUN_MODE="plan"` performs an offline input audit and sends **no API requests**.
For inference set `RUN_MODE="run"`, then Restart Kernel → Run All.
The key is loaded from `ROUTERAI_API_KEY` in the environment or project `.env`, never printed.
Results: `baseline_results/llm/<EXPERIMENT_ID>/`. The deterministic results are read-only.
''')
    add('code', sources[1], 'install')
    add('code', sources[2], 'definitions')
    add('markdown', '''## Experiment configuration

Keep one model for all future LLM-using pipelines. The exact model ID is pinned below.
An experiment stores immutable request settings and per-page inputs; changing them requires a new ID.
Normal resume reuses all completed responses, including errors, without charging again.
`RETRY_ERRORS=True` explicitly retries failed responses. Interrupted requests with unknown results
require `RETRY_UNCERTAIN=True`: exactly-once billing cannot be guaranteed by the remote API.
The 65,536 output-token budget is an explicit experimental setting, not a value specified in the draft.
''')
    add('code', config, 'configuration')
    add('markdown', '''## Shared dataset and evaluation contract

These definitions are copied from the verified determined notebook. Main metrics retain its name
threshold 0.85, price tolerance ±1%, URL identity including www folding, and reviewed price-absence policy.
Extra GT and Pages columns survive for joins by `(dataset, entity_id)` or `(dataset, page_id)`.
Strict supplementary metrics use normalized exact names and numeric prices on the same URL matches.
See LLM_BENCHMARK.md for differences between the current dataset and the article draft.
''')
    add('code', sources[6], 'definitions')
    add('code', sources[8], 'definitions')
    add('code', adapter, 'definitions')
    add('markdown', '''## Complete-HTML LLM extraction and durable responses

HTML is passed unchanged after UTF-8 decoding, including scripts and embedded state. No GT fields enter
the prompt. The query is provenance, not a filter, because current GT labels all page products.
The response schema has name, price, old_price, product_url, image_url, availability, delivery_time,
reviews_count. Missing attributes are null; optional GT blanks remain unknown rather than negative labels.

No input shortening, chunking, model substitution, JSON repair or extra extraction call is performed.
Context errors, output truncation, invalid JSON and schema errors are retained as observed failures.
Retries apply only to transport failures, 429 and selected 5xx errors; every attempt is recorded.
''')
    add('code', implementations[0], 'definitions')
    add('code', sources[14], 'definitions')
    add('code', exports, 'definitions')
    add('markdown', '''## Paper analytics and comparison

Outputs include the standard CSV/Excel reports, full eight-field predictions, compressed requests and
raw responses, per-attempt usage/status/latency, rejected rows, estimated RUB costs, strict field metrics,
PSR for positive/empty pages, optional-field annotation coverage and exploratory page-bootstrap intervals.
Unknown usage is never reported as zero. Provider cost is saved verbatim with its reported currency;
RUB estimates use the saved RouterAI model-pricing snapshot and are not an invoice.
`baseline_comparison.xlsx` places verified original-parser results and LLM results beside the same GT.
''')
    add('code', implementations[1], 'definitions')
    add('code', implementations[2], 'definitions')
    add('code', 'REPORTING_CODE_SHA256 = ' + repr(hashlib.sha256(reporting.encode()).hexdigest()) + '\n' + reporting, 'definitions')
    add('markdown', '''## Run / resume / replay

Run this cell after reviewing configuration. First use the offline plan; for paid inference set `RUN_MODE="run"`.
Repeating the run with the same experiment ID resumes responses. `RUN_MODE="replay"` rebuilds reports offline.
Inspect `run_status.json` and per-dataset `llm_cost_summary.json`: completion of the run does not imply all
model responses succeeded. Failed requests stay in the evaluation denominator; an unvisited corpus is never
reported as a complete experiment. Close generated Excel workbooks before rebuilding reports.
''')
    add('code', 'llm_result = llm_main()\n', 'main')
    notebook = dict(cells=cells, metadata=original['metadata'], nbformat=4, nbformat_minor=5)
    target = ROOT / 'baseline_llm.ipynb'
    target.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(target)


if __name__ == '__main__':
    build()
