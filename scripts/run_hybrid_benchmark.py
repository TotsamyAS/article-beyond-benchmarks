"""Run the actual self-contained hybrid notebook; offline plan by default."""
import argparse
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def load_notebook():
    notebook=json.loads((ROOT/'baseline_hybrid.ipynb').read_text(encoding='utf-8'))
    ns={'__name__':'hybrid_notebook'}
    for i,cell in enumerate(notebook['cells']):
        if cell['cell_type']=='code' and set(cell.get('metadata',{}).get('tags',[])) & {'definitions','configuration'}:
            exec(compile(''.join(cell['source']),f'baseline_hybrid.ipynb:cell-{i+1}','exec'),ns)
    return ns


def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group()
    mode.add_argument('--plan',action='store_true');mode.add_argument('--run',action='store_true');mode.add_argument('--replay',action='store_true')
    p.add_argument('--datasets',nargs='+',choices=['synthetic','robustness','industrial'])
    p.add_argument('--experiment');p.add_argument('--limit',type=int);p.add_argument('--artifacts',type=Path)
    p.add_argument('--retry-errors',action='store_true');p.add_argument('--retry-uncertain',action='store_true')
    args=p.parse_args();os.chdir(ROOT);ns=load_notebook()
    ns.update(RUN_MODE='run' if args.run else 'replay' if args.replay else 'plan',
              RETRY_ERRORS=args.retry_errors,RETRY_UNCERTAIN=args.retry_uncertain)
    if args.datasets: ns['RUN_DATASETS']=tuple(args.datasets)
    if args.experiment: ns['EXPERIMENT_ID']=args.experiment
    if args.limit is not None: ns['PAGE_LIMIT']=args.limit
    if args.artifacts: ns['ARTIFACT_MANIFEST']=args.artifacts
    ns['hybrid_main']()


if __name__=='__main__': main()
