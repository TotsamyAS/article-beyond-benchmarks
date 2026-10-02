"""Restore shuffled synthetic files using generator hashes and embedded page identities.

Dry run by default. --write backs up every input before restoring any destination.
No benchmark extractor or model prediction is used to construct ground truth.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import html
import io
import json
from pathlib import Path
import re
import zipfile

from bs4 import BeautifulSoup
import pandas as pd


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False, lineterminator="\n").encode("utf-8-sig")


def recovery_plan(root: Path) -> tuple[dict[str, bytes], dict, dict[str, bytes]]:
    snapshot = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    variants, manifests, configs, summaries, readmes = [], [], [], [], []
    by_hash = defaultdict(list)
    html_files = {}
    for name, data in snapshot.items():
        by_hash[digest(data)].append(name)
        text = data.decode("utf-8-sig", errors="strict")
        first = text.splitlines()[0] if text.splitlines() else ""
        if re.match(r"\s*(?:<!doctype html|<html)", text, re.I):
            html_files[name] = data
        elif first.startswith("mutation_id,"):
            frame = pd.read_csv(io.StringIO(text))
            if {"base_entity_id", "product_url_gt"} <= set(frame):
                variants.append((name, frame))
            elif {"mutation_axis", "sha256"} <= set(frame):
                manifests.append((name, frame))
        elif text.lstrip().startswith("{"):
            value = json.loads(text)
            if "n_clean_pages" in value and "robustness_axes" in value:
                configs.append((name, value))
            elif "clean_entities" in value and "templates" in value:
                summaries.append((name, value))
        elif text.startswith("# Synthetic E-commerce Benchmark"):
            readmes.append(name)
    for label, matches in [("robustness GT", variants), ("robustness manifest", manifests),
                           ("generator config", configs), ("generator summary", summaries), ("README", readmes)]:
        if len(matches) != 1:
            raise ValueError(f"Expected one {label} identified by content; found {len(matches)}")
    gt_source, gt = variants[0]
    manifest_source, mutation_pages = manifests[0]
    config_source, config = configs[0]
    summary_source, summary = summaries[0]
    if gt.entity_id.duplicated().any() or mutation_pages.mutation_id.duplicated().any():
        raise ValueError("Duplicate generator identities")
    if set(gt.mutation_id) != set(mutation_pages.mutation_id):
        raise ValueError("Generator GT and mutation manifest disagree")
    n_clean = int(config["n_clean_pages"])
    n_per_page = int(config["entities_per_page"])
    n_mutations = int(config["robustness_mutations_per_clean_page"])
    if len(mutation_pages) != n_clean * n_mutations or len(gt) != len(mutation_pages) * n_per_page:
        raise ValueError("Generator counts do not match configuration")
    fields = [c for c in gt if c not in {"mutation_id", "entity_id"}]
    base_labels = gt[fields].drop_duplicates()
    if len(base_labels) != n_clean * n_per_page or base_labels.base_entity_id.duplicated().any():
        raise ValueError("Mutation GT does not preserve one consistent label per base entity")

    plan, mapping = {}, []
    used_hashes = set()
    for row in mutation_pages.itertuples():
        sources = by_hash.get(row.sha256, [])
        if not sources:
            raise ValueError(f"Missing exact HTML payload for {row.mutation_id}: {row.sha256}")
        payload = snapshot[sources[0]]
        soup = BeautifulSoup(payload.decode("utf-8"), "lxml")
        canonical = soup.select_one('link[rel="canonical"]')
        marker = soup.select_one('meta[name="synthetic-mutation"]')
        if canonical is None or canonical.get("href", "").rstrip("/").split("/")[-1] != row.base_page_id:
            raise ValueError(f"Generator hash/canonical disagreement: {row.mutation_id}")
        if marker is None or marker.get("content") != f"{row.mutation_axis}:{int(row.variant_idx)}":
            raise ValueError(f"Generator mutation marker disagreement: {row.mutation_id}")
        target = f"html/{row.mutation_id}.html"
        plan[target] = payload
        mapping.append(dict(source=sources[0], destination=target, sha256=row.sha256, evidence="generator_sha256"))
        used_hashes.add(row.sha256)

    clean = {}
    for source, payload in html_files.items():
        if digest(payload) in used_hashes:
            continue
        soup = BeautifulSoup(payload.decode("utf-8"), "lxml")
        canonical = soup.select_one('link[rel="canonical"]')
        marker = soup.select_one('meta[name="synthetic-mutation"]')
        page_id = canonical.get("href", "").rstrip("/").split("/")[-1] if canonical else ""
        if marker is not None or not re.fullmatch(r"syn-\d{4}", page_id) or page_id in clean:
            raise ValueError(f"Ambiguous/unrecognized clean HTML: {source}")
        clean[page_id] = payload
        target = f"html/{page_id}.html"
        plan[target] = payload
        mapping.append(dict(source=source, destination=target, sha256=digest(payload), evidence="embedded_canonical_page_id"))
    if len(clean) != n_clean or set(clean) != set(gt.base_page_id):
        raise ValueError("Clean HTML identities do not cover the generator GT")
    # Independently confirm every target URL occurs in the hash-identified HTML.
    for row in gt.itertuples():
        payload = html.unescape(plan[f"html/{row.mutation_id}.html"].decode("utf-8")).replace("\\/", "/")
        if row.product_url_gt not in payload:
            raise ValueError(f"GT URL is absent from its exact mutation payload: {row.entity_id}")
    for row in base_labels.itertuples():
        payload = html.unescape(clean[row.base_page_id].decode("utf-8")).replace("\\/", "/")
        if row.product_url_gt not in payload:
            raise ValueError(f"GT URL is absent from its clean payload: {row.base_entity_id}")

    clean_gt = base_labels.rename(columns={"base_page_id": "page_id", "base_entity_id": "entity_id"}).copy()
    clean_gt = clean_gt.sort_values(["page_id", "entity_id"]).reset_index(drop=True)
    clean_gt["base_page_id"] = clean_gt.page_id
    clean_gt["base_entity_id"] = clean_gt.entity_id
    clean_gt["html_file"] = "html/" + clean_gt.page_id + ".html"
    clean_gt["mutation_axis"] = "clean"
    clean_pages = []
    for page_id, payload in sorted(clean.items()):
        group = mutation_pages[mutation_pages.base_page_id.eq(page_id)]
        if group.category.nunique() != 1 or group.base_template.nunique() != 1:
            raise ValueError(f"Inconsistent page metadata: {page_id}")
        clean_pages.append(dict(page_id=page_id, base_page_id=page_id, category=group.category.iloc[0],
            base_template=group.base_template.iloc[0], mutation_axis="clean", variant_idx=0,
            artifact_path=f"html/{page_id}.html", benchmark_html_file=f"html/{page_id}.html",
            n_entities=n_per_page, sha256=digest(payload), source="synthetic-shop.local",
            page_url=f"https://synthetic-shop.local/search/{page_id}"))
    repaired_pages = mutation_pages.copy()
    repaired_pages["page_id"] = repaired_pages.mutation_id
    repaired_pages["benchmark_html_file"] = "html/" + repaired_pages.mutation_id + ".html"
    repaired_pages["source"] = "synthetic-shop.local"
    repaired_pages["page_url"] = "https://synthetic-shop.local/search/" + repaired_pages.base_page_id
    plan["synthetic_ground_truth.csv"] = csv_bytes(clean_gt)
    plan["synthetic_pages.csv"] = csv_bytes(pd.DataFrame(clean_pages))
    plan["robustness_ground_truth.csv"] = snapshot[gt_source]  # original labels, byte-for-byte
    plan["robustness_pages.csv"] = csv_bytes(repaired_pages)
    plan["config.json"] = snapshot[config_source]
    plan["dataset_summary.json"] = snapshot[summary_source]
    readme = snapshot[readmes[0]].decode("utf-8-sig")
    if "## Restored file mapping" not in readme:
        readme += "\n## Restored file mapping\n\nAll 220 HTML payloads live under `html/`. Clean pages use `synthetic_pages.csv`\nand `synthetic_ground_truth.csv`; mutations use `robustness_pages.csv` and\n`robustness_ground_truth.csv`. The benchmark reports these separately.\n\nMisassigned filenames were recovered with all 200 original generator SHA-256\nhashes and the 20 embedded clean page identities. Original robustness GT was\npreserved; clean GT was recovered from its identical per-base-entity labels\nacross mutations. No extractor predictions were used.\n"
    plan["README.md"] = readme.encode("utf-8")
    report = dict(clean_pages=len(clean), clean_entities=len(clean_gt), robustness_pages=len(mutation_pages),
                  robustness_entities=len(gt), exact_mutation_hash_matches=len(used_hashes),
                  gt_urls_verified=len(gt) + len(clean_gt),
                  original_robustness_gt_sha256=digest(snapshot[gt_source]),
                  original_manifest_sha256=digest(snapshot[manifest_source]),
                  changed_files=[name for name, data in plan.items() if snapshot.get(name) != data], mapping=mapping)
    return plan, report, snapshot


def restore(root: Path, write: bool = False) -> dict:
    root = root.resolve()
    plan, report, snapshot = recovery_plan(root)
    if write and report["changed_files"]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        evidence_dir = Path(__file__).resolve().parents[1] / ".artifacts" / f"synthetic-repair-{timestamp}"
        evidence_dir.mkdir(parents=True)
        backup = evidence_dir / "synthetic-before-repair.zip"
        with zipfile.ZipFile(backup, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in snapshot.items():
                archive.writestr(name, data)
        with zipfile.ZipFile(backup) as archive:
            for name, data in snapshot.items():
                if archive.read(name) != data:
                    raise RuntimeError(f"Backup verification failed: {name}")
        for name, data in plan.items():
            destination = (root / name).resolve()
            if not destination.is_relative_to(root):
                raise ValueError(f"Unsafe destination: {destination}")
            if snapshot.get(name) == data:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".repair-tmp")
            temporary.write_bytes(data)
            temporary.replace(destination)
        for name, data in plan.items():
            if (root / name).read_bytes() != data:
                raise RuntimeError(f"Restored content mismatch: {name}")
        report["backup"] = str(backup)
        report["report"] = str(evidence_dir / "recovery_report.json")
        Path(report["report"]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "synthetic")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = restore(args.root, args.write)
    print(json.dumps({k: len(v) if k in {"mapping", "changed_files"} else v for k, v in result.items()}, ensure_ascii=False, indent=2))
