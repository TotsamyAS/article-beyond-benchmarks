#!/usr/bin/env python3
"""Build a reproducible D_ind candidate pool from locally cached HTML artifacts.

The script NEVER performs network requests. It scans one or more local artifact
roots, classifies HTML snapshots, excludes anti-bot/derived artifacts, removes
exact duplicates, copies the selected HTML into ``<out-dir>/html/``, and writes
one Excel workbook for manual annotation and later benchmark runs.

The script intentionally DOES NOT pre-extract product fields for Ground Truth.
Names, prices, URLs, stock, rating, delivery time, and region remain blank until
manual annotation. This avoids contaminating Ground Truth with the production
extractor or with heuristic guesses.

Primary outputs
---------------
- dind.xlsx
    * GroundTruth   manual annotation sheet (product fields blank by design)
    * Pages         selected, deduplicated benchmark pages
    * Excluded      blocked/derived/duplicate/etc. pages with reasons
    * AllArtifacts  every discovered HTML + diagnostics
    * Summary       dataset statistics and fingerprint
- html/*.html       copied selected HTML with stable benchmark filenames
- dind_summary.json machine-readable statistics + deterministic fingerprint

Copied HTML naming
------------------
With run_id:
    <domain>-<run_id>.html
    e.g. dns-shop.ru-run-a31f4c.html
If more than one selected page has the same domain+run_id, deterministic
suffixes are added: ``-2``, ``-3``, ...

Without run_id:
    <domain>-run-none1.html
    <domain>-run-none2.html
    ...
The ``none`` counter is maintained per domain and is deterministic.

Typical use (from PriceTracker repository root):

    python build_dind_from_artifacts_excel.py \
        .artifacts/collection \
        services/stealth-renderer/.artifacts \
        --out-dir .artifacts/dind

Design notes
------------
* Anti-bot classification reuses ``core.extraction.block_page_detection`` when
  the PriceTracker source tree is importable. Otherwise a source-compatible
  fallback detector is used.
* HTML size is an auxiliary signal only. A small page is NOT automatically a
  block page; it is excluded for size only when it is very small and contains
  neither product evidence nor a legitimate empty-result marker.
* Generated ``<source> collection artifact`` summaries are excluded because
  they are derived from pipeline outputs and would create target leakage.
* Legitimate empty-search pages are retained by default as negative examples.
* Exact duplicates remain visible in the Excel audit sheets but only one
  representative enters the selected benchmark set.
* Existing ``<out-dir>/html`` is rebuilt on every run so stale benchmark pages
  cannot silently remain in the frozen set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "openpyxl is required to write dind.xlsx. Install it with: pip install openpyxl"
    ) from exc

RUN_RE = re.compile(r"^run-[0-9a-f]+$", re.IGNORECASE)

# Copied from the application's current block-page contract as a standalone
# fallback. If the project package is importable, its implementation is used.
BLOCK_PAGE_MARKERS = (
    "xpvnsulc", "captcha", "hcaptcha", "geetest", "captcha-root",
    "captcha_root", "captcha-holder", "sp_rotated_captcha", "firewall",
    "firewallcaptcha", "challenge", "access denied", "403 error",
    "http 403", "403", "status code 403", "forbidden", "guru meditation",
    "verify you are human", "you are not a robot", "/__qrator/qauth.js",
    'content="no-referrer"', "доступ ограничен", "в доступе отказано",
    "доступ к ресурсу ограничен", "доступ к сайту", "запрещен",
    "запрещено", "проблемы с ip", "проблема с ip", "отключите vpn",
    "отключить vpn", "выключите vpn", "выключить vpn",
)

POSITIVE_RESULT_MARKERS = (
    "product-card", "product-title", "v-product-price__value",
    "scenario-availability-item", "x-catalog-items-count", "search results for",
    "вы искали", "product-item-big-card", "product-item-container",
    'data-entity="item"', "catalog-section bx-blue", "order-button",
    "data-productname", 'class="price"', 'class="more"', "руб.", "с ндс",
    "digi-product", "digi-product__price", "digi-product__label",
    'itemtype="https://schema.org/product"', 'itemtype="http://schema.org/product"',
    '"@type":"product"', '"@type": "product"', "petrovich.ru/product/",
    'href="/product/', "data-product-id", "data-offer-id", "data-sku",
    "в корзину", "empty-products__header", "ничего не найдено",
    "товары не найдены", "товаров не найдено",
    "нет товаров, которые соответствуют критериям поиска",
    "нет товаров, соответствующих критериям поиска",
    "нет товаров, которые соответствуют запросу",
    "нет товаров, соответствующих запросу", 'class="empty-products',
    "/no-search-results", "catalog-block-view__item", "item_block",
    "js-notice-block", "js_price_wrapper", "price_matrix_wrapper",
    'itemprop="itemlistelement"', 'itemprop="offers"',
    'class="btn-exlg to-cart',
)

# Product evidence from fast-renderer's htmlHasProductEvidence().  This is
# intentionally distinct from POSITIVE_RESULT_MARKERS: the Python block detector
# treats legitimate empty-result markers as "positive evidence" only to avoid
# misclassifying them as anti-bot pages, while they are not product evidence.
PRODUCT_EVIDENCE_MARKERS = (
    "digi-product", "digi-product__price", "mvid-product-card", "current-price",
    "itemlist", "with-hover", "price-main", "product-item-container",
    "product-item-big-card", "product-list-item", "search-results__item",
    "js-product", "data-retail-price", "data-description", 'data-entity="item"',
    'data-testid="product', "data-testid='product", 'data-test="product',
    'data-zone-name="productsnippet"', 'data-baobab-name="productsnippet"',
    "price_value", "js-productlistitem", "listitembuy__price",
    "product-card-list", "product-card", "product-title",
    "v-product-price__value", "catalog-product", "catalog-block-view__item",
    "item_block", 'data-meta-name="snippetproductverticallayout"',
    'data-id="product"', "tile-root", "tile-clickable-element",
    'href="/product/', "href='/product/", "searchresultsv2",
    "tsheadline500medium", "tsbody500medium", 'data-qa="product-name"',
    "data-qa='product-name'", 'data-qa="product-price-current"',
    "data-qa='product-price-current'", 'data-qa="product-add-to-cart-button"',
    "data-qa='product-add-to-cart-button'", 'data-qa="product-photo-click"',
    "data-qa='product-photo-click'", 'data-qa="product-availability"',
    "data-qa='product-availability'", "catalog__list-item snippet",
    "promo-slider__item snippet", "snippet__price", "snippet-price__value",
    "snippet__title", "snippet__photo", "/product-", "itemlistelement",
    '"@type":"offer"', '"pricecurrency":"rub"',
    'itemtype="https://schema.org/product"',
    'itemtype="http://schema.org/product"', '"@type":"product"',
    '"@type": "product"', "data-product-id", "data-offer-id", "data-sku",
)

EMPTY_PATTERNS = (
    ("ru_no_products_matching_search", re.compile(r"нет\s+товаров[^.]{0,180}(?:поиск|запрос|критери)", re.I)),
    ("ru_products_not_found", re.compile(r"(?:товары?|результаты?|предложения?)\s+не\s+найден", re.I)),
    ("ru_nothing_found", re.compile(r"ничего\s+не\s+найдено", re.I)),
    ("en_no_products_found", re.compile(r"no\s+(?:products?|items?|results?)\s+(?:found|available|match)", re.I)),
)

HARD_BLOCK_MARKERS = tuple(
    x for x in BLOCK_PAGE_MARKERS if x not in {"403", 'content="no-referrer"'}
)
BLOCK_STATUS_RE = re.compile(
    r"(?:\bhttp\s*403\b|\bstatus\s+code\s+403\b|\b403\s+forbidden\b|\baccess\s+denied\b)",
    re.I,
)

DERIVED_MARKERS = (
    "collection artifact</title>",
    "collection artifact</h1>",
    "<strong>collected products:</strong>",
    "<tr><th>product</th><th>price</th><th>delivery</th><th>url</th></tr>",
)

TRACKING_Q_RE = re.compile(r"(?:[?&](?:utm_[^=&]+|yclid|gclid|fbclid|_openstat)=)", re.I)


def clean_text(html: str) -> str:
    """Very small visible-text approximation used only for diagnostics."""
    x = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", " ", html, flags=re.I)
    x = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", " ", x, flags=re.I)
    x = re.sub(r"<[^>]+>", " ", x)
    return re.sub(r"\s+", " ", x).strip()


def normalize_source(s: str | None) -> str | None:
    if not s:
        return None
    s = str(s).strip().strip("'").strip('"').lower()
    m = re.match(r"^https?://(?:www\.)?([^/]+)", s)
    if m:
        s = m.group(1)
    if s.startswith("www."):
        s = s[4:]
    return s or None


def host_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return normalize_source(urlparse(url).hostname)
    except Exception:
        return None


def parse_kv_file(path: Path | None) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    out: dict[str, str] = {}
    try:
        for raw in path.read_text("utf-8", errors="replace").splitlines():
            if "=" not in raw:
                continue
            k, v = raw.split("=", 1)
            k = k.strip()
            if k:
                out[k] = v.strip()
    except OSError:
        pass
    return out


def parse_json_file(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text("utf-8", errors="replace"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def html_has_product_evidence(html: str) -> bool:
    low = html.casefold()
    return any(m.casefold() in low for m in PRODUCT_EVIDENCE_MARKERS)


def detect_empty_result(html: str) -> str | None:
    # Keep product evidence as the higher-priority interpretation, like the app.
    if html_has_product_evidence(html):
        return None
    visible = clean_text(html)
    for name, rx in EMPTY_PATTERNS:
        if rx.search(visible):
            return name
    return None


def looks_like_mvideo_results_page(*, html: str, page_url: str = "") -> bool:
    if not html:
        return False
    lowered_url = page_url.casefold()
    lowered = html.casefold()
    is_mvideo = "mvideo.ru" in lowered_url or "<mvid-root" in lowered
    if not is_mvideo:
        return False
    product_card_count = len(re.findall(
        r"<a\b[^>]*\bmvid-product-card\b[^>]*\bhref=[\"']/products/", lowered
    ))
    has_listing_shell = (
        "<mvid-search" in lowered and "<mvid-listing" in lowered and "products-list" in lowered
    )
    has_search_count = "listing-page-title__count" in lowered and "найдено товаров" in lowered
    has_card_fields = (
        "current-price" in lowered and 'class="name"' in lowered and 'href="/products/' in lowered
    )
    return (
        product_card_count >= 3 and has_listing_shell and has_card_fields
        or product_card_count >= 1 and has_search_count and has_card_fields
    )


def fallback_looks_like_block_page(*, html: str, page_url: str = "", max_html_len: int = 20000) -> bool:
    page_url_cf = page_url.casefold()
    if "/xpvnsulc" in page_url_cf:
        return True
    if looks_like_mvideo_results_page(html=html, page_url=page_url):
        return False
    combined = f"{page_url_cf}\n{html[:max_html_len]}".casefold()
    compact = re.sub(r"\s+", "", combined)
    if any(token in page_url_cf for token in ("captcha", "blocked", "forbidden")):
        return True
    if any(marker in combined for marker in POSITIVE_RESULT_MARKERS):
        return False
    if "<body></body>" in compact and 'content="no-referrer"' in combined:
        return True
    if BLOCK_STATUS_RE.search(combined):
        return True
    return any(marker in combined for marker in HARD_BLOCK_MARKERS)


def find_project_root(explicit: Path | None, roots: list[Path]) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    candidates += [Path.cwd()]
    for root in roots:
        candidates.extend([root, *root.parents])
    seen: set[Path] = set()
    for c in candidates:
        try:
            c = c.resolve()
        except OSError:
            continue
        if c in seen:
            continue
        seen.add(c)
        if (c / "core" / "extraction" / "block_page_detection.py").exists():
            return c
    return None


def load_block_detector(project_root: Path | None) -> tuple[Callable[..., bool], str]:
    if project_root:
        sys.path.insert(0, str(project_root))
        try:
            from core.extraction.block_page_detection import looks_like_block_page  # type: ignore
            return looks_like_block_page, "application_import"
        except Exception as exc:
            print(f"[warn] Could not import application block detector: {exc}", file=sys.stderr)
    return fallback_looks_like_block_page, "standalone_source_compatible_fallback"


def is_derived_collection_summary(html: str) -> bool:
    low = html.casefold()
    hits = sum(m in low for m in DERIVED_MARKERS)
    return hits >= 2


def infer_run_id(path: Path, debug: dict[str, str]) -> str | None:
    if debug.get("run_id"):
        return debug["run_id"].strip() or None
    for part in reversed(path.parts):
        if RUN_RE.match(part):
            return part
    return None


def infer_provenance(path: Path, metadata: dict[str, Any], debug: dict[str, str]) -> str:
    low_parts = "/".join(p.casefold() for p in path.parts)
    if path.name.casefold() == "page.html" and (
        "metadata.json" in {p.name.casefold() for p in path.parent.iterdir() if p.is_file()}
        or "stealth" in low_parts
        or "-sr-" in low_parts
    ):
        return "stealth_renderer"
    if any(RUN_RE.match(p) for p in path.parts):
        return "collection_run"
    if "source-html" in low_parts or path.name.casefold().startswith("latest_"):
        return "latest_source_html"
    if metadata:
        return "renderer_artifact"
    if debug:
        return "debug_paired_html"
    return "unknown_local_html"


def sibling_debug_path(html_path: Path) -> Path | None:
    # collection convention: source.html -> source-debug.log
    candidate = html_path.with_name(f"{html_path.stem}-debug.log")
    if candidate.exists():
        return candidate
    # generic nearby log with the exact stem is preferred; no broad guesses.
    return None


def sibling_metadata_path(html_path: Path) -> Path | None:
    p = html_path.parent / "metadata.json"
    return p if p.exists() else None


def safe_rel(path: Path, roots: list[Path]) -> str:
    rp = path.resolve()
    for root in roots:
        try:
            return str(rp.relative_to(root.resolve()))
        except ValueError:
            continue
    return str(rp)


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_page_id(source: str | None, sha: str) -> str:
    prefix = re.sub(r"[^a-z0-9.-]+", "-", (source or "unknown").casefold()).strip("-")[:40]
    return f"dind-{prefix}-{sha[:16]}"


@dataclass
class ArtifactRow:
    artifact_path: str
    abs_path: str
    provenance: str
    run_id: str | None
    source: str | None
    query: str | None
    location: str | None
    route: str | None
    source_url: str | None
    page_url: str | None
    captured_at: str | None
    size_bytes: int
    char_len: int
    sha256: str
    title: str | None
    metadata_is_blocked: bool | None
    app_blocked: bool
    block_detector: str
    blocked_markers: str
    product_evidence: bool
    empty_result_reason: str | None
    derived_collection_summary: bool
    small_html: bool
    tracking_url_present: bool
    page_kind: str
    include: bool
    exclusion_reason: str | None
    exact_duplicate: bool = False
    duplicate_of_page_id: str | None = None
    page_id: str | None = None
    benchmark_html_file: str | None = None
    development_overlap_risk: bool = False


def extract_title(html: str) -> str | None:
    m = re.search(r"<title\b[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    if not m:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()[:500] or None


def metadata_blocked_markers(metadata: dict[str, Any]) -> list[str]:
    raw = metadata.get("blockedMarkers")
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def classify_one(
    html_path: Path,
    *,
    roots: list[Path],
    block_detector: Callable[..., bool],
    block_detector_name: str,
    small_threshold: int,
    tiny_threshold: int,
    keep_no_product_evidence: bool,
) -> ArtifactRow:
    data = html_path.read_bytes()
    html = data.decode("utf-8", errors="replace")
    debug_path = sibling_debug_path(html_path)
    meta_path = sibling_metadata_path(html_path)
    debug = parse_kv_file(debug_path)
    metadata = parse_json_file(meta_path)

    source_url = debug.get("source_url") or metadata.get("url")
    page_url = debug.get("page_url") or metadata.get("finalUrl") or source_url
    source = normalize_source(debug.get("source_name")) or host_from_url(page_url) or host_from_url(source_url)
    if not source:
        stem = html_path.stem
        source = None if stem.casefold() == "page" else normalize_source(stem)

    run_id = infer_run_id(html_path, debug)
    provenance = infer_provenance(html_path, metadata, debug)
    title = extract_title(html)
    size_bytes = len(data)
    char_len = len(html)
    sha = file_sha256(data)
    derived = is_derived_collection_summary(html)
    product_evidence = html_has_product_evidence(html)
    empty_reason = detect_empty_result(html)

    meta_is_blocked: bool | None = None
    if "isBlocked" in metadata:
        meta_is_blocked = bool(metadata.get("isBlocked"))

    app_blocked = bool(block_detector(html=html, page_url=str(page_url or ""), max_html_len=80_000))
    markers = metadata_blocked_markers(metadata)
    if app_blocked and not markers:
        # Diagnostics only; authoritative decision remains app_blocked.
        low = f"{page_url or ''}\n{html[:80_000]}".casefold()
        markers = sorted({m for m in HARD_BLOCK_MARKERS if m in low})[:20]
        if BLOCK_STATUS_RE.search(low):
            markers.append("status_or_access_denied")

    small_html = size_bytes < small_threshold
    tiny_html = size_bytes < tiny_threshold
    tracking_present = bool(TRACKING_Q_RE.search(str(page_url or source_url or "")))

    if derived:
        include = False
        exclusion = "derived_collection_summary"
        page_kind = "derived"
    elif meta_is_blocked is True or app_blocked:
        include = False
        exclusion = "blocked"
        page_kind = "blocked"
    elif empty_reason:
        include = True
        exclusion = None
        page_kind = "empty_result"
    elif product_evidence:
        include = True
        exclusion = None
        page_kind = "product_page_or_listing"
    elif tiny_html:
        include = False
        exclusion = "too_small_without_product_evidence"
        page_kind = "unknown_small"
    elif keep_no_product_evidence:
        include = True
        exclusion = None
        page_kind = "unknown_nonblocked"
    else:
        include = False
        exclusion = "no_product_evidence"
        page_kind = "unknown_nonblocked"

    captured_at = None
    if metadata.get("timestamp"):
        captured_at = str(metadata.get("timestamp"))
    else:
        try:
            captured_at = datetime.fromtimestamp(html_path.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            pass

    # Historical stealth artifacts are valuable, but may have been seen during
    # development. Mark that risk explicitly instead of deleting the evidence.
    development_overlap_risk = provenance == "stealth_renderer"

    return ArtifactRow(
        artifact_path=safe_rel(html_path, roots),
        abs_path=str(html_path.resolve()),
        provenance=provenance,
        run_id=run_id,
        source=source,
        query=debug.get("query") or None,
        location=debug.get("location") or None,
        route=debug.get("route") or None,
        source_url=str(source_url) if source_url else None,
        page_url=str(page_url) if page_url else None,
        captured_at=captured_at,
        size_bytes=size_bytes,
        char_len=char_len,
        sha256=sha,
        title=title,
        metadata_is_blocked=meta_is_blocked,
        app_blocked=app_blocked,
        block_detector=block_detector_name,
        blocked_markers=";".join(markers),
        product_evidence=product_evidence,
        empty_result_reason=empty_reason,
        derived_collection_summary=derived,
        small_html=small_html,
        tracking_url_present=tracking_present,
        page_kind=page_kind,
        include=include,
        exclusion_reason=exclusion,
        development_overlap_risk=development_overlap_risk,
    )


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def discover_html(roots: list[Path], *, exclude_roots: list[Path] | None = None) -> list[Path]:
    """Discover input HTML but do not re-ingest files produced by this script."""
    exclude_roots = [p.resolve() for p in (exclude_roots or []) if p.exists()]
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root.is_file() and root.suffix.casefold() in {".html", ".htm"}:
            paths = [root]
        elif root.is_dir():
            paths = list(root.rglob("*.html")) + list(root.rglob("*.htm"))
        else:
            continue
        for p in sorted(paths):
            try:
                rp = p.resolve()
            except OSError:
                continue
            if any(_is_under(rp, ex) for ex in exclude_roots):
                continue
            if rp in seen:
                continue
            seen.add(rp)
            out.append(rp)
    return out


def apply_exact_dedup(rows: list[ArtifactRow]) -> None:
    groups: dict[str, list[ArtifactRow]] = defaultdict(list)
    for row in rows:
        if row.include:
            groups[row.sha256].append(row)

    for sha, grp in groups.items():
        # Prefer collection_run over historical renderer test artifacts, then
        # richer metadata / newer capture / stable path as deterministic tiebreaks.
        def rank(r: ArtifactRow) -> tuple[int, int, str, str]:
            provenance_rank = {
                "collection_run": 0,
                "latest_source_html": 1,
                "renderer_artifact": 2,
                "stealth_renderer": 3,
                "debug_paired_html": 4,
                "unknown_local_html": 5,
            }.get(r.provenance, 9)
            metadata_penalty = 0 if (r.page_url or r.source_url) else 1
            return (provenance_rank, metadata_penalty, r.captured_at or "", r.artifact_path)

        grp.sort(key=rank)
        representative = grp[0]
        representative.page_id = stable_page_id(representative.source, sha)
        for dup in grp[1:]:
            dup.exact_duplicate = True
            dup.duplicate_of_page_id = representative.page_id
            dup.page_id = representative.page_id
            dup.include = False
            dup.exclusion_reason = "exact_duplicate"
            dup.page_kind = dup.page_kind

    for row in rows:
        if row.include and not row.page_id:
            row.page_id = stable_page_id(row.source, row.sha256)


def dataset_fingerprint(selected: list[ArtifactRow]) -> str:
    h = hashlib.sha256()
    for row in sorted(selected, key=lambda r: r.page_id or ""):
        h.update((row.page_id or "").encode())
        h.update(b"\0")
        h.update(row.sha256.encode())
        h.update(b"\0")
        h.update((row.source or "").encode())
        h.update(b"\n")
    return h.hexdigest()


def _filename_token(value: str | None, fallback: str = "unknown") -> str:
    raw = (value or fallback).strip().casefold()
    raw = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._")
    return raw or fallback


def assign_benchmark_filenames(selected: list[ArtifactRow]) -> None:
    """Assign deterministic copied filenames.

    With run_id: <domain>-<run_id>.html. Collisions get -2, -3, ...
    Without run_id: <domain>-run-none1.html, -run-none2.html, ... per domain.
    """
    selected.sort(key=lambda r: (r.source or "", r.run_id or "", r.captured_at or "", r.sha256))
    none_counter: defaultdict[str, int] = defaultdict(int)
    used: Counter[str] = Counter()

    for row in selected:
        domain = _filename_token(row.source, "unknown")
        if row.run_id:
            rid = _filename_token(row.run_id, "run-unknown")
            if not rid.startswith("run-"):
                rid = f"run-{rid}"
            base = f"{domain}-{rid}"
            used[base] += 1
            suffix = "" if used[base] == 1 else f"-{used[base]}"
            filename = f"{base}{suffix}.html"
        else:
            none_counter[domain] += 1
            filename = f"{domain}-run-none{none_counter[domain]}.html"
        row.benchmark_html_file = f"html/{filename}"


def copy_selected_html(selected: list[ArtifactRow], out_dir: Path) -> None:
    """Rebuild out-dir/html and copy selected pages byte-for-byte."""
    dest = out_dir / "html"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    for row in selected:
        if not row.benchmark_html_file:
            raise RuntimeError(f"No benchmark filename assigned for {row.abs_path}")
        src = Path(row.abs_path)
        dst = out_dir / row.benchmark_html_file
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if file_sha256(dst.read_bytes()) != row.sha256:
            raise RuntimeError(f"SHA-256 mismatch after copy: {src} -> {dst}")


def build_annotation_template(selected: list[ArtifactRow]) -> tuple[list[dict[str, Any]], list[str]]:
    """Create blank manual-GT rows; do not infer any product field."""
    fields = [
        "gt_id", "page_id", "html_file", "entity_id", "source", "run_id",
        "query", "page_url", "page_kind",
        "is_product", "name_gt", "price_gt", "product_url_gt", "in_stock_gt",
        "rating_gt", "delivery_time_gt", "region_gt",
        "annotator", "annotation_status", "notes",
    ]
    rows: list[dict[str, Any]] = []
    for i, r in enumerate(selected, 1):
        rows.append({
            "gt_id": i,
            "page_id": r.page_id or "",
            "html_file": r.benchmark_html_file or "",
            "entity_id": "",
            "source": r.source or "",
            "run_id": r.run_id or "",
            "query": r.query or "",
            "page_url": r.page_url or "",
            "page_kind": r.page_kind,
            "is_product": "",
            "name_gt": "",
            "price_gt": "",
            "product_url_gt": "",
            "in_stock_gt": "",
            "rating_gt": "",
            "delivery_time_gt": "",
            "region_gt": "",
            "annotator": "",
            "annotation_status": "pending",
            "notes": "",
        })
    return rows, fields


def _excel_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _write_sheet(ws, rows: list[dict[str, Any]], fields: list[str], *, html_link_column: str | None = None) -> None:
    ws.append(fields)
    for row in rows:
        ws.append([_excel_value(row.get(f)) for f in fields])

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    width_map = {
        "gt_id": 9, "page_id": 30, "html_file": 48, "benchmark_html_file": 48,
        "artifact_path": 48, "abs_path": 60, "source": 24, "run_id": 22,
        "query": 32, "page_url": 52, "source_url": 52, "title": 42,
        "notes": 42, "blocked_markers": 42, "exclusion_reason": 30,
        "sha256": 66, "name_gt": 42, "product_url_gt": 52,
        "delivery_time_gt": 22, "annotation_status": 20,
    }
    for idx, field in enumerate(fields, 1):
        ws.column_dimensions[ws.cell(1, idx).column_letter].width = width_map.get(field, 18)

    for row_cells in ws.iter_rows(min_row=2):
        for cell in row_cells:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    if html_link_column and html_link_column in fields:
        col = fields.index(html_link_column) + 1
        for r in range(2, ws.max_row + 1):
            cell = ws.cell(r, col)
            if cell.value:
                cell.hyperlink = str(cell.value)
                cell.style = "Hyperlink"


def write_excel_workbook(
    path: Path,
    *,
    selected: list[ArtifactRow],
    excluded: list[ArtifactRow],
    all_rows: list[ArtifactRow],
    summary: dict[str, Any],
) -> None:
    wb = Workbook()
    wb.remove(wb.active)

    gt_rows, gt_fields = build_annotation_template(selected)
    page_fields = list(ArtifactRow.__dataclass_fields__.keys())

    ws_gt = wb.create_sheet("GroundTruth")
    _write_sheet(ws_gt, gt_rows, gt_fields, html_link_column="html_file")

    if ws_gt.max_row >= 2:
        status_col = gt_fields.index("annotation_status") + 1
        is_product_col = gt_fields.index("is_product") + 1
        in_stock_col = gt_fields.index("in_stock_gt") + 1
        max_validation_row = max(ws_gt.max_row, 5000)

        dv_status = DataValidation(
            type="list", formula1='"pending,in_progress,done,skip"', allow_blank=True
        )
        dv_bool = DataValidation(type="list", formula1='"0,1"', allow_blank=True)
        ws_gt.add_data_validation(dv_status)
        ws_gt.add_data_validation(dv_bool)

        letter = ws_gt.cell(2, status_col).column_letter
        dv_status.add(f"{letter}2:{letter}{max_validation_row}")
        for cidx in (is_product_col, in_stock_col):
            letter = ws_gt.cell(2, cidx).column_letter
            dv_bool.add(f"{letter}2:{letter}{max_validation_row}")

    ws_pages = wb.create_sheet("Pages")
    _write_sheet(
        ws_pages, [asdict(r) for r in selected], page_fields,
        html_link_column="benchmark_html_file"
    )

    ws_exc = wb.create_sheet("Excluded")
    _write_sheet(ws_exc, [asdict(r) for r in excluded], page_fields)

    ws_all = wb.create_sheet("AllArtifacts")
    _write_sheet(ws_all, [asdict(r) for r in all_rows], page_fields)

    ws_sum = wb.create_sheet("Summary")
    ws_sum.append(["key", "value"])
    summary_rows: list[tuple[str, Any]] = []

    def flatten(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                flatten(f"{prefix}.{k}" if prefix else str(k), v)
        elif isinstance(value, list):
            summary_rows.append((prefix, "; ".join(map(str, value))))
        else:
            summary_rows.append((prefix, value))

    flatten("", summary)
    for k, v in summary_rows:
        ws_sum.append([k, _excel_value(v)])
    for cell in ws_sum[1]:
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.font = Font(color="FFFFFF", bold=True)
    ws_sum.freeze_panes = "A2"
    ws_sum.column_dimensions["A"].width = 48
    ws_sum.column_dimensions["B"].width = 85
    for row in ws_sum.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("roots", nargs="+", type=Path, help="Local directories/files containing cached HTML artifacts")
    ap.add_argument("--out-dir", type=Path, default=Path(".artifacts/dind"))
    ap.add_argument("--project-root", type=Path, default=None, help="PriceTracker root; auto-detected when omitted")
    ap.add_argument("--small-threshold", type=int, default=20_000,
                    help="Diagnostic 'small HTML' threshold in bytes (not a block criterion by itself)")
    ap.add_argument("--tiny-threshold", type=int, default=1_500,
                    help="Exclude non-product/non-empty pages below this byte size")
    ap.add_argument("--keep-no-product-evidence", action="store_true",
                    help="Keep nonblocked pages even when no product/empty-result evidence is found")
    ap.add_argument("--xlsx-name", default="dind.xlsx", help="Output workbook filename inside --out-dir")
    args = ap.parse_args()

    roots = [p.expanduser().resolve() for p in args.roots]
    missing = [str(p) for p in roots if not p.exists()]
    if missing:
        print("[warn] Missing roots: " + ", ".join(missing), file=sys.stderr)
    roots = [p for p in roots if p.exists()]
    if not roots:
        print("[error] No existing roots", file=sys.stderr)
        return 2

    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    project_root = find_project_root(args.project_root, roots)
    detector, detector_name = load_block_detector(project_root)
    html_paths = discover_html(roots, exclude_roots=[out_dir])
    print(f"[*] HTML files discovered: {len(html_paths)}")
    print(f"[*] Block detector: {detector_name}")
    if project_root:
        print(f"[*] Project root: {project_root}")

    rows: list[ArtifactRow] = []
    for i, path in enumerate(html_paths, 1):
        try:
            rows.append(classify_one(
                path,
                roots=roots,
                block_detector=detector,
                block_detector_name=detector_name,
                small_threshold=args.small_threshold,
                tiny_threshold=args.tiny_threshold,
                keep_no_product_evidence=args.keep_no_product_evidence,
            ))
        except Exception as exc:
            print(f"[warn] Failed {path}: {exc}", file=sys.stderr)
        if i % 500 == 0:
            print(f"    processed {i}/{len(html_paths)}")

    apply_exact_dedup(rows)
    selected = [r for r in rows if r.include]
    excluded = [r for r in rows if not r.include]

    assign_benchmark_filenames(selected)
    copy_selected_html(selected, out_dir)

    by_prov = Counter(r.provenance for r in rows)
    selected_by_source = Counter(r.source or "<unknown>" for r in selected)
    exclusion_counts = Counter(r.exclusion_reason or "<none>" for r in excluded)
    page_kind_counts = Counter(r.page_kind for r in selected)
    fingerprint = dataset_fingerprint(selected)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "roots": [str(p) for p in roots],
        "project_root": str(project_root) if project_root else None,
        "block_detector": detector_name,
        "parameters": {
            "small_threshold": args.small_threshold,
            "tiny_threshold": args.tiny_threshold,
            "keep_no_product_evidence": args.keep_no_product_evidence,
            "xlsx_name": args.xlsx_name,
            "selected_html_directory": "html",
        },
        "counts": {
            "html_discovered": len(html_paths),
            "classified": len(rows),
            "selected_unique_pages": len(selected),
            "excluded": len(excluded),
            "blocked": sum(1 for r in rows if r.exclusion_reason == "blocked"),
            "derived_collection_summaries": sum(1 for r in rows if r.exclusion_reason == "derived_collection_summary"),
            "exact_duplicates": sum(1 for r in rows if r.exclusion_reason == "exact_duplicate"),
            "small_html_all": sum(1 for r in rows if r.small_html),
            "historical_stealth_selected": sum(1 for r in selected if r.provenance == "stealth_renderer"),
            "development_overlap_risk_selected": sum(1 for r in selected if r.development_overlap_risk),
        },
        "selected_page_kinds": dict(sorted(page_kind_counts.items())),
        "provenance_all": dict(sorted(by_prov.items())),
        "exclusion_reasons": dict(sorted(exclusion_counts.items())),
        "selected_by_source": dict(sorted(selected_by_source.items())),
        "dataset_fingerprint_sha256": fingerprint,
        "methodological_flags": {
            "network_requests_performed": False,
            "derived_pipeline_html_excluded": True,
            "blocked_pages_excluded_from_extraction_set": True,
            "legitimate_empty_results_retained": True,
            "exact_content_duplicates_removed": True,
            "historical_stealth_artifacts_marked_development_overlap_risk": True,
            "ground_truth_product_fields_auto_extracted": False,
            "selected_html_copied_for_offline_benchmark": True,
        },
    }
    (out_dir / "dind_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    workbook_path = out_dir / args.xlsx_name
    write_excel_workbook(
        workbook_path, selected=selected, excluded=excluded, all_rows=rows, summary=summary
    )

    print(f"[+] Selected unique D_ind candidates: {len(selected)}")
    print(f"[+] Excluded: {len(excluded)}")
    print(f"[+] Dataset fingerprint: {fingerprint}")
    print(f"[+] Workbook: {workbook_path}")
    print(f"[+] Benchmark HTML: {out_dir / 'html'}")
    print(f"[+] Outputs: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
