# Frozen original fast-renderer assets

These files are the externalized payloads used by `baseline_determined.ipynb` and `baseline_hybrid.ipynb`.

- `provenance.json` — frozen parser provenance and SHA-256 fingerprints.
- `original_fast.cjs.zlib` — the same zlib-compressed CommonJS bundle that was previously Base85-embedded in the notebooks.
- `worker.cjs` — the original benchmark worker.
- `THIRD_PARTY_LICENSES.json` — frozen dependency license metadata/text.

The notebooks verify the decompressed bundle and worker against the SHA-256 values in `provenance.json` before execution. No network or npm lookup is introduced by this change.
