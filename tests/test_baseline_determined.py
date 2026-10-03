"""Regression tests execute the notebook's actual code, without running its corpora."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load_benchmark():
    notebook = json.loads((ROOT / "baseline_determined.ipynb").read_text(encoding="utf-8"))
    ns = {}
    with contextlib.redirect_stdout(io.StringIO()):
        for i in [2, 4, 6, 8, 10, 12, 14, 16, 18]:
            exec(compile("".join(notebook["cells"][i]["source"]), f"notebook_cell_{i}", "exec"), ns)
    return ns


class BenchmarkRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = load_benchmark()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.ns["OUTPUT_DIR"] = self.folder / "results"

    def gt(self, rows, **kwargs):
        path = self.folder / "gt.csv"
        pd.DataFrame(rows, columns=None if rows else ["html_file"]).to_csv(path, index=False)
        return self.ns["read_groundtruth"](path, **kwargs)

    def manifest(self, *names):
        return pd.DataFrame([
            dict(dataset="demo", page_id=name, html_key=f"{name}.html", html_file=f"html/{name}.html",
                 run_id="capture-1", source="example.test", query="дрель", skip_reason="")
            for name in names
        ], columns=["dataset", "page_id", "html_key", "html_file", "run_id", "source", "query", "skip_reason"])

    def product(self, page="a", **changes):
        row = dict(gt_id=1, entity_id=f"entity-{page}", page_id=page, html_file=f"html/{page}.html",
                   is_product=1, name_gt="Tool Alpha", price_gt=100, product_url_gt="https://example.test/product/1")
        row.update(changes)
        return row

    def prediction(self, page="a", **changes):
        row = dict(dataset="demo", variant="b2_css", page_id=page, html_key=f"{page}.html", html_file=f"html/{page}.html",
                   run_id="capture-1", source="example.test", product_url="https://example.test/product/1",
                   pred_name="Tool Alpha", pred_price=100, pred_in_stock=None, extract_notes="fixture")
        row.update(changes)
        return row

    def evaluate(self, raw, gt, manifest, variants=("b2_css",)):
        return self.ns["evaluate_predictions"](pd.DataFrame(raw), gt, manifest, variants)

    def test_www_identity_and_relative_html_paths(self):
        norm = self.ns["_norm_url"]
        self.assertEqual(norm("HTTPS://WWW.EXAMPLE.TEST/product/1/?q=2#x"), "https://example.test/product/1")
        self.assertEqual(norm(norm("https://www.example.test/a/?x")), norm("https://example.test/a"))
        self.assertNotEqual(norm("https://shop.example.test/a"), norm("https://example.test/a"))
        self.assertIsNone(norm("https://[invalid-host/product"))
        key = self.ns["normalize_html_key"]
        self.assertNotEqual(key("html/one/a.html"), key("html/two/a.html"))

    def test_www_match_exports_gt_identity_and_correct_counters(self):
        gt = self.gt([self.product(category="tools", cluster=3)])
        ev, metrics = self.evaluate([self.prediction(product_url="https://www.example.test/product/1/")], gt, self.manifest("a"))
        r = ev.iloc[0]
        self.assertEqual((r.gt_id, r.entity_id, r.page_id, r.match_status), (1, "entity-a", "a", "matched_gt"))
        self.assertEqual(r.query, "дрель")
        self.assertEqual((r.entity_TP, r.name_TP, r.price_TP), (1, 1, 1))
        self.assertEqual(metrics.price_F1.iloc[0], 1)
        self.assertEqual(gt.category.iloc[0], "tools")
        self.assertEqual(gt.cluster.iloc[0], 3)

    def test_missing_predictions_keep_gt_ids_per_variant(self):
        ev, _ = self.evaluate([], self.gt([self.product()]), self.manifest("a"), ("b1_jsonld", "b2_css"))
        self.assertEqual(len(ev), 2)
        self.assertTrue(ev.pred_id.isna().all())
        self.assertTrue(ev.result_id.is_unique)
        self.assertEqual(set(ev.entity_id), {"entity-a"})
        self.assertTrue(ev.match_status.eq("missing_prediction").all())
        self.assertTrue(ev.entity_FN.eq(1).all())

    def test_unmatched_prediction_has_no_invented_gt_entity(self):
        gt = self.gt([dict(page_id="a", html_file="a.html", is_product=0)])
        ev, _ = self.evaluate([self.prediction(page_id="wrong-cached-id")], gt, self.manifest("a"))
        self.assertTrue(ev.entity_id.isna().all())
        self.assertTrue(ev.gt_id.isna().all())
        self.assertEqual(ev.page_id.iloc[0], "a")
        self.assertEqual(ev.entity_FP.iloc[0], 1)

    def test_subset_filters_both_gt_and_predictions(self):
        gt = self.gt([self.product(), self.product("b", gt_id=2)])
        ev, metrics = self.evaluate([self.prediction("b")], gt, self.manifest("a"))
        self.assertEqual(set(ev.page_id), {"a"})
        self.assertEqual((metrics.n_pages.iloc[0], metrics.n.iloc[0], metrics.entity_FN.iloc[0]), (1, 1, 1))
        ev, metrics = self.evaluate([self.prediction()], gt, self.manifest())
        self.assertTrue(ev.empty)
        self.assertEqual(metrics.n.iloc[0], 0)

    def test_negative_only_and_empty_exports_have_schema(self):
        for records, manifest in [([dict(page_id="a", html_file="a.html", is_product=0)], self.manifest("a")),
                                  ([], self.manifest())]:
            gt = self.gt(records)
            ev, metrics = self.evaluate([], gt, manifest)
            paths = self.ns["export_dataset_outputs"]("demo", manifest, pd.DataFrame(), gt, None, ev, metrics)
            exported = pd.read_csv(paths["predictions"])
            self.assertTrue(exported.empty)
            self.assertIn("entity_id", exported)
            self.assertIn("match_status", exported)
            pages = pd.read_csv(paths["page_results"])
            if len(manifest):
                self.assertEqual(pages.page_status.iloc[0], "negative")
                self.assertEqual(pages.page_has_products_ok.iloc[0], 1)
            else:
                self.assertTrue(pages.empty)

    def test_absent_price_penalizes_hallucinated_price(self):
        gt = self.gt([self.product(price_gt=0)], zero_price_is_missing=True)
        ev, metrics = self.evaluate([self.prediction()], gt, self.manifest("a"))
        self.assertEqual(ev.gt_price_state.iloc[0], "absent")
        self.assertEqual(ev.price_presence_ok.iloc[0], 0)
        self.assertEqual((metrics.price_FP.iloc[0], metrics.price_FN.iloc[0], metrics.unexpected_price_FP.iloc[0]), (1, 0, 1))
        ev, metrics = self.evaluate([self.prediction(pred_price=None)], gt, self.manifest("a"))
        self.assertEqual(ev.price_presence_ok.iloc[0], 1)
        self.assertEqual(metrics.price_FP.iloc[0], 0)
        ev, metrics = self.evaluate([], gt, self.manifest("a"))
        self.assertTrue(ev.price_presence_ok.isna().all())
        self.assertEqual(metrics.price_FN.iloc[0], 0)
        self.assertTrue(self.ns["groundtruth_audit"](gt, self.manifest("a"))["ready_for_available_pages"])

    def test_unknown_price_is_not_confirmed_absence(self):
        gt = self.gt([self.product(price_gt=None)], zero_price_is_missing=True)
        ev, metrics = self.evaluate([self.prediction()], gt, self.manifest("a"))
        self.assertEqual(ev.gt_price_state.iloc[0], "unannotated")
        self.assertEqual(metrics.price_FP.iloc[0], 0)
        self.assertFalse(self.ns["groundtruth_audit"](gt, self.manifest("a"))["ready_for_available_pages"])
        gt = self.gt([self.product(price_gt=None, notes="USER_REVIEW: no price; product no longer sold")], zero_price_is_missing=True)
        self.assertEqual(gt.gt_price_state.iloc[0], "absent")

    def test_synthetic_zero_is_a_real_price(self):
        gt = self.gt([self.product(price_gt=0)])
        _, metrics = self.evaluate([self.prediction(pred_price=0)], gt, self.manifest("a"))
        self.assertEqual(gt.gt_price_state.iloc[0], "present")
        self.assertEqual(metrics.price_TP.iloc[0], 1)

    def test_status_policy_and_partial_pages(self):
        for status in ["skip", "pending", "todo", "new", "excluded"]:
            gt = self.gt([self.product(annotation_status=status)])
            ev, metrics = self.evaluate([self.prediction()], gt, self.manifest("a"))
            self.assertTrue(ev.empty, status)
            self.assertEqual(metrics.n_pages.iloc[0], 0)
        gt = self.gt([self.product(annotation_status="in_progress")], annotation_policy="done")
        self.assertFalse(gt._annotated.iloc[0])
        gt = self.gt([self.product(annotation_status="in_progress")])
        self.assertTrue(gt._annotated.iloc[0])
        gt = self.gt([self.product(), self.product(gt_id=2, entity_id="other", annotation_status="pending")])
        ev, _ = self.evaluate([self.prediction()], gt, self.manifest("a"))
        self.assertTrue(ev.empty)

    def test_duplicate_normalized_keys_and_entity_ids_fail(self):
        for second in [self.product(gt_id=2, entity_id="other", product_url_gt="https://www.example.test/product/1"),
                       self.product("b", gt_id=2, entity_id="entity-a")]:
            gt = self.gt([self.product(), second])
            with self.assertRaises(ValueError):
                self.evaluate([], gt, self.manifest("a", "b"))

    def test_pages_manifest_ignores_extras_tracks_missing_and_nested_paths(self):
        html = self.folder / "html"
        for name in ["one/a.html", "two/a.html", "cadr_examples/demo.html"]:
            f = html / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("<html><body>empty</body></html>", encoding="utf-8")
        reference = self.folder / "dind.xlsx"
        pd.DataFrame([
            dict(page_id="one", benchmark_html_file="html/one/a.html", page_url="https://example.test/one", include=True),
            dict(page_id="two", benchmark_html_file="html/two/a.html", page_url="https://example.test/two", include=False),
            dict(page_id="missing", benchmark_html_file="html/missing.html", include=True),
        ]).to_excel(reference, sheet_name="Pages", index=False)
        manifest = self.ns["build_html_manifest"]("demo", html, reference)
        self.assertEqual(len(manifest), 3)
        self.assertEqual(set(manifest.html_key), {"one/a.html", "two/a.html", "missing.html"})
        self.assertEqual(manifest.set_index("page_id").loc["missing", "skip_reason"], "missing_html")
        self.assertEqual(manifest.set_index("page_id").loc["two", "skip_reason"], "excluded_in_pages")

    def test_synthetic_mutation_identity_and_export_count_consistency(self):
        gt = self.gt([dict(mutation_id="mutation-1", entity_id="entity-1", name_gt="Tool Alpha", price_gt=100,
                           product_url_gt="https://example.test/product/1", cluster="group-1")])
        self.assertEqual(gt.html_key.iloc[0], "mutation-1.html")
        self.assertEqual(gt.page_id.iloc[0], "mutation-1")
        ev, metrics = self.evaluate([self.prediction("mutation-1")], gt, self.manifest("mutation-1"))
        for col in self.ns["COUNT_COLUMNS"]:
            self.assertEqual(ev[col].sum(), metrics[col].iloc[0])

    def test_duplicate_www_predictions_choose_complete_record(self):
        gt = self.gt([self.product()])
        ev, metrics = self.evaluate([
            self.prediction(pred_price=None),
            self.prediction(product_url="https://www.example.test/product/1"),
        ], gt, self.manifest("a"))
        self.assertEqual(len(ev), 1)
        self.assertEqual(metrics.price_TP.iloc[0], 1)

    def test_main_cell_exports_both_datasets_and_empty_results(self):
        configs = {}
        for dataset in ["synthetic", "industrial"]:
            folder = self.folder / dataset
            html = folder / "html"
            html.mkdir(parents=True)
            (html / "empty.html").write_text("<html><body>No products</body></html>", encoding="utf-8")
            reference = folder / "groundtruth.csv"
            pd.DataFrame([dict(page_id="empty", html_file="empty.html", is_product=0)]).to_csv(reference, index=False)
            configs[dataset] = dict(root=folder, html_dir=html, reference_file=reference)
        notebook = json.loads((ROOT / "baseline_determined.ipynb").read_text(encoding="utf-8"))
        with patch.dict(self.ns, DATASETS=configs, RUN_DATASETS=None, display=lambda *args: None):
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile("".join(notebook["cells"][20]["source"]), "main_cell", "exec"), self.ns)
            output = self.ns["OUTPUT_DIR"]
            self.assertTrue(pd.read_csv(output / "baseline_predictions_all.csv").empty)
            self.assertEqual(len(pd.read_csv(output / "baseline_page_results_all.csv")), 4)
            self.assertEqual(len(pd.read_csv(output / "baseline_metrics_all.csv")), 4)

    def test_generated_gt_ids_survive_reordering_classification_rows(self):
        rows = [self.product(gt_id=None, category="one"), self.product("b", gt_id=None, category="two")]
        first = self.gt(rows).set_index("entity_id").gt_id.to_dict()
        second = self.gt(rows[::-1]).set_index("entity_id").gt_id.to_dict()
        self.assertEqual(first, second)

    def test_analytics_preserves_classes_false_positives_and_error_magnitude(self):
        gt = self.gt([self.product(category="tools", cluster_id=7)])
        raw = pd.DataFrame([self.prediction(pred_price=125),
                            self.prediction(product_url="https://example.test/product/extra")])
        manifest = self.manifest("a")
        ev, metrics = self.ns["evaluate_predictions"](raw, gt, manifest, ("b2_css",))
        paths = self.ns["export_dataset_outputs"]("demo", manifest, raw, gt, None, ev, metrics)
        wide = pd.read_csv(paths["entity_analytics"])
        match = wide[wide.match_status.eq("matched_gt")].iloc[0]
        self.assertEqual(match.gt_meta__category, "tools")
        self.assertEqual(match.price_error_absolute, 25)
        self.assertEqual(match.price_error_relative, .25)
        groups = pd.read_csv(paths["group_metrics"])
        classes = groups[groups.group_dimension.eq("gt_meta__category")]
        self.assertEqual(classes.entity_FP.sum(), 1)
        self.assertIn("<unmatched_or_unlabeled>", set(classes.group_value))
        by_source = groups[groups.group_dimension.eq("source")]
        for col in self.ns["COUNT_COLUMNS"]:
            self.assertEqual(by_source[col].sum(), metrics[col].sum())
        log = pd.read_csv(paths["extraction_log"])
        self.assertTrue(log.extract_ms.isna().all())
        self.assertTrue(log.extraction_status.eq("not_recorded").all())
        profile = json.loads(paths["analysis_profile"].read_text(encoding="utf-8"))
        self.assertEqual(profile["scored_gt_entities"], 1)

    def test_extraction_failure_is_distinct_from_empty_success(self):
        path = self.folder / "empty.html"
        path.write_text("<html><body>empty</body></html>", encoding="utf-8")
        manifest = self.manifest("a")
        manifest["html_path"] = str(path)
        manifest["page_url"] = "https://example.test/"
        with patch.dict(self.ns["EXTRACTORS"], broken=lambda *args: (_ for _ in ()).throw(RuntimeError("fixture error")),
                        empty=lambda *args: []):
            with contextlib.redirect_stdout(io.StringIO()):
                raw = self.ns["run_baselines"](manifest, ("broken", "empty"))
        log = self.ns["extraction_log_frame"](raw, manifest, ("broken", "empty")).set_index("variant")
        self.assertTrue(raw.empty)
        self.assertEqual(log.loc["broken", "extraction_status"], "extract_error")
        self.assertEqual(log.loc["empty", "extraction_status"], "ok")
        self.assertIn("fixture error", log.loc["broken", "error_traceback"])
        self.assertTrue(log.extract_ms.ge(0).all())

    def test_unscored_export_does_not_leave_previous_scored_files(self):
        manifest = self.manifest("a")
        gt = self.gt([self.product()])
        raw = pd.DataFrame([self.prediction()])
        ev, metrics = self.ns["evaluate_predictions"](raw, gt, manifest, ("b2_css",))
        self.ns["export_dataset_outputs"]("demo", manifest, raw, gt, None, ev, metrics)
        paths = self.ns["export_dataset_outputs"]("demo", manifest, raw, None, None, None, None)
        for key in ["predictions", "metrics", "groundtruth_loaded", "entity_analytics"]:
            self.assertTrue(pd.read_csv(paths[key]).empty, key)
        pages = pd.read_csv(paths["page_results"])
        self.assertTrue(pages.is_scored.eq(False).all())

    def test_csv_companion_selects_only_its_split(self):
        html = self.folder / 'html'
        html.mkdir()
        for name in ['clean', 'mutation']:
            (html / f'{name}.html').write_text('<html>fixture</html>')
        for dataset, name in [('synthetic', 'clean'), ('robustness', 'mutation')]:
            reference = self.folder / f'{dataset}_ground_truth.csv'
            pd.DataFrame([self.product(name)]).to_csv(reference, index=False)
            pd.DataFrame([dict(page_id=name, benchmark_html_file=f'html/{name}.html',
                               page_url='https://example.test/')]).to_csv(self.folder / f'{dataset}_pages.csv', index=False)
            manifest = self.ns['build_html_manifest'](dataset, html, reference)
            self.assertEqual(manifest.html_key.tolist(), [f'{name}.html'])

    def test_selective_run_reuses_verified_results_and_rejects_stale_gt(self):
        configs = {}
        for dataset in ['synthetic', 'industrial']:
            folder = self.folder / dataset
            html = folder / 'html'
            html.mkdir(parents=True)
            (html / 'empty.html').write_text('<html>empty</html>')
            reference = folder / 'groundtruth.csv'
            pd.DataFrame([dict(page_id='empty', html_file='empty.html', is_product=0)]).to_csv(reference, index=False)
            configs[dataset] = dict(root=folder, html_dir=html, reference_file=reference)
        notebook = json.loads((ROOT / 'baseline_determined.ipynb').read_text(encoding='utf-8'))
        main = compile(''.join(notebook['cells'][20]['source']), 'main_cell', 'exec')
        with patch.dict(self.ns, DATASETS=configs, RUN_DATASETS=None, display=lambda *args: None):
            with contextlib.redirect_stdout(io.StringIO()):
                exec(main, self.ns)
            output = self.ns['OUTPUT_DIR']
            industrial_metrics = (output / 'industrial/baseline_metrics.csv').read_bytes()
            actual_runner = self.ns['run_baselines']
            calls = []
            def tracked(manifest, variants):
                calls.append(manifest.dataset.iloc[0])
                return actual_runner(manifest, variants)
            with patch.dict(self.ns, RUN_DATASETS=('synthetic',), run_baselines=tracked):
                with contextlib.redirect_stdout(io.StringIO()):
                    exec(main, self.ns)
                self.assertEqual(calls, ['synthetic'])
                self.assertEqual(industrial_metrics, (output / 'industrial/baseline_metrics.csv').read_bytes())
                self.assertEqual(set(pd.read_csv(output / 'baseline_metrics_all.csv').dataset), {'synthetic', 'industrial'})
                refs = json.loads((output / 'baseline_combined_manifest.json').read_text())
                self.assertEqual(next(r['status'] for r in refs['datasets'] if r['dataset']=='industrial'), 'reused')
                with configs['industrial']['reference_file'].open('a') as f:
                    f.write('\n')
                with contextlib.redirect_stdout(io.StringIO()):
                    exec(main, self.ns)
                self.assertEqual(set(pd.read_csv(output / 'baseline_metrics_all.csv').dataset), {'synthetic'})


if __name__ == "__main__":
    unittest.main()
