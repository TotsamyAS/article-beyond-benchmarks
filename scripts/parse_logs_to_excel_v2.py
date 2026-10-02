#!/usr/bin/env python3
"""
scripts/parse_logs_to_excel_v2.py

Парсит логи pricetracker + products.jsonl + HTML-артефакты и строит
компактный Excel для Section 5 (Q1-журнал).

Запуск:
    python scripts/parse_logs_to_excel_v2.py .docker-logs/api .docker-logs/airflow `
        --products archive/legacy/products.jsonl `
        --artifacts .artifacts/collection `
        --reference section5_prev.xlsx `
        -o .artifacts/log-reports/section5.xlsx

Как получить reference (предыдущая сборка):
    это тот же самый xlsx, полученный прошлым запуском скрипта.
    В нём должны быть листы:
      - ProductsFromDB  (столбец is_false_positive)
      - GroundTruth     (name_gt, name_ok, price_sys, price_gt, price_ok)
    Ключ сопоставления: (run_id, source, url). url нормализуется
    (lowercase, strip query/fragment, strip trailing slash).

Что делают формулы TP/FP/FN (соответствуют вашим исходным SUMIFS):
    TP = 1, если is_false_positive == 0
    FP = 1, если is_false_positive == 1
    FN = 1, если name_gt присутствует, а name_sys пустой
         (или is_false_positive == "" и name_sys пустой)
"""

from __future__ import annotations
import argparse
import ast
import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd

# --- regexes ---

TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+(\w+)\s+(\S+)\s*(.*)$"
)
RUN_SUB_RE = re.compile(r"^collector:(run-[0-9a-f]+):(.+)$")
SRC_URL_RE = re.compile(r"^https?://(?:www\.)?([^/]+)")
JSONLD_RE = re.compile(
    r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# --- minimal sheet schemas ---

GT_COLS = [
    "gt_id", "run_id", "source", "product_url", "is_product",
    "name_sys", "name_gt", "name_ok",
    "price_sys", "price_gt", "price_ok",
    "confidence", "notes",
    "is_false_positive", "TP", "FP", "FN",
]

PRED_COLS = [
    "pred_id", "run_id", "source", "product_url", "variant",
    "pred_name", "pred_price",
    "name_ok", "price_ok", "notes",
]

REF_MANUAL_GT_COLS = [
    "name_gt", "name_ok", "price_sys", "price_gt", "price_ok",
    "is_product", "confidence", "notes",
]

# --- helpers ---

def _to_int(x: Any) -> int | None:
    if x is None or x == "":
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        try:
            return int(float(x))
        except (TypeError, ValueError):
            return None


def _is_blank(x: Any) -> bool:
    if x is None:
        return True
    if isinstance(x, float) and pd.isna(x):
        return True
    return str(x).strip() == ""


def normalize_source(s: str | None) -> str | None:
    if not s:
        return None
    s = str(s).strip().strip("'").strip('"').lower()
    m = SRC_URL_RE.match(s)
    if m:
        s = m.group(1)
    if s.startswith("www."):
        s = s[4:]
    return s


def _norm_url(u: Any) -> str | None:
    if _is_blank(u):
        return None
    u = str(u).strip().rstrip("/").split("?")[0].split("#")[0]
    return u.lower()


def _name_match(a: str | None, b: str | None, threshold: float = 0.85) -> bool:
    if not a or not b:
        return False
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio() >= threshold


def _price_match(p1: Any, p2: Any, tol: float = 0.01) -> bool:
    if p1 is None or p2 is None or p1 == "" or p2 == "":
        return False
    try:
        a, b = float(p1), float(p2)
    except (ValueError, TypeError):
        return False
    if b == 0:
        return abs(a) < 1e-9
    return abs(a - b) / abs(b) <= tol


def parse_kv(s: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    i, n = 0, len(s)
    while i < n:
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break
        m = re.match(r"([A-Za-z_][\w]*)\s*=", s[i:])
        if not m:
            j = i
            while j < n and not s[j].isspace():
                j += 1
            i = j
            continue
        key = m.group(1)
        i += m.end()
        if i >= n:
            out[key] = ""
            break
        c = s[i]
        if c in ("'", '"'):
            q = c
            i += 1
            buf: list[str] = []
            while i < n and s[i] != q:
                if s[i] == "\\" and i + 1 < n:
                    buf.append(s[i + 1])
                    i += 2
                else:
                    buf.append(s[i])
                    i += 1
            if i < n:
                i += 1
            out[key] = "".join(buf)
        elif c in "[{":
            close = "]" if c == "[" else "}"
            depth = 1
            i += 1
            start = i
            while i < n and depth > 0:
                if s[i] == c:
                    depth += 1
                elif s[i] == close:
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            out[key] = s[start:i]
            if i < n:
                i += 1
        else:
            start = i
            while i < n and not s[i].isspace():
                i += 1
            out[key] = s[start:i]
    return out


def parse_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = ast.literal_eval("[" + raw + "]")
        if isinstance(v, list):
            return [str(x) for x in v]
    except Exception:
        pass
    items: list[str] = []
    buf: list[str] = []
    in_q = False
    q = None
    for ch in raw:
        if not in_q and ch in ("'", '"'):
            in_q = True
            q = ch
            continue
        if in_q and ch == q:
            in_q = False
            q = None
            continue
        if not in_q and ch == ",":
            items.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf:
        items.append("".join(buf).strip())
    return [x for x in items if x]


# --- log reading ---

def read_logs(logs_dir: Path) -> list[dict]:
    records: list[dict] = []
    for path in sorted(logs_dir.rglob("*.log")):
        rel = path.relative_to(logs_dir).as_posix()
        current: dict | None = None
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    line = raw.rstrip("\n")
                    m = TS_RE.match(line)
                    if m:
                        ts, level, logger, rest = m.groups()
                        parts = rest.split(None, 1)
                        event = parts[0] if parts else ""
                        kv_str = parts[1] if len(parts) > 1 else ""
                        kv = parse_kv(kv_str)
                        rec: dict[str, Any] = {
                            "timestamp": ts, "level": level, "logger": logger,
                            "event": event, "kv": kv, "lines": [line],
                            "source_file": rel, "run_id": None,
                        }
                        rid = kv.get("run_id")
                        if rid:
                            rec["run_id"] = str(rid).strip("'")
                        else:
                            rm = RUN_SUB_RE.match(event)
                            if rm:
                                rec["run_id"] = rm.group(1)
                        records.append(rec)
                        current = rec
                    elif current is not None:
                        current["lines"].append(line)
        except Exception as e:
            records.append({
                "timestamp": "", "level": "ERROR", "logger": "",
                "event": "", "kv": {}, "lines": [f"<read error: {e}>"],
                "source_file": rel, "run_id": None,
            })
    return records


# --- products.jsonl ---

def load_products(path: Path | None) -> pd.DataFrame:
    if not path or not path.exists():
        return pd.DataFrame()
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "source" in df.columns:
        df["source"] = df["source"].astype(str).map(normalize_source)
    for col in ("price", "rating", "in_stock", "is_current"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# --- HTML artifacts + JSON-LD ---

def _iter_jsonld_nodes(data):
    if isinstance(data, list):
        for x in data:
            yield from _iter_jsonld_nodes(x)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _iter_jsonld_nodes(data["@graph"])
        yield data


def _node_to_product(node: dict) -> dict:
    offers = node.get("offers") or node.get("Offer") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price = None
    availability = None
    for src in (offers, node):
        if isinstance(src, dict):
            p = src.get("price") or src.get("lowPrice")
            if p is not None and price is None:
                try:
                    price = float(str(p).replace(",", ".").replace(" ", ""))
                except (ValueError, TypeError):
                    pass
            a = src.get("availability")
            if a and availability is None:
                availability = str(a)
    return {
        "name": node.get("name"),
        "url": node.get("url") or (offers.get("url") if isinstance(offers, dict) else None),
        "price": price,
        "availability": availability,
    }


def extract_jsonld_products(html_text: str) -> list[dict]:
    products: list[dict] = []
    for m in JSONLD_RE.finditer(html_text):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
            except json.JSONDecodeError:
                continue
        for node in _iter_jsonld_nodes(data):
            if not isinstance(node, dict):
                continue
            t = node.get("@type")
            types = [t] if isinstance(t, str) else (t or [])
            if not any("product" in str(x).lower() for x in types):
                continue
            products.append(_node_to_product(node))
    return products


def load_gt_cache(artifacts_root: Path | None) -> dict[tuple[str, str], list[dict]]:
    cache: dict[tuple[str, str], list[dict]] = {}
    if not artifacts_root or not artifacts_root.exists():
        return cache
    for run_dir in sorted(artifacts_root.glob("run-*")):
        rid = run_dir.name
        for html_path in run_dir.glob("*.html"):
            src = normalize_source(html_path.stem)
            if not src or not rid:
                continue
            try:
                text = html_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            cache[(rid, src)] = extract_jsonld_products(text)
    return cache


def _find_gt_match(cache, rid, src, url, name_sys):
    products = cache.get((rid, src), [])
    if not products:
        return None, None
    if url:
        u = _norm_url(url)
        for p in products:
            if _norm_url(p.get("url")) == u:
                return p.get("name"), p.get("price")
    if name_sys:
        best, best_score = None, 0.0
        for p in products:
            n = p.get("name")
            if not n:
                continue
            score = SequenceMatcher(None, name_sys.lower(), n.lower()).ratio()
            if score > best_score:
                best_score, best = score, p
        if best and best_score >= 0.9:
            return best.get("name"), best.get("price")
    return None, None


# --- reference xlsx (manual annotations) ---

def load_reference(path: Path | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Загружает reference xlsx с ручной разметкой.

    Ожидаются листы 'GroundTruth' и/или 'ProductsFromDB'.
    Возвращает (gt_ref, products_ref); оба могут быть пустыми.
    """
    if not path:
        return pd.DataFrame(), pd.DataFrame()
    p = Path(path).expanduser()
    if not p.exists():
        print(f"[!] Reference не найден: {p}")
        return pd.DataFrame(), pd.DataFrame()
    try:
        xl = pd.ExcelFile(p)
    except Exception as e:
        print(f"[!] Не могу открыть reference xlsx: {e}")
        return pd.DataFrame(), pd.DataFrame()

    gt_ref = pd.DataFrame()
    prod_ref = pd.DataFrame()
    for name in xl.sheet_names:
        low = name.lower()
        try:
            if low == "groundtruth":
                gt_ref = pd.read_excel(xl, sheet_name=name)
            elif low == "productsfromdb":
                prod_ref = pd.read_excel(xl, sheet_name=name)
        except Exception as e:
            print(f"[!] Не читается лист {name}: {e}")
    print(f"[*] Reference: GroundTruth={len(gt_ref)} строк, "
          f"ProductsFromDB={len(prod_ref)} строк")
    return gt_ref, prod_ref


def _build_merge_key(df: pd.DataFrame) -> pd.Series:
    """Composite key: run_id | source_norm | url_norm (учитывает доступные колонки)."""
    if df.empty:
        return pd.Series(dtype=str)

    def _col(name_variants):
        for v in name_variants:
            if v in df.columns:
                return df[v]
        return None

    parts = []
    run = _col(["run_id"])
    if run is not None:
        parts.append(run.astype(str).str.strip().fillna(""))
    src = _col(["source"])
    if src is not None:
        parts.append(src.map(normalize_source).astype(str).fillna(""))
    url = _col(["product_url", "url"])
    if url is not None:
        parts.append(url.map(_norm_url).astype(str).fillna(""))
    if not parts:
        return pd.Series([""] * len(df), index=df.index)
    key = parts[0]
    for p in parts[1:]:
        key = key + "|" + p
    return key


def _merge_gt_manual(new_gt: pd.DataFrame, gt_ref: pd.DataFrame) -> pd.DataFrame:
    """Переносит ручные поля name_gt/name_ok/price_sys/price_gt/price_ok/notes
    из reference GroundTruth в новую сборку по ключу (run_id|source|url)."""
    if new_gt.empty or gt_ref.empty:
        return new_gt
    ref = gt_ref.copy()

    # Обратная совместимость со старыми именами колонок
    renames = {}
    if "url" in ref.columns and "product_url" not in ref.columns:
        renames["url"] = "product_url"
    if "name" in ref.columns and "name_sys" not in ref.columns:
        renames["name"] = "name_sys"
    if "price" in ref.columns and "price_sys" not in ref.columns:
        renames["price"] = "price_sys"
    if "name_correct" in ref.columns and "name_ok" not in ref.columns:
        renames["name_correct"] = "name_ok"
    if "price_correct" in ref.columns and "price_ok" not in ref.columns:
        renames["price_correct"] = "price_ok"
    ref = ref.rename(columns=renames)

    new_key = _build_merge_key(new_gt)
    ref_key = _build_merge_key(ref)
    if new_key.empty or ref_key.empty:
        return new_gt
    new_gt = new_gt.assign(_k=new_key)
    ref = ref.assign(_k=ref_key)

    manual = [c for c in REF_MANUAL_GT_COLS if c in ref.columns]
    if not manual:
        return new_gt.drop(columns=["_k"])

    ref_small = (ref[["_k"] + manual]
                 .drop_duplicates(subset=["_k"], keep="first")
                 .rename(columns={c: f"_ref_{c}" for c in manual}))

    merged = new_gt.merge(ref_small, on="_k", how="left")
    for c in manual:
        ref_col = f"_ref_{c}"
        if ref_col not in merged.columns:
            continue
        if c not in merged.columns:
            merged[c] = merged[ref_col]
        else:
            # object-dtype: иначе pandas падает, если колонка была string,
            # а из reference приходит число (1/0/2590)
            if str(merged[c].dtype) == "string":
                merged[c] = merged[c].astype(object)
            mask = merged[ref_col].notna() & (merged[ref_col].astype(str) != "")
            merged.loc[mask, c] = merged.loc[mask, ref_col].values
        merged = merged.drop(columns=[ref_col])
    return merged.drop(columns=["_k"])


def _merge_gt_fp(new_gt: pd.DataFrame, prod_ref: pd.DataFrame) -> pd.DataFrame:
    """Переносит is_false_positive из reference ProductsFromDB."""
    if new_gt.empty or prod_ref.empty:
        return new_gt
    ref = prod_ref.copy()
    if "is_false_positive" not in ref.columns:
        return new_gt
    if "url" not in ref.columns and "product_url" in ref.columns:
        ref = ref.rename(columns={"product_url": "url"})

    new_key = _build_merge_key(new_gt)
    ref_key = _build_merge_key(ref)
    if new_key.empty or ref_key.empty:
        return new_gt
    new_gt = new_gt.assign(_k=new_key)
    ref = ref.assign(_k=ref_key)

    ref_small = (ref[["_k", "is_false_positive"]]
                 .drop_duplicates(subset=["_k"], keep="first")
                 .rename(columns={"is_false_positive": "_ref_fp"}))
    merged = new_gt.merge(ref_small, on="_k", how="left")

    if "is_false_positive" not in merged.columns:
        merged["is_false_positive"] = merged["_ref_fp"]
    else:
        if str(merged["is_false_positive"].dtype) == "string":
            merged["is_false_positive"] = merged["is_false_positive"].astype(object)
        mask = merged["_ref_fp"].notna()
        merged.loc[mask, "is_false_positive"] = merged.loc[mask, "_ref_fp"].values
    merged = merged.drop(columns=["_ref_fp", "_k"])
    return merged


def _compute_tp_fp_fn(df: pd.DataFrame) -> pd.DataFrame:
    """Вычисляет TP/FP/FN (аналогично SUMIFS-формулам в FieldAssessment).

      TP = 1, если is_false_positive == 0
      FP = 1, если is_false_positive == 1
      FN = 1, если name_gt присутствует, а name_sys пустой
           (или is_false_positive пустой и name_sys пустой)
    Если is_false_positive пуст (нет разметки в reference) — TP=FP=0,
    строку надо доразметить.
    """
    if df.empty:
        df["TP"] = 0
        df["FP"] = 0
        df["FN"] = 0
        return df

    def _bin(x) -> int | None:
        if _is_blank(x):
            return None
        try:
            return 1 if int(float(x)) != 0 else 0
        except (ValueError, TypeError):
            s = str(x).strip().lower()
            if s in ("1", "true", "yes", "y"):
                return 1
            if s in ("0", "false", "no", "n"):
                return 0
            return None

    tp, fp, fn = [], [], []
    for _, r in df.iterrows():
        is_fp = _bin(r.get("is_false_positive"))
        name_sys_blank = _is_blank(r.get("name_sys"))
        name_gt_blank = _is_blank(r.get("name_gt"))

        if is_fp is None:
            tp.append(0); fp.append(0)
        elif is_fp == 1:
            tp.append(0); fp.append(1)
        else:
            tp.append(1); fp.append(0)

        if not name_gt_blank and name_sys_blank:
            fn.append(1)
        else:
            fn.append(0)

    df = df.copy()
    df["TP"] = tp
    df["FP"] = fp
    df["FN"] = fn
    return df


def find_reference_orphans(new_gt: pd.DataFrame, gt_ref: pd.DataFrame) -> pd.DataFrame:
    """Строки reference, которых нет в новой сборке (чтобы не потерять разметку)."""
    if gt_ref.empty:
        return pd.DataFrame()
    ref = gt_ref.copy()
    if "url" in ref.columns and "product_url" not in ref.columns:
        ref = ref.rename(columns={"url": "product_url"})
    ref_k = _build_merge_key(ref)
    if new_gt.empty:
        return ref.assign(_k=ref_k).drop(columns=["_k"])
    new_k = set(_build_merge_key(new_gt))
    mask = ~ref_k.isin(new_k)
    return ref.loc[mask].copy()


# --- core sheet builders ---

def build_runs(records: list[dict]) -> pd.DataFrame:
    runs: dict[str, dict] = {}
    for r in records:
        rid = r.get("run_id")
        if not rid:
            continue
        e = runs.setdefault(rid, {
            "run_id": rid, "session_id": None, "query": None,
            "started_at": None, "finished_at": None, "status": None,
            "sources_ok": set(), "sources_failed": set(),
        })
        ts = r["timestamp"]
        if ts:
            if e["started_at"] is None or ts < e["started_at"]:
                e["started_at"] = ts
            if e["finished_at"] is None or ts > e["finished_at"]:
                e["finished_at"] = ts
        kv = r["kv"]
        if kv.get("session_id") and not e["session_id"]:
            e["session_id"] = kv["session_id"]
        if kv.get("query") and not e["query"]:
            e["query"] = kv["query"]
        if r["event"] == "collection_run_event_terminal":
            e["status"] = kv.get("status")
            for s in parse_list(kv.get("processed", "")):
                e["sources_ok"].add(normalize_source(s))
            for s in parse_list(kv.get("failed", "")):
                e["sources_failed"].add(normalize_source(s))
    rows = []
    for rid, e in sorted(runs.items()):
        rows.append({
            "run_id": rid,
            "session_id": e["session_id"],
            "query": e["query"],
            "started_at": e["started_at"],
            "finished_at": e["finished_at"],
            "status": e["status"],
            "sources_ok": ", ".join(sorted(x for x in e["sources_ok"] if x)),
            "sources_failed": ", ".join(sorted(x for x in e["sources_failed"] if x)),
        })
    return pd.DataFrame(rows)


def classify_result(sp: dict) -> str:
    cnt = sp.get("candidates_count")
    status = sp.get("extraction_status")
    if status == "extracted" and cnt and cnt > 0:
        return "candidates_found"
    if status == "failed":
        return "failed_external_or_error"
    if status == "empty" or cnt == 0:
        return "empty_no_error_likely_FN_or_by_design"
    return "unknown"


def build_candidates_and_source_results(records: list[dict]):
    by_run: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("run_id"):
            by_run[r["run_id"]].append(r)

    cand_rows: list[dict] = []
    src_rows: list[dict] = []

    for rid, evs in by_run.items():
        evs.sort(key=lambda x: x["timestamp"] or "")
        session_id = None
        query = None
        for r in evs:
            kv = r["kv"]
            if kv.get("session_id") and not session_id:
                session_id = kv["session_id"]
            if kv.get("query") and not query:
                query = kv["query"]

        current_source: str | None = None
        src_products: dict[str, dict] = {}
        src_failures: dict[str, list[dict]] = defaultdict(list)

        def _ensure(src: str, ts: str) -> dict:
            return src_products.setdefault(src, {
                "run_id": rid, "session_id": session_id, "query": query,
                "source": src, "started_at": ts,
                "page_url": None, "route": None,
                "candidates_count": None, "candidates": [],
                "extraction_status": "pending",
                "error_kind": None, "error_message": None,
            })

        for r in evs:
            kv = r["kv"]
            ev = r["event"]
            rm = RUN_SUB_RE.match(ev)
            sub = rm.group(2) if rm else None

            if sub == "start" and kv.get("source"):
                current_source = normalize_source(kv["source"])
                _ensure(current_source, r["timestamp"])

            if sub == "load_page:ok" and current_source:
                sp = _ensure(current_source, r["timestamp"])
                sp["page_url"] = kv.get("page_url")
                sp["route"] = kv.get("route")

            if sub == "products_built" and current_source:
                names = parse_list(kv.get("first_names", ""))
                urls = parse_list(kv.get("first_urls", ""))
                cnt = _to_int(kv.get("count"))
                sp = _ensure(current_source, r["timestamp"])
                sp["candidates_count"] = cnt
                sp["candidates"] = [
                    (i, names[i], urls[i] if i < len(urls) else "")
                    for i in range(len(names))
                ]
                sp["extraction_status"] = "extracted" if (cnt or 0) > 0 else "empty"

            if ev.endswith(":source_failed"):
                src = normalize_source(kv.get("source"))
                if src:
                    src_failures[src].append({
                        "kind": "source_failed",
                        "message": kv.get("error") or kv.get("reason") or "",
                        "ts": r["timestamp"],
                    })
            if ev.endswith(":route_error"):
                src = normalize_source(kv.get("source_url"))
                if src:
                    src_failures[src].append({
                        "kind": "route_error",
                        "message": kv.get("error") or "",
                        "ts": r["timestamp"],
                    })
            if ev.endswith(":stealth_renderer_fallback_failed"):
                src = normalize_source(kv.get("source"))
                if src:
                    src_failures[src].append({
                        "kind": "stealth_renderer_fallback_failed",
                        "message": kv.get("reason") or "",
                        "ts": r["timestamp"],
                    })
            if ev.endswith(":tls_check_result"):
                if (kv.get("blocked") or "").lower() == "true":
                    src = normalize_source(kv.get("source"))
                    if src:
                        src_failures[src].append({
                            "kind": "tls_blocked",
                            "message": kv.get("reason") or "",
                            "ts": r["timestamp"],
                        })

        for src, fails in src_failures.items():
            sp = _ensure(src, fails[0]["ts"])
            if sp["candidates_count"] in (None, 0):
                sp["extraction_status"] = "failed"
                sp["error_kind"] = fails[-1]["kind"]
                sp["error_message"] = fails[-1]["message"]

        for src, sp in src_products.items():
            conclusion = classify_result(sp)
            for (i, nm, u) in sp["candidates"]:
                cand_rows.append({
                    "run_id": rid,
                    "source": src,
                    "query": query,
                    "candidate_idx": i,
                    "name": nm,
                    "url": u,
                    "price": None,
                    "in_stock": None,
                })
            src_rows.append({
                "run_id": rid,
                "source": src,
                "query": query,
                "route": sp["route"],
                "candidates_count": sp["candidates_count"],
                "extraction_status": sp["extraction_status"],
                "error_kind": sp["error_kind"],
                "result_class": conclusion,
            })

    return pd.DataFrame(cand_rows), pd.DataFrame(src_rows)


def _dedup_products_for_join(p: pd.DataFrame) -> pd.DataFrame:
    if p.empty:
        return p
    keys = [c for c in ("run_id", "source", "url") if c in p.columns]
    if not keys:
        return p
    sort_cols, asc = [], []
    if "is_current" in p.columns:
        sort_cols.append("is_current"); asc.append(False)
    if "valid_from" in p.columns:
        sort_cols.append("valid_from"); asc.append(False)
    if sort_cols:
        p = p.sort_values(sort_cols, ascending=asc)
    return p.drop_duplicates(subset=keys, keep="first")


def enrich_candidates_with_products(cands: pd.DataFrame,
                                    products: pd.DataFrame) -> pd.DataFrame:
    if cands.empty or products.empty:
        return cands
    enrich_cols = [c for c in ("price", "in_stock") if c in products.columns]
    if not enrich_cols:
        return cands
    left = cands.copy().reset_index(drop=True)
    p = _dedup_products_for_join(products.copy())

    if {"run_id", "source", "url"}.issubset(left.columns) \
            and {"run_id", "source", "url"}.issubset(p.columns):
        p_url = (p[["run_id", "source", "url"] + enrich_cols]
                 .drop_duplicates(subset=["run_id", "source", "url"], keep="first"))
        left = left.merge(p_url, on=["run_id", "source", "url"],
                          how="left", suffixes=("", "_db"))

    if {"run_id", "source", "name"}.issubset(left.columns) \
            and {"run_id", "source", "name"}.issubset(p.columns):
        p_name = (p[["run_id", "source", "name"] + enrich_cols]
                  .drop_duplicates(subset=["run_id", "source", "name"], keep="first"))
        missing = left.index[left.get("price", pd.Series()).isna()] \
            if "price" in left.columns else []
        if len(missing) > 0:
            sub = left.loc[missing, ["run_id", "source", "name"]].reset_index(drop=True)
            fb = sub.merge(p_name, on=["run_id", "source", "name"],
                           how="left", suffixes=("", "_fb")).reset_index(drop=True)
            if len(fb) == len(missing):
                for col in enrich_cols:
                    if col in fb.columns:
                        left.loc[missing, col] = fb[col].values
    return left


def build_ground_truth(cands: pd.DataFrame,
                       products: pd.DataFrame,
                       artifacts_root: Path | None) -> pd.DataFrame:
    """Lean GT-шаблон с автопрефиллом из JSON-LD.
    Ручная доразметка переносится отдельно (см. _merge_gt_manual/_merge_gt_fp)."""
    if not products.empty:
        base = products.copy()
        if not cands.empty and "run_id" in base.columns:
            meta_cols = [c for c in ("run_id", "session_id", "query", "timestamp")
                         if c in cands.columns]
            if meta_cols:
                meta = cands[meta_cols].drop_duplicates("run_id")
                base = base.merge(meta, on="run_id", how="left")
    elif not cands.empty:
        base = cands.copy()
    else:
        return pd.DataFrame(columns=GT_COLS)

    cache = load_gt_cache(artifacts_root)

    rows: list[dict] = []
    for i, r in base.reset_index(drop=True).iterrows():
        rid = r.get("run_id")
        src = normalize_source(r.get("source"))
        url = r.get("url")
        name_sys = r.get("name")
        price_sys = r.get("price")

        gt_name, gt_price = _find_gt_match(cache, rid, src, url, name_sys)

        name_ok = ""
        price_ok = ""
        if gt_name and name_sys:
            name_ok = 1 if _name_match(name_sys, gt_name) else 0
        if gt_price is not None and price_sys is not None and price_sys != "":
            price_ok = 1 if _price_match(price_sys, gt_price) else 0

        if gt_name and gt_price is not None:
            confidence = "auto_high" if (name_ok == 1 and price_ok == 1) else "auto_low"
        elif gt_name or gt_price is not None:
            confidence = "auto_partial"
        else:
            confidence = "manual_needed"

        rows.append({
            "gt_id": i,
            "run_id": rid,
            "source": src,
            "product_url": url,
            "is_product": 1 if url else "",
            "name_sys": name_sys,
            "name_gt": gt_name or "",
            "name_ok": name_ok,
            "price_sys": price_sys,
            "price_gt": gt_price if gt_price is not None else "",
            "price_ok": price_ok,
            "confidence": confidence,
            "notes": "",
            "is_false_positive": "",
            "TP": 0,
            "FP": 0,
            "FN": 0,
        })

    df = pd.DataFrame(rows, columns=GT_COLS).astype(object)
    if not df.empty:
        order = {"manual_needed": 0, "auto_low": 1, "auto_partial": 2, "auto_high": 3}
        df["_o"] = df["confidence"].map(order).fillna(99)
        df = df.sort_values("_o").drop(columns=["_o"]).reset_index(drop=True)
    return df


def build_errors(records: list[dict]) -> pd.DataFrame:
    rows = []
    for r in records:
        if r["level"] not in ("WARNING", "ERROR", "CRITICAL"):
            continue
        kv = r["kv"]
        rows.append({
            "timestamp": r["timestamp"],
            "level": r["level"],
            "event": r["event"],
            "source": normalize_source(kv.get("source") or kv.get("source_url")),
            "error_type": kv.get("error_type"),
            "reason": (kv.get("reason") or "")[:200],
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def build_availability(runs, cands, srcs, gt, errs, ref_stats=None) -> pd.DataFrame:
    rows = []
    def add(item, status, detail, n=0):
        rows.append({"item": item, "status": status, "rows": n, "detail": detail})
    add("run_id", "AVAILABLE" if not runs.empty else "NOT_FOUND",
        "явный run-XXXX в событиях", len(runs))
    add("session_id", "AVAILABLE",
        "из live_search:* / collector:*",
        int(runs["session_id"].notna().sum()) if not runs.empty else 0)
    add("query", "AVAILABLE",
        "из live_search:* / collector:start",
        int(runs["query"].notna().sum()) if not runs.empty else 0)
    add("source", "AVAILABLE",
        "source/source_url, нормализация к base domain",
        int(srcs["source"].nunique()) if not srcs.empty else 0)
    add("extracted_candidates(name,url)",
        "AVAILABLE" if not cands.empty else "NOT_FOUND",
        "first_names/first_urls из collector:run-XXXX:products_built "
        "(в логе только первые ~5, полный список — в products.jsonl)",
        len(cands))
    add("per_candidate_price",
        "AVAILABLE" if "price" in cands.columns and cands["price"].notna().any() else "PARTIAL",
        "из price_history_scd2 (products.jsonl)",
        int(cands["price"].notna().sum()) if "price" in cands.columns else 0)
    add("ground_truth",
        "AVAILABLE" if not gt.empty and (gt["confidence"] != "manual_needed").any() else "PARTIAL",
        "автопрефилл из JSON-LD + перенос ручной разметки из --reference",
        len(gt))
    if ref_stats:
        add("manual_annotations",
            "AVAILABLE" if ref_stats.get("matched", 0) > 0 else "PARTIAL",
            "перенесено из --reference: is_false_positive, name_gt, name_ok, "
            "price_sys, price_gt, price_ok",
            ref_stats.get("matched", 0))
        if ref_stats.get("orphans", 0) > 0:
            add("reference_orphans", "WARN",
                "строки reference, которых нет в новой сборке — см. лист ReferenceOrphans",
                ref_stats.get("orphans", 0))
    add("error_taxonomy", "PARTIAL",
        "WARNING/ERROR из логов; антибот-шум в основную статью не идёт",
        len(errs))
    return pd.DataFrame(rows)


def _empty_pred_sheet() -> pd.DataFrame:
    return pd.DataFrame(columns=PRED_COLS)


# --- main ---

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Собирает Q1-минимальный Section-5 отчёт.",
    )
    ap.add_argument("logs", nargs="+",
                    help="папки с .log (например .docker-logs/api .docker-logs/airflow)")
    ap.add_argument("-o", "--out", default=str(Path(__file__).resolve().parents[1] / ".artifacts/log-reports/section5.xlsx"))
    ap.add_argument("--products", type=Path, default=None,
                    help="products.jsonl (price_history_scd2)")
    ap.add_argument("--artifacts", type=Path, default=None,
                    help="папка .artifacts/collection с run-*/ *.html для JSON-LD GT")
    ap.add_argument("--reference", type=Path, default=None,
                    help="предыдущий section5.xlsx с ручной разметкой "
                         "(листы GroundTruth и ProductsFromDB); "
                         "переносит is_false_positive/name_gt/name_ok/price_*/"
                         "price_ok и считает TP/FP/FN")
    args = ap.parse_args()

    all_records: list[dict] = []
    for raw_path in args.logs:
        d = Path(raw_path).expanduser().resolve()
        if not d.exists():
            print(f"[!] Пропускаю (нет папки): {d}")
            continue
        print(f"[*] Читаю {d}")
        recs = read_logs(d)
        print(f"    записей: {len(recs)}")
        all_records.extend(recs)

    if not all_records:
        print("[!] Ни одной записи не найдено.")
        return 2
    print(f"[*] Всего записей: {len(all_records)}")

    products = load_products(args.products) if args.products else pd.DataFrame()
    if args.products:
        if products.empty:
            print(f"[!] products.jsonl пуст или не найден: {args.products}")
        else:
            print(f"[*] products.jsonl: {len(products)} строк")

    # Reference (ручная разметка)
    gt_ref = pd.DataFrame()
    prod_ref = pd.DataFrame()
    if args.reference:
        gt_ref, prod_ref = load_reference(args.reference)

    runs = build_runs(all_records)
    cands, srcs = build_candidates_and_source_results(all_records)
    cands = enrich_candidates_with_products(cands, products)
    errs = build_errors(all_records)
    gt = build_ground_truth(cands, products, args.artifacts)

    # Merge manual annotations
    ref_stats = {"matched": 0, "orphans": 0}
    orphans = pd.DataFrame()
    if not gt_ref.empty and not gt.empty:
        gt = _merge_gt_manual(gt, gt_ref)
        orphans = find_reference_orphans(gt, gt_ref)
        ref_stats["orphans"] = len(orphans)
    if not prod_ref.empty and not gt.empty:
        gt = _merge_gt_fp(gt, prod_ref)
    if not gt.empty:
        matched_mask = gt["is_false_positive"].notna() & (gt["is_false_positive"].astype(str) != "")
        ref_stats["matched"] = int(matched_mask.sum())

    # TP / FP / FN
    gt = _compute_tp_fp_fn(gt)

    avail = build_availability(runs, cands, srcs, gt, errs, ref_stats)

    if not gt.empty:
        vc = gt["confidence"].value_counts().to_dict()
        print(f"[*] GroundTruth: {len(gt)} строк, confidence={vc}")
        auto = int((gt["confidence"] != "manual_needed").sum())
        print(f"    автозаполнено: {auto}, требует ручной проверки: {len(gt) - auto}")
        if ref_stats["matched"]:
            print(f"    перенесено ручных аннотаций: {ref_stats['matched']}")
        print(f"    TP={int(gt['TP'].sum())}, FP={int(gt['FP'].sum())}, "
              f"FN={int(gt['FN'].sum())}")

    def _or_note(df, note):
        return df if not df.empty else pd.DataFrame([{"note": note}])

    sheets = {
        "Availability": avail,
        "Runs": _or_note(runs, "нет run_id в логах"),
        "SourceResults": _or_note(srcs, "нет данных"),
        "Candidates": _or_note(cands, "нет products_built событий"),
        "ProductsFromDB": _or_note(products, "products.jsonl не передан"),
        "GroundTruth": gt,          # главный лист для Q1
        "Baseline": _empty_pred_sheet(),
        "Ablation": _empty_pred_sheet(),
        "Errors": _or_note(errs, "нет ошибок уровня WARNING/ERROR"),
    }
    if not orphans.empty:
        sheets["ReferenceOrphans"] = orphans

    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name[:31], index=False)

    print(f"[+] Готово: {out}")
    print("    Листы: " + ", ".join(sheets.keys()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
