# Command-line and maintenance tools

Run these commands from the repository root with the benchmark Python environment.
The two root notebooks are the primary experiment entry points.

| File | Purpose | Typical invocation |
|---|---|---|
| `run_llm_benchmark.py` | Execute the actual LLM notebook; plan is the default | `python scripts/run_llm_benchmark.py --plan` |
| `run_determined_benchmark.py` | Execute the actual original-parser notebook | `python scripts/run_determined_benchmark.py --datasets synthetic robustness` |
| `generate_synthetic_dataset.py` | Verify or regenerate synthetic v2 and all mutations from frozen inputs | `python scripts/generate_synthetic_dataset.py` (`--write` regenerates) |
| `verify_original_baseline.py` | Check saved original-parser results against reference Excel | `python scripts/verify_original_baseline.py` |
| `export_benchmark_review.py` | Rebuild determined Excel reports from saved CSVs, without extraction | `python scripts/export_benchmark_review.py` |
| `build_llm_notebook.py` | Embed LLM sources and shared evaluation cells in the LLM notebook | `python scripts/build_llm_notebook.py` |
| `llm_baseline_runtime.py` | LLM calls, schema handling, saved responses and usage accounting | Embedded by the builder; not a standalone CLI |
| `llm_baseline_analysis.py` | Strict supplementary metrics, costs and comparison | Embedded by the builder |
| `llm_baseline_main.py` | Corpus audit and experiment orchestration | Embedded by the builder |
| `benchmark_reporting.py` | Reporting performance adapter and stage/heartbeat logs; embedded in both notebooks | No standalone run; preserves frozen cache/scoring contract |
| `build_dind_from_artifacts.py` | Build an annotation candidate corpus from local collection artifacts | `python scripts/build_dind_from_artifacts.py --help` |
| `prefill_industrial_gt.py` | Historical assisted annotation; `--write` changes the workbook after backup | `python scripts/prefill_industrial_gt.py --help` |
| `repair_synthetic_dataset.py` | Verify/restore the historical shuffled synthetic dataset | `python scripts/repair_synthetic_dataset.py` (dry run) |
| `parse_logs_to_excel.py` | Legacy event-log report | `python scripts/parse_logs_to_excel.py --help` |
| `parse_logs_to_excel_v2.py` | Legacy products/logs report and optional evaluation-contract parity helper | `python scripts/parse_logs_to_excel_v2.py --help` |
| `validate_original_mechanism.py` + `.mts` worker | Historical comparison using an external PriceTracker checkout and its TypeScript tools | Only for reconstructing the earlier comparison; not needed to run either notebook |

Use the checked-in GT and HTML for evaluation. Dataset creation/prefill/repair tools
are for maintenance, not prerequisites for reproducing results. The old full replay
expects historical B1/B2 control outputs; use `verify_original_baseline.py` for the
current embedded original-parser baseline.

Python dependency files and the model catalog snapshot are under `config/`.
Legacy log report defaults go to `.artifacts/log-reports/` so they do not clutter the root.
