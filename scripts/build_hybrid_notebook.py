"""Build a standalone hybrid notebook; never modifies either existing notebook."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'config/hybrid_original_snapshot.json'


def freeze_original(root):
    names = ['core/extraction/custom.py', 'core/extraction/product_card_fields.py', 'core/extraction/scrapegraph.py']
    sources = {name: (root / name).read_text(encoding='utf-8') for name in names}
    source = sources[names[-1]]
    tree = ast.parse(source)
    excluded = {'from_env', 'rank_source_candidates', '_run_inference', '_run_openai_inference',
                '_run_scrapegraph_inference', '_run_image_inference', '_build_openai_client',
                '_fetch_page_html', '_decode_response_text'}
    pieces, methods = [], {}
    lines = source.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
            if any(m.startswith(('core.', 'httpx', 'pydantic')) for m in modules): continue
            pieces.append(ast.get_source_segment(source, node))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            pieces.append(ast.get_source_segment(source, node))
        elif isinstance(node, ast.ClassDef) and node.name == '_OpenRouterImageUnsupportedError':
            pieces.append(ast.get_source_segment(source, node))
        elif isinstance(node, ast.ClassDef) and node.name == 'ScrapegraphExtractionClient':
            members = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            start = min(d.lineno for d in node.decorator_list) if node.decorator_list else node.lineno
            header = ''.join(lines[start-1:members[0].lineno-2])
            included = []
            for method in members:
                if method.name in excluded: continue
                first = min([method.lineno, *[d.lineno for d in method.decorator_list]])
                segment = ''.join(lines[first-1:method.end_lineno])
                methods[method.name] = hashlib.sha256(segment.encode()).hexdigest()
                included.append(segment)
            pieces.append(header + '\n' + '\n'.join(included))
    selected = '\n\n'.join(pieces) + '\n'
    compile(selected, '<original-selected-client>', 'exec')
    snapshot = dict(source_repository=str(root), sources=sources,
        sha256={k: hashlib.sha256(v.encode()).hexdigest() for k,v in sources.items()},
        selected_client=selected, selected_client_sha256=hashlib.sha256(selected.encode()).hexdigest(),
        unchanged_methods_sha256=methods, excluded_environment_and_network_methods=sorted(excluded),
        adapter_note='Original method bodies unchanged. Network/environment integration replaced by isolated benchmark transport; pydantic/httpx clients are not imported.')
    SNAPSHOT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build():
    original = json.loads((ROOT / 'baseline_determined.ipynb').read_text(encoding='utf-8'))
    src = {i: ''.join(c['source']) for i,c in enumerate(original['cells'])}
    snapshot = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
    reporting = (ROOT / 'scripts/benchmark_reporting.py').read_text(encoding='utf-8')
    runtime_source = (ROOT / 'scripts/llm_baseline_runtime.py').read_text(encoding='utf-8')
    # Embed only transport/ledger utilities, never the full-HTML prompt or caller.
    excluded_runtime = {'LLM_SYSTEM_PROMPT','LLM_FIELDS','llm_payload','llm_page_call',
                        'run_llm_baseline','llm_validate_products','llm_decode_response','extraction_contract'}
    kept=[]
    for node in ast.parse(runtime_source).body:
        names={node.name} if isinstance(node,(ast.FunctionDef,ast.ClassDef)) else {
            t.id for t in getattr(node,'targets',[]) if isinstance(t,ast.Name)}
        if not names & excluded_runtime: kept.append(ast.get_source_segment(runtime_source,node))
    runtime='\n\n'.join(kept)+'\n'
    analysis = (ROOT / 'scripts/llm_baseline_analysis.py').read_text(encoding='utf-8')
    hybrid = (ROOT / 'scripts/hybrid_baseline.py').read_text(encoding='utf-8')
    config = src[4].split('# Exact original parser modes')[0]
    config = config.replace('"baseline_results" / "determined"', '"baseline_results" / "hybrid"')
    config = config[:config.index('# Synthetic v2:')] if '# Synthetic v2:' in config else config
    config += '''
RUN_DATASETS = None  # Or ("synthetic", "robustness") / ("industrial",)
EXPERIMENT_ID = "qwen38-hybrid-v1"
RUN_MODE = "plan"  # offline plan / paid run / cached replay
PAGE_LIMIT = None
RETRY_ERRORS = False
RETRY_UNCERTAIN = False
VARIANTS = ("hybrid_original",)
LLM_CONFIG = dict(provider="RouterAI", base_url="https://routerai.ru/api/v1",
    model="qwen/qwen3.8-omni-flash", temperature=0.0, max_output_tokens=8192,
    timeout_seconds=180, max_attempts=3)
OUTPUT_DIR = OUTPUT_DIR / EXPERIMENT_ID
ORIGINAL_RUNTIME_DIR = OUTPUT_DIR / ".original_runtime"
RESUME_EXTRACTION = True
NAME_THRESHOLD = 0.85
PRICE_REL_TOL = 0.01
MAX_CANDIDATES_PER_HTML = 5000
EXCLUDE_DERIVED_ARTIFACT_HTML = True
ANNOTATION_POLICY = "labeled"
PARSER_CONTRACT_FILE = None
BENCHMARK_RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
# Optional CSV: dataset,page_id,html_sha256,ax_tree_file,screenshot_file.
# Relative artifact paths resolve against the CSV's parent. No live rendering or fetching.
ARTIFACT_MANIFEST = None
LLM_COMPARISON_DIR = BENCHMARK_ROOT / "baseline_results/llm/qwen38-omni-flash-v2"
HYBRID_LIMITS = dict(max_llm_stages=3, max_ax_chars=60000, max_candidates=8,
    max_candidate_chars=20000, max_candidate_total_chars=60000, max_image_bytes=10000000)
HYBRID_REQUIRED_FIELDS = {"name", "price", "product_url"}
HYBRID_OPTIONAL_FIELDS = {"old_price", "image_url", "in_stock", "delivery_time", "reviews_count"}
'''
    config += '\nMODEL_SNAPSHOT = ' + repr(json.loads((ROOT/'config/llm_model_snapshot.json').read_text(encoding='utf-8'))) + '\n'
    config += 'HYBRID_SOURCE_SNAPSHOT = ' + repr(snapshot) + '\n'
    config += 'HYBRID_CODE_SHA256 = ' + repr(hashlib.sha256((runtime+analysis+hybrid+reporting).encode()).hexdigest()) + '\n'
    config += 'SHARED_CONTRACT_SHA256 = ' + repr({str(i):hashlib.sha256(src[i].encode()).hexdigest() for i in [6,8,14,16]}) + '\n'
    cells=[]
    def add(kind, text, tag=None):
        cell=dict(cell_type=kind, metadata={'tags':[tag]} if tag else {}, source=text.splitlines(keepends=True),
                  id=hashlib.sha256((str(len(cells))+text).encode()).hexdigest()[:12])
        if kind=='code': cell.update(execution_count=None, outputs=[])
        cells.append(cell)
    add('markdown', '''# Hybrid snapshot baseline — original PriceTracker cascade

Original fast-renderer → completeness gate → original bounded AX / candidate / screenshot fallback.
Same RouterAI model as the LLM baseline. Default **plan** is offline and sends no requests.
Output and runtime: `baseline_results/hybrid/<EXPERIMENT_ID>/`, entirely separate from the LLM run.
No edits, cache writes or locks are made in either other baseline's directory.

This is the saved-snapshot extraction arm, not the full live L1–L9 application. Source discovery,
onboarding, navigation/Browser-use and DB persistence are outside this benchmark. Query filtering is
disabled because GT labels all page products. Missing AX/screenshot artifacts are explicitly reported;
HTML alone does not reproduce those modes. See HYBRID_BENCHMARK.md before using results in the article.
''')
    # Existing environment already contains these dependencies. No installation cell.
    add('markdown','Use the existing benchmark kernel (config/requirements-benchmark.lock.txt) and Node.js ≥24.\nNo extra Python packages, browser installation or original checkout are required.\n')
    add('code',src[2],'definitions')
    add('code',config,'configuration')
    add('code',src[6],'definitions')
    add('code',src[8],'definitions')
    add('markdown','## Frozen original deterministic parser\nOriginal sources, bundle and licenses are embedded verbatim.\n')
    add('code',src[10],'definitions')
    add('code',src[12],'definitions')
    add('code',runtime,'definitions')
    add('code',src[14],'definitions')
    exports=src[16].replace('original_parser_contract','extraction_contract').replace('"baseline_determined.ipynb"','"baseline_hybrid.ipynb"')
    exports=exports.replace('DISPLAY_NAMES = {','DISPLAY_NAMES = {\n    "hybrid_original": "Hybrid original snapshot cascade",')
    add('code',exports,'definitions')
    add('code',analysis,'definitions')
    add('markdown','## Hybrid routing, independent durable requests, costs and analytics\nThe original L8 method bodies are frozen; transport and explicit benchmark bounds are adapters.\nNo GT is supplied to routing or model calls. Screenshots use direct vision, with original URL rewriting.\n')
    add('code',hybrid,'definitions')
    add('code','REPORTING_CODE_SHA256 = '+repr(hashlib.sha256(reporting.encode()).hexdigest())+'\n'+reporting,'definitions')
    add('markdown','## Run\nFirst run the offline plan. Set RUN_MODE="run" for paid inference, or "replay" for saved responses only.\nKeep EXPERIMENT_ID/settings for resume. RETRY_ERRORS/RETRY_UNCERTAIN opt into retries; these can incur charges.\n')
    add('code','hybrid_result = hybrid_main()\n','main')
    (ROOT/'baseline_hybrid.ipynb').write_text(json.dumps(dict(cells=cells,metadata=original['metadata'],nbformat=4,nbformat_minor=5),ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    print('Built baseline_hybrid.ipynb (other notebooks unchanged).')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--refresh-original',type=Path)
    args=p.parse_args()
    if args.refresh_original: freeze_original(args.refresh_original)
    build()
