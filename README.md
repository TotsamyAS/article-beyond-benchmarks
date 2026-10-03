# Beyond Synthetic Benchmarks — evaluation artifacts

This repository contains the offline product-extraction benchmarks for the draft
*Beyond Synthetic Benchmarks: A Hybrid Cascading Architecture for Cost-Effective
and Robust Entity Extraction in Industrial E-Commerce Environments*.

The evaluation starts from **saved HTML snapshots** and manually reviewed ground
truth. It measures extraction from a fixed page state; it does not reproduce live
website acquisition, browser navigation or access-control handling.

## Start here

| Reviewer task | Entry point |
|---|---|
| Inspect original-parser predictions beside ground truth | [`baseline_results/determined/industrial/baseline_review.xlsx`](baseline_results/determined/industrial/baseline_review.xlsx), sheet `Сравнение` (Comparison) |
| Reproduce the original deterministic parser | [`baseline_determined.ipynb`](baseline_determined.ipynb) |
| Run or inspect the LLM-only protocol | [`baseline_llm.ipynb`](baseline_llm.ipynb) |
| Understand matching, price absence and entity identifiers | [`BENCHMARK.md`](BENCHMARK.md) |
| Understand LLM prompts, retry accounting and paper/dataset differences | [`LLM_BENCHMARK.md`](LLM_BENCHMARK.md) |
| Check equivalence to the external original implementation | [`baseline_results/determined/original_parity.json`](baseline_results/determined/original_parity.json) and [`scripts/verify_original_baseline.py`](scripts/verify_original_baseline.py) |

Both notebooks contain their executable implementation. The `scripts/` directory
provides command-line entry points, maintenance tools and the sources used to build
the LLM notebook. Extraction does not depend on a local PriceTracker checkout.

## Repository layout

```text
baseline_determined.ipynb   Original deterministic HTML extraction and evaluation
baseline_llm.ipynb          Full-HTML LLM-only extraction and evaluation
README.md                  Reviewer guide (this file)
BENCHMARK.md               Detailed deterministic evaluation contract
LLM_BENCHMARK.md           Detailed LLM protocol and execution instructions
config/                    Python dependencies, model snapshot, empty key template
scripts/                   Launchers, notebook builder and dataset/report tools
tests/                     Offline regression tests
industrial/                Real HTML snapshots and reviewed dind.xlsx ground truth
synthetic/                 Clean and mutated HTML, manifests and ground truth
baseline_results/          Outputs, provenance and original-implementation validation
archive/legacy/            Earlier log reports and product export, outside the benchmark
```

Hidden local files/directories have operational roles: `.env` holds the optional API
key, `.venv/` is the Python environment, `.benchmark_runtime/` is the disposable
original-parser runtime/checkpoint cache, and `.artifacts/` / `.backup/` contain
local working evidence and backups. They are ignored by Git. `.gitignore` remains
at the repository root; ordinary root files are notebooks and documentation.

## Experimental status

| Pipeline | Implementation | Results |
|---|---|---|
| `original_fast` | Frozen original PriceTracker fast-renderer, embedded with dependencies | Available under `baseline_results/determined/` |
| `original_fast_query` | The same extraction followed by its original query-relevance filter | Available under `baseline_results/determined/` |
| `llm_full_html` | `qwen/qwen3.8-omni-flash` through RouterAI, full saved HTML per request | Prepared and tested offline; no paid run was performed during implementation |
| Browser-based and hybrid pipelines | Planned comparison arms in the draft | Not implemented by the two notebooks currently provided |

The original-parser validation matched both methods in the reference
`baseline_results/validation/original/original_comparison.xlsx`: 239 page responses
and 13,111 evaluated result rows. The standalone notebook bundles the original
TypeScript sources, compiled JavaScript, dependencies and fingerprints.

The draft contains provisional dataset sizes, model choices and unfilled claims.
Use the versioned manifests and actual result files as evidence; do not treat
placeholder claims in the draft as measured findings. LLM run completeness is
recorded in its `run_status.json`; `plan/` files are input audits, not model results.

## Data and evaluation scope

| Split | Page records | Available HTML | Positive GT entities |
|---|---:|---:|---:|
| Synthetic v2 | 20 | 20 | 420 |
| Robustness v2 | 200 | 200 | 4,200 |
| Industrial | 240 | 239 | 6,317 |

Industrial ground truth is in [`industrial/dind.xlsx`](industrial/dind.xlsx):
`Pages` defines the corpus and `GroundTruth` contains the annotations. One missing
HTML is retained in the manifest and excluded from scoring. Negative annotations
represent pages with no products. Synthetic and robustness have separate CSV
manifests and labels while sharing `synthetic/html/`; mutation/base identifiers
support paired analysis. Version `synthetic-v2-12to30` has 12–30 products per page;
see [`synthetic/README.md`](synthetic/README.md) for regeneration and versioning.
V1 outputs (100 / 1,000 entities) cannot be compared as the same corpus.
Check each result's manifest/GT fingerprint to identify its dataset version. Run
determined first so the LLM comparison workbook can use predictions against the
same v2 labels. LLM progress and completion are recorded per experiment.

