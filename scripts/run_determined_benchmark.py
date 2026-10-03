"""Run the embedded original-parser notebook without Jupyter or any API calls."""
import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--datasets', nargs='+', choices=['synthetic', 'robustness', 'industrial'])
    args = parser.parse_args()
    os.chdir(ROOT)
    notebook = json.loads((ROOT / 'baseline_determined.ipynb').read_text(encoding='utf-8'))
    namespace = {'__name__': 'determined_notebook',
                 'display': lambda value: print(value.to_string(index=False) if hasattr(value, 'to_string') else value)}
    # Skip only the package-install cell. All definitions, embedded sources and
    # evaluation code come from the actual notebook, as does its final main cell.
    code = [(i, c) for i, c in enumerate(notebook['cells'])
            if c['cell_type'] == 'code' and not ''.join(c['source']).lstrip().startswith('%pip')]
    for i, cell in code[:-1]:
        exec(compile(''.join(cell['source']), f'baseline_determined.ipynb:cell-{i+1}', 'exec'), namespace)
    if args.datasets:
        namespace['RUN_DATASETS'] = tuple(args.datasets)
    i, cell = code[-1]
    exec(compile(''.join(cell['source']), f'baseline_determined.ipynb:cell-{i+1}', 'exec'), namespace)


if __name__ == '__main__':
    main()
