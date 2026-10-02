"""Compare saved baselines with an offline replay of the original PriceTracker.

The original repository is read-only. No HTTP, browser or LLM calls are made.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_notebook():
    notebook = json.loads((ROOT / "baseline_determined.ipynb").read_text(encoding="utf-8"))
    ns = {}
    with contextlib.redirect_stdout(io.StringIO()):
        for index in [2, 4, 6, 8, 10, 12, 14, 16, 18]:
            exec(compile("".join(notebook["cells"][index]["source"]), f"notebook_cell_{index}", "exec"), ns)
    return ns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path("D:/Job_Codes/PriceTracker"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--extract-only", action="store_true")
    mode.add_argument("--report-only", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Reuse completed page checkpoints after interruption")
    args = parser.parse_args()
    ns = load_notebook()
    pd = ns["pd"]
    out = ROOT / "baseline_results/validation/original"
    out.mkdir(parents=True, exist_ok=True)
    manifest = pd.read_csv(ROOT / "baseline_results/determined/industrial/html_manifest.csv").fillna("")
    reference = ROOT / "industrial/dind.xlsx"
    baseline_folder = ROOT / "baseline_results/determined/industrial"
    baseline_provenance = json.loads((baseline_folder / "benchmark_run.json").read_text(encoding="utf-8"))
    if not {"b1_jsonld", "b2_css"}.issubset(baseline_provenance.get("variants", [])):
        raise RuntimeError("This historical comparison script requires B1/B2 outputs. The notebook now runs the embedded original parser; use verify_original_baseline.py to compare it with the preserved reference Excel.")
    assert baseline_provenance["reference_sha256"] == hashlib.sha256(reference.read_bytes()).hexdigest(), "Saved baseline uses different GT"
    for key in ["predictions", "metrics", "manifest", "page_results"]:
        item = baseline_provenance["files"][key]
        assert hashlib.sha256((baseline_folder / item["file"]).read_bytes()).hexdigest() == item["sha256"], "Saved baseline changed"
    gt = ns["align_groundtruth_to_manifest"](
        ns["read_groundtruth"](reference, zero_price_is_missing=True), manifest)
    source_paths = list((args.repository / "services/fast-renderer/src").glob("*.ts"))
    source_paths += [args.repository / "core" / name for name in [
        "sources/search_engine.py", "extraction/custom.py", "extraction/product_card_fields.py"]]
    fingerprints = {str(p.relative_to(args.repository)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in source_paths}
    provenance_path = out / "original_source.json"
    raw_path = out / "original_pages.jsonl"
    if not args.report_only:
        pages = manifest[manifest.skip_reason.eq("")].to_dict("records")
        for page in pages:
            page["html_path"] = str(ROOT / "industrial/html" / page["html_key"])
            assert hashlib.sha256(Path(page["html_path"]).read_bytes()).hexdigest() == page["content_sha256"]
        input_path = out / "replay_inputs.json"
        input_text = json.dumps(pages, ensure_ascii=False)
        if args.resume:
            previous = json.loads(provenance_path.read_text(encoding="utf-8"))
            assert previous["source_sha256"] == fingerprints, "Cannot resume: original source changed"
            assert previous["reference_sha256"] == hashlib.sha256(reference.read_bytes()).hexdigest(), "Cannot resume: GT changed"
            assert json.loads(input_path.read_text(encoding="utf-8")) == pages, "Cannot resume: inputs changed"
        else:
            input_path.write_text(input_text, encoding="utf-8")
        provenance = {
            "repository": str(args.repository), "source_sha256": fingerprints,
            "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
            "replay_inputs_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "scope": "offline saved HTML; built-in supplier profiles; no persisted source selectors; no browser or LLM",
            "no_query_variant": "original_fast: query omitted to evaluate all GT products on each saved page",
            "query_variant": "original_fast_query: the original query filter applied to the same extracted rows",
            "limit": 500,
        }
        provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
        subprocess.run(["node", str(args.repository / "services/fast-renderer/node_modules/tsx/dist/cli.mjs"),
                        str(ROOT / "scripts/validate_original_fast_renderer.mts"), str(args.repository),
                        str(input_path), str(raw_path), *(["--resume"] if args.resume else [])], check=True, timeout=3600)
    if args.extract_only:
        return
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert provenance["source_sha256"] == fingerprints, "Original source changed since extraction"
    assert provenance["reference_sha256"] == hashlib.sha256(reference.read_bytes()).hexdigest(), "GT changed"
    records = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == len(manifest[manifest.skip_reason.eq("")])
    assert len({r["page_id"] for r in records}) == len(records)
    assert {r["page_id"] for r in records} == set(manifest.loc[manifest.skip_reason.eq(""), "page_id"])
    lookup = manifest.set_index("page_id").to_dict("index")
    raw, logs = [], []
    for record in records:
        page = lookup[record["page_id"]]
        assert hashlib.sha256((ROOT / "industrial/html" / page["html_key"]).read_bytes()).hexdigest() == page["content_sha256"]
        for variant, rows in [
            ("original_fast", record.get("result", {}).get("rows", [])),
            ("original_fast_query", record.get("queried", {}).get("rows", [])),
        ]:
            candidates = [dict(product_url=r["product_url"], pred_name=r["name"], pred_price=r["price"],
                               pred_in_stock=r.get("in_stock"), extract_notes=record["result"]["route"]) for r in rows]
            for candidate in ns["deduplicate_predictions"](candidates):
                raw.append(dict({k: page[k] for k in ["dataset", "html_file", "html_key", "run_id", "source"]},
                                page_id=record["page_id"], variant=variant, **candidate))
            logs.append(dict(page_id=record["page_id"], source=page["source"], variant=variant,
                             replay_status=record["status"], status=record.get("result", {}).get("status"),
                             count=len(rows), elapsed_ms=record["elapsed_ms"], error=record.get("error", "")))
    raw = pd.DataFrame(raw)
    variants = ("original_fast", "original_fast_query")
    evaluation, metrics = ns["evaluate_predictions"](raw, gt, manifest, variants)
    raw.to_csv(out / "original_raw_predictions.csv", index=False, encoding="utf-8-sig")
    evaluation.to_csv(out / "original_predictions.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(logs).to_csv(out / "original_extraction_log.csv", index=False, encoding="utf-8-sig")
    baseline = pd.read_csv(ROOT / "baseline_results/determined/industrial/baseline_predictions.csv")
    baseline_metrics = pd.read_csv(ROOT / "baseline_results/determined/industrial/baseline_metrics.csv")
    comparison = pd.concat([baseline, evaluation], ignore_index=True)
    all_metrics = pd.concat([baseline_metrics, metrics], ignore_index=True)
    comparison.to_csv(out / "comparison_predictions.csv", index=False, encoding="utf-8-sig")
    all_metrics.to_csv(out / "comparison_metrics.csv", index=False, encoding="utf-8-sig")
    pages = ns["build_page_analytics"](evaluation, gt, manifest, raw, variants)
    groups = ns["build_group_metrics"](ns["build_entity_analytics"](evaluation, gt, manifest), pages)
    groups.to_csv(out / "original_group_metrics.csv", index=False, encoding="utf-8-sig")
    pages.to_csv(out / "original_page_results.csv", index=False, encoding="utf-8-sig")
    all_pages = pd.concat([pd.read_csv(baseline_folder / "baseline_page_results.csv"), pages], ignore_index=True)
    all_pages.to_csv(out / "comparison_page_results.csv", index=False, encoding="utf-8-sig")
    if "export_review_workbook" in ns:
        print("Writing comparison Excel...", flush=True)
        ns["export_review_workbook"](out / "original_comparison.xlsx", comparison, all_metrics, all_pages,
                                     manifest, provenance=provenance)
    conditional = []
    for variant, part in comparison.groupby("variant", sort=False):
        matched = part[part.match_status.eq("matched_gt")]
        conditional.append(dict(variant=variant, matched=len(matched), correct_names=int(matched.name_TP.sum()),
                                name_accuracy_on_matched=float(matched.name_TP.mean()) if len(matched) else None))
    summary = all_metrics.merge(pd.DataFrame(conditional), on="variant", validate="one_to_one")
    summary.to_csv(out / "comparison_summary.csv", index=False, encoding="utf-8-sig")
    b2 = comparison[comparison.variant.eq("b2_css") & comparison.entity_id.notna()]
    original = comparison[comparison.variant.eq("original_fast") & comparison.entity_id.notna()]
    paired = b2.merge(original, on=["dataset", "entity_id"], suffixes=("_b2", "_original"), validate="one_to_one")
    paired.to_csv(out / "paired_b2_original.csv", index=False, encoding="utf-8-sig")
    validation = {
        "available_pages": len(records), "listed_pages": len(manifest),
        "replay_errors": [r["html_key"] for r in records if r["status"] != "ok"],
        "baseline_run_id": baseline_provenance["benchmark_run_id"],
        "validation_run_id": ns["BENCHMARK_RUN_ID"],
        "original_source_sha256": fingerprints,
        "reference_sha256": provenance["reference_sha256"],
        "raw_response_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "scope": provenance["scope"],
        "original_statuses": pd.Series([r.get("result", {}).get("status", "error") for r in records]).value_counts().to_dict(),
        "summary": json.loads(summary.to_json(orient="records")),
        "paired_entities": len(paired),
        "name_fixed_by_original": int((paired.name_TP_b2.eq(0) & paired.name_TP_original.eq(1)).sum()),
        "name_lost_by_original": int((paired.name_TP_b2.eq(1) & paired.name_TP_original.eq(0)).sum()),
    }
    (out / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    print(all_metrics[["variant", "entity_F1", "name_F1", "price_F1"]].to_string(index=False))
    print("Replay statuses:", pd.DataFrame(logs).replay_status.value_counts().to_dict())


if __name__ == "__main__":
    main()
