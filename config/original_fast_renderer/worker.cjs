const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { performance } = require('node:perf_hooks');
const { extractProductsFromHtml, filterRowsByQueryRelevance } = require('./original_fast.cjs');
const [inputPath, outputPath, mode] = process.argv.slice(2);
const pages = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const completed = new Map();
if (mode === '--resume' && fs.existsSync(outputPath)) {
  const bytes = fs.readFileSync(outputPath, 'utf8');
  const lines = bytes.split('\n');
  if (lines.at(-1)) {
    // A killed process can leave a partial final JSON line; keep only complete records.
    fs.copyFileSync(outputPath, outputPath + '.interrupted');
    fs.writeFileSync(outputPath, lines.slice(0, -1).join('\n') + '\n', 'utf8');
  }
  for (const line of lines.slice(0, -1).filter(Boolean)) {
    const rec = JSON.parse(line);
    if (completed.has(rec.page_id)) throw new Error(`Duplicate checkpoint: ${rec.page_id}`);
    completed.set(rec.page_id, rec);
  }
} else fs.writeFileSync(outputPath, '', 'utf8');
console.log(`Original parser: ${completed.size}/${pages.length} saved checkpoints`);
for (const [index, page] of pages.entries()) {
  if (completed.has(page.page_id)) continue;
  const started = performance.now();
  let rec;
  try {
    const bytes = fs.readFileSync(page.html_path);
    const sha = crypto.createHash('sha256').update(bytes).digest('hex');
    if (sha !== page.content_sha256) throw new Error(`HTML changed after manifest creation: ${page.html_key}`);
    const readMs = performance.now() - started;
    const extractStarted = performance.now();
    const result = extractProductsFromHtml({
      html: bytes.toString('utf8'), pageUrl: page.page_url || page.source_url,
      source: { url: page.source_url || page.page_url, name: page.source },
      limit: 500, includeCandidates: false,
    });
    const extractMs = performance.now() - extractStarted;
    const queryStarted = performance.now();
    const queried = filterRowsByQueryRelevance(result.rows, page.query || undefined);
    rec = {page_id: page.page_id, html_key: page.html_key, content_sha256: sha,
      status: 'ok', html_read_ms: readMs, extract_ms: extractMs,
      query_filter_ms: performance.now() - queryStarted,
      elapsed_ms: performance.now() - started, result, queried};
  } catch (error) {
    rec = {page_id: page.page_id, html_key: page.html_key, status: 'error',
      elapsed_ms: performance.now() - started, error: String(error), stack: error?.stack};
  }
  fs.appendFileSync(outputPath, JSON.stringify(rec) + '\n', 'utf8');
  if ((index + 1) % 10 === 0 || index + 1 === pages.length) {
    console.log(`Original parser: ${index + 1}/${pages.length}; ${rec.status}`);
  }
}
