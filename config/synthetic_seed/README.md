# Frozen generation inputs

`catalog.csv` retains the original 100 v1 GT labels. `pages.json` assigns 12–30
products to each of the original 20 pages. `templates.json` contains the ten
original HTML layouts and their ten mutation blueprints (110 layouts in total),
with product values replaced by placeholders. They were captured from v1 HTML,
not inferred from parser predictions. These are generator inputs, not datasets.

The first five products on each page keep their labels and identities. Additional
products use the same category catalogue, with explicit series suffixes after
distinct names are exhausted and deterministic price variation. Both HTML and GT
are rendered from these labels, including JSON-LD and embedded state. Existing
mutation structure is preserved; mutations never change the product values.

Every repeated template has complementary sizes summing to 42 products, and each
category averages 21 products/page. Thus both page and entity category shares
remain 50/20/20/10 percent. Counts were fixed before evaluating either baseline.

Reproduce or verify with `python scripts/generate_synthetic_dataset.py --write`
or `python scripts/generate_synthetic_dataset.py`, respectively, from repository root.
