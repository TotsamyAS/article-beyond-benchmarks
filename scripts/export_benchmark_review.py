"""Regenerate review workbooks from saved benchmark CSVs without extraction."""
import hashlib
import json
from pathlib import Path

from validate_original_mechanism import load_notebook

ROOT = Path(__file__).resolve().parents[1]


def main():
    ns = load_notebook()
    pd = ns["pd"]
    out = ROOT / "baseline_results/determined"
    for name in ns["DATASETS"]:
        folder = out / name
        if not (folder / "baseline_predictions.csv").is_file():
            continue
        metadata = json.loads((folder / "benchmark_run.json").read_text(encoding="utf-8"))
        for key in ["predictions", "metrics", "page_results", "manifest"]:
            item = metadata["files"][key]
            assert hashlib.sha256((folder / item["file"]).read_bytes()).hexdigest() == item["sha256"]
        ns["export_review_workbook"](
            folder / "baseline_review.xlsx", pd.read_csv(folder / "baseline_predictions.csv"),
            pd.read_csv(folder / "baseline_metrics.csv"), pd.read_csv(folder / "baseline_page_results.csv"),
            pd.read_csv(folder / "html_manifest.csv"), provenance=metadata)
        print("Excel ready:", folder / "baseline_review.xlsx", flush=True)
    ns["export_review_workbook"](
        out / "baseline_review_all.xlsx", pd.read_csv(out / "baseline_predictions_all.csv"),
        pd.read_csv(out / "baseline_metrics_all.csv"), pd.read_csv(out / "baseline_page_results_all.csv"),
        provenance=json.loads((out / "baseline_combined_manifest.json").read_text(encoding="utf-8")))
    print("Excel ready:", out / "baseline_review_all.xlsx", flush=True)


if __name__ == "__main__":
    main()
