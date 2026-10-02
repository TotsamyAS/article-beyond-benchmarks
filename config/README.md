# Environment and model configuration

- `requirements-benchmark.lock.txt`: exact package versions used for the reference
  benchmark environment (Python 3.13.13). Install with
  `python -m pip install -r config/requirements-benchmark.lock.txt` from the root.
- `requirements.txt`: the original minimum-dependency specification. Use the lock
  file for paper reproduction rather than resolving these lower bounds afresh.
- `llm_model_snapshot.json`: public RouterAI catalog snapshot used by the notebook
  builder and offline planner. Paid experiments save their own startup snapshot.
- `env.example`: an empty API-key template. Actual credentials belong in the ignored
  root `.env` or in the process environment; no credentials are stored here.

Notebook experiment parameters are intentionally visible in each notebook's
configuration cell. The deterministic notebook embeds its original JS dependencies;
Node.js 24.6.0 is the reference runtime and no npm install is needed.