Main evaluation uses page-scoped normalized product URL identity, name similarity
threshold 0.85 and price tolerance ±1%. `www` and the bare domain are equivalent.
In industrial GT, **price 0 means confirmed absence of a price**, not a free product.
Unknown fields are not treated as negative labels. The LLM reports additionally
provide strict field metrics, PSR and optional-field annotation coverage. Those
strict metrics are explicitly separate from the established main metrics.

GT labels products on each saved page. The full-HTML LLM extracts all such products,
without query filtering. `original_fast` is its direct all-products comparator;
`original_fast_query` is reported separately because filtering changes recall against
this GT. The shared offline validation contract is not the full production L9 service.

No GT labels are sent to the model. Additional GT/Pages columns are retained for
classification and clustering joins: `(dataset, entity_id)` identifies a GT product,
and `(dataset, page_id)` identifies a page. Unmatched predictions have no GT ID;
`result_id`, `pred_id` and `benchmark_run_id` identify method/run-specific results.
Across synthetic dataset versions, also join on `dataset_version`; page/entity IDs
are intentionally retained for the original products. Analytics exports preserve
the version, `product_origin` and page size as GT/page metadata columns.

## Reproduce

Run commands from the repository root. The reference environment is Python
3.13.13; the original parser was validated with Node.js 24.6.0. Node.js is required
for the deterministic notebook, but npm and the original repository are not.
The LLM notebook does not require Node.js.

Create/activate a Python environment, then install the pinned dependencies:

```sh
python -m venv .venv
# Activate .venv using the command appropriate to your shell.
python -m pip install -r config/requirements-benchmark.lock.txt
```

Select this environment as the notebook kernel. Open `baseline_determined.ipynb`
and use **Restart Kernel → Run All** to perform fresh original-parser extraction.
Both notebooks currently select only `synthetic` and `robustness` for the v2 rerun.
Set `RUN_DATASETS=None` to run all three splits. Existing outputs are written to
`baseline_results/determined/`; preserve an earlier run separately if needed.

For LLM-only, first run the offline plan (no key or network required):

```sh
python scripts/run_llm_benchmark.py --plan
```

Create a root `.env` if one does not already exist; the template is
[`config/env.example`](config/env.example). Set `ROUTERAI_API_KEY` there or in the
environment. The following command makes **paid API requests**:

```sh
python scripts/run_llm_benchmark.py --run
```

In Windows PowerShell without environment activation, use
`.\.venv\Scripts\python.exe -X utf8 scripts/run_llm_benchmark.py --run`.
The default experiment is `qwen38-omni-flash-synthetic-v2`; old v1 responses stay
in their original directory. Repeat the same command to resume completed responses. An independent repetition
uses a new `--experiment` name. `--replay` rebuilds reports offline. In the notebook,
set `RUN_MODE="run"` before **Run All**; its default `plan` mode sends no requests.

The hosted model need not return identical outputs across fresh runs. Model IDs,
request parameters, timestamps, raw responses, usage, retries and price snapshots
are persisted to make each reported run auditable. See the LLM guide for interrupted
requests with unknown billing and explicitly retrying failures.

## Read the outputs

Deterministic outputs are in `baseline_results/determined/`. LLM outputs are in
`baseline_results/llm/<experiment-id>/`, with one subdirectory per dataset.

- `baseline_review.xlsx`: GT names/prices and method predictions side by side;
  `Лишние` contains unmatched predictions and `Страницы` includes empty/excluded pages.
- `baseline_predictions.csv`: matched products, misses, extras and per-row TP/FP/FN.
- `baseline_metrics.csv`, `baseline_group_metrics.csv`: aggregate and grouped quality.
- `baseline_entity_analytics.csv`: prediction/GT data with classification metadata.
- `html_manifest.csv`, `groundtruth_loaded.csv`, `benchmark_run.json`: exact scope,
  settings and input/output fingerprints.
- LLM `baseline_comparison.xlsx`: verified original-parser and LLM results beside
  the same GT; generated after inference or replay.
- LLM `llm_calls/`, `llm_attempts.csv`, `llm_cost_summary.json`: full requests/responses,
  observed usage and diagnostic timings. Missing usage is not replaced with zero.

RUB cost estimates use the stored RouterAI model-pricing snapshot and are not an
invoice. Timings reflect the current machine/network rather than controlled hardware.
Bootstrap intervals resample pages; they do not measure variability across repeated
LLM runs. Detailed output schemas and interpretation are in the two benchmark guides.

## Verify and maintain

All regression tests run offline; they make no paid requests:

```sh
python -m unittest discover -s tests -t . -v
python scripts/verify_original_baseline.py
```

The second command compares existing results with the reference original Excel;
it does not rerun extraction. Tests of removed historical card-example fixtures
are explicitly skipped; the available corpus and synthetic fixtures are still tested.

[`scripts/README.md`](scripts/README.md) lists the commands and distinguishes normal
evaluation from GT-maintenance and historical replay tools. Do not rebuild or prefill
ground truth to reproduce a benchmark: the reviewed annotations are already present.
After changing LLM implementation sources, regenerate the notebook with
`python scripts/build_llm_notebook.py` and rerun tests.

Historical result metadata may contain absolute paths from the machine on which it
was produced. Those records are retained as provenance. Current entry points resolve
the repository/dataset paths and do not require that machine's directory layout.
