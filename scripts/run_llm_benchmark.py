"""Execute the actual LLM notebook without Jupyter; default is an offline plan."""
import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_notebook():
    notebook = json.loads((ROOT / 'baseline_llm.ipynb').read_text(encoding='utf-8'))
    ns = {'__name__': 'llm_notebook'}
    for i, cell in enumerate(notebook['cells']):
        tags = cell.get('metadata', {}).get('tags', [])
        if cell['cell_type'] == 'code' and ('definitions' in tags or 'configuration' in tags):
            exec(compile(''.join(cell['source']), f'baseline_llm.ipynb:cell-{i + 1}', 'exec'), ns)
    return ns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run', action='store_true', help='Issue paid requests for unfinished pages')
    mode.add_argument('--replay', action='store_true', help='Rebuild outputs from saved responses; no network')
    mode.add_argument('--plan', action='store_true', help='Offline input audit, the default')
    parser.add_argument('--datasets', nargs='+', choices=['synthetic', 'robustness', 'industrial'])
    parser.add_argument('--experiment', help='Stable output/checkpoint directory name')
    parser.add_argument('--limit', type=int, help='First N available pages per dataset; use a separate experiment')
    parser.add_argument('--retry-errors', action='store_true', help='Explicitly retry previously failed responses')
    parser.add_argument('--retry-uncertain', action='store_true', help='Explicitly retry interrupted requests with unknown billing')
    args = parser.parse_args()
    os.chdir(ROOT)
    ns = load_notebook()
    ns.update(RUN_MODE='run' if args.run else 'replay' if args.replay else 'plan',
              RUN_DATASETS=tuple(args.datasets) if args.datasets else None,
              RETRY_ERRORS=args.retry_errors, RETRY_UNCERTAIN=args.retry_uncertain)
    if args.experiment:
        ns['EXPERIMENT_ID'] = args.experiment
    if args.limit is not None:
        ns['PAGE_LIMIT'] = args.limit
    ns['llm_main']()


if __name__ == '__main__':
    main()
