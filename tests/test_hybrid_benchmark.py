"""Only the new hybrid notebook; temporary outputs and fake API responses."""
import ast
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from scripts.run_hybrid_benchmark import load_notebook


def response(items=None, status=200, finish='stop'):
    body=dict(id='fixture',model='qwen/qwen3.8-omni-flash',usage=dict(prompt_tokens=100,completion_tokens=20),
        choices=[dict(finish_reason=finish,message=dict(content=json.dumps({'items':items or []})))])
    if status!=200: body={'error':{'message':'fixture'}}
    return dict(body=json.dumps(body),http_status=status,transport_error=None,latency_ms=1,headers={})


class HybridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()): cls.ns=load_notebook()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.settings=patch.dict(self.ns,OUTPUT_DIR=self.root/'results',BENCHMARK_ROOT=self.root,
            ORIGINAL_RUNTIME_DIR=self.root/'runtime',RUN_MODE='run',EXPERIMENT_ID='fixture',
            RUN_DATASETS=('synthetic',),PAGE_LIMIT=None,RETRY_ERRORS=False,RETRY_UNCERTAIN=False,
            ARTIFACT_MANIFEST=None,LLM_COMPARISON_DIR=None,
            LLM_CONFIG=self.ns['LLM_CONFIG']|{'max_attempts':1})
        self.settings.start();self.addCleanup(self.settings.stop)

    def page(self, html='<div class="product-card"><a href="/product/1">Дрель Test</a><span class="price">100 ₽</span></div>'):
        file=self.root/'page.html';file.write_text(html,encoding='utf-8')
        return dict(dataset='synthetic',page_id='one',html_key='page.html',html_file='page.html',
            html_path=str(file),page_url='https://example.test/search',source_url='https://example.test/search',
            source='example.test',run_id='',query='unrelated provenance',skip_reason='',
            content_sha256=hashlib.sha256(file.read_bytes()).hexdigest())

    def item(self, **changes):
        return dict(name='Дрель Test',price=100,product_url='https://example.test/product/1',in_stock=True,**changes)

    def route(self,page,deterministic=None,transport=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.ns['hybrid_route'](page,deterministic or [],transport)

    def test_frozen_original_method_bodies_and_no_full_html_caller(self):
        snapshot=self.ns['HYBRID_SOURCE_SNAPSHOT'];source=snapshot['sources']['core/extraction/scrapegraph.py']
        cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='ScrapegraphExtractionClient')
        lines=source.splitlines(keepends=True)
        for method in cls.body:
            if isinstance(method,ast.FunctionDef) and method.name in snapshot['unchanged_methods_sha256']:
                first=min([method.lineno,*[d.lineno for d in method.decorator_list]])
                text=''.join(lines[first-1:method.end_lineno])
                self.assertEqual(hashlib.sha256(text.encode()).hexdigest(),snapshot['unchanged_methods_sha256'][method.name])
        self.assertNotIn('llm_payload',self.ns)
        self.assertNotIn('llm_page_call',self.ns)

    def test_deterministic_complete_makes_no_llm_call(self):
        def fail(_): raise AssertionError('No API on deterministic success')
        items,route,calls=self.route(self.page(),[self.item()],fail)
        self.assertEqual(route['selected_mode'],'deterministic');self.assertEqual(calls,[])

    def test_empty_gate_and_hidden_block_marker_with_product_evidence(self):
        def fail(_): raise AssertionError('No API on explicit empty page')
        _,route,calls=self.route(self.page('<p>Товары не найдены</p>'),transport=fail)
        self.assertFalse(route['diagnostics']['preflight']['run_llm']);self.assertEqual(calls,[])
        client=self.ns['HybridClient'](api_key='',model='fixture')
        decision=client._build_preflight_decision(page_html='<script>captcha</script><div>Товар 100 ₽</div>',candidate_snippets=[])
        self.assertTrue(decision['run_llm'])

    def test_candidate_request_bounded_resume_and_signature_change(self):
        sent=[]
        def transport(payload): sent.append(payload);return response([self.item()])
        page=self.page()
        items,route,calls=self.route(page,transport=transport)
        self.assertEqual(len(items),1);self.assertEqual(len(sent),1)
        self.assertIn('candidate_only',sent[0]['messages'][1]['content'])
        self.assertNotIn('unrelated provenance',sent[0]['messages'][1]['content'])
        _,_,again=self.route(page,transport=transport)
        self.assertEqual(len(sent),1);self.assertTrue(again[0]['reused'])
        with patch.dict(self.ns,HYBRID_LIMITS=self.ns['HYBRID_LIMITS']|{'max_llm_stages':2}):
            with self.assertRaises(self.ns['HybridStop']): self.route(page,transport=transport)

    def test_ax_then_candidates_and_direct_vision_fallback(self):
        page=self.page();ax=self.root/'ax.json';image=self.root/'shot.png'
        ax.write_text(json.dumps(dict(status='captured',nodes=[dict(nodeId='1',role={'value':'link'},name={'value':'Дрель 100 ₽'},properties=[])])),encoding='utf-8')
        image.write_bytes(b'fixture image bytes; fake transport only')
        paths=dict(ax_tree_file=ax,screenshot_file=image)
        seen=[]
        def transport(payload):
            content=payload['messages'][1]['content'];seen.append(content)
            return response([dict(name='Дрель Test',price=100,product_url='https://invented.test/p')]) if isinstance(content,list) else response([])
        with patch.dict(self.ns,hybrid_artifacts=lambda p:(paths,{'fixture':'hash'})):
            items,route,calls=self.route(page,transport=transport)
        self.assertEqual([c['mode'] for c in calls],['ax_tree','candidate_only','screenshot_ocr'])
        self.assertEqual(route['selected_mode'],'screenshot_ocr')
        self.assertEqual(items[0]['product_url'],page['page_url']) # original search-URL rewriting
        self.assertEqual(seen[-1][1]['type'],'image_url')

    def test_partial_candidate_preserved_when_vision_returns_empty(self):
        page=self.page();image=self.root/'shot.png';image.write_bytes(b'fixture')
        def transport(payload):
            if isinstance(payload['messages'][1]['content'],list): return response([])
            return response([self.item(),{'name':'Missing required price and URL'}])
        with patch.dict(self.ns,hybrid_artifacts=lambda p:({'screenshot_file':image},{})), \
             patch.object(self.ns['HybridClient'],'_candidate_result_complete_enough',return_value=False):
            items,route,calls=self.route(page,transport=transport)
        self.assertEqual(len(items),1);self.assertEqual(route['selected_mode'],'candidate_only_partial')

    def test_transport_read_timeout_recorded_and_retry_only_explicit(self):
        page=self.page();sent=[]
        def transport(payload): sent.append(payload);raise TimeoutError('fixture')
        _,_,calls=self.route(page,transport=transport)
        self.assertEqual(calls[0]['status'],'transport_error')
        self.route(page,transport=transport);self.assertEqual(len(sent),1)
        with patch.dict(self.ns,RETRY_ERRORS=True): self.route(page,transport=transport)
        self.assertEqual(len(sent),2)

    def test_unknown_billing_stops_and_fatal_http_stops(self):
        page=self.page()
        def interrupt(payload): raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.route(page,transport=interrupt)
        with self.assertRaises(self.ns['HybridStop']): self.route(page,transport=lambda p:response([]))
        with patch.dict(self.ns,RETRY_UNCERTAIN=True):
            with self.assertRaises(self.ns['HybridStop']): self.route(page,transport=lambda p:response([],status=403))

    def test_end_to_end_fake_transport_exports_and_replay_without_api(self):
        page=self.page();cfg=dict(root=self.root,html_dir=self.root,reference_file=self.root/'gt.csv')
        pd.DataFrame([dict(page_id='page',html_file='page.html',entity_id='one-product',name_gt='Дрель Test',
            price_gt=100,product_url_gt='https://example.test/product/1')]).to_csv(cfg['reference_file'],index=False)
        def original(manifest,variants):
            out=self.ns['OUTPUT_DIR']/'synthetic';out.mkdir(parents=True,exist_ok=True)
            (out/'original_parser_responses.jsonl').write_text(json.dumps(dict(page_id=manifest.page_id.iloc[0],result={'rows':[]}))+'\n')
            return pd.DataFrame()
        def transport(payload): return response([self.item()])
        with patch.dict(self.ns,DATASETS={'synthetic':cfg},hybrid_prepare=lambda:None,
                        run_original_baselines=original,llm_transport=transport,
                        llm_compare_determined=lambda *a:(None,{'dataset':'synthetic','status':'fixture'})):
            with contextlib.redirect_stdout(io.StringIO()): result=self.ns['hybrid_main']()
            self.assertEqual(result['status'],'complete')
            out=self.ns['OUTPUT_DIR']
            self.assertTrue((out/'baseline_comparison.xlsx').exists())
            metrics=pd.read_csv(out/'baseline_metrics_all.csv');self.assertEqual(metrics.name_F1.iloc[0],1)
            with patch.dict(self.ns,RUN_MODE='replay',llm_transport=lambda p:(_ for _ in ()).throw(AssertionError('No replay calls'))):
                with contextlib.redirect_stdout(io.StringIO()): replay=self.ns['hybrid_main']()
            self.assertEqual(replay['status'],'complete')
            costs=json.loads((out/'synthetic/hybrid_cost_summary.json').read_text(encoding='utf-8'))
            self.assertEqual(costs['new_http_attempts'],0)


if __name__=='__main__': unittest.main()
