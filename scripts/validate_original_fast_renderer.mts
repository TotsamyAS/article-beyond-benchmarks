/** Offline replay of the original, unmodified PriceTracker TypeScript modules. */
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { performance } from "node:perf_hooks";

const [repository, inputPath, outputPath, mode] = process.argv.slice(2);
const source = path.join(repository, "services", "fast-renderer", "src");
const { extractProductsFromHtml } = await import(pathToFileURL(path.join(source, "extractor.ts")).href);
const { filterRowsByQueryRelevance } = await import(pathToFileURL(path.join(source, "queryRelevance.ts")).href);
const pages = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const completed = new Set<string>();
if (mode === "--resume" && fs.existsSync(outputPath)) {
  for (const line of fs.readFileSync(outputPath, "utf8").split(/\r?\n/).filter(Boolean)) {
    const record = JSON.parse(line);
    if (completed.has(record.page_id)) throw new Error(`Duplicate checkpoint: ${record.page_id}`);
    completed.add(record.page_id);
  }
} else {
  fs.writeFileSync(outputPath, "", "utf8");
}
console.log(`Original fast-renderer: resuming with ${completed.size}/${pages.length} completed pages`);
for (const [index, page] of pages.entries()) {
  if (completed.has(page.page_id)) continue;
  const started = performance.now();
  let record;
  try {
    const html = fs.readFileSync(page.html_path, "utf8");
    const result = extractProductsFromHtml({
      html, pageUrl: page.page_url || page.source_url,
      source: { url: page.source_url || page.page_url, name: page.source },
      limit: 500, includeCandidates: false,
    });
    // The original extractor calls this same pure filter after extracting rows.
    const queried = filterRowsByQueryRelevance(result.rows, page.query || undefined);
    record = { page_id: page.page_id, html_key: page.html_key, status: "ok",
      elapsed_ms: performance.now() - started, result, queried };
  } catch (error) {
    record = { page_id: page.page_id, html_key: page.html_key, status: "error",
      elapsed_ms: performance.now() - started, error: String(error), stack: error?.stack };
  }
  fs.appendFileSync(outputPath, JSON.stringify(record) + "\n", "utf8");
  if ((index + 1) % 10 === 0 || index + 1 === pages.length) {
    console.log(`Original fast-renderer: ${index + 1}/${pages.length}; ${record.status}`);
  }
}
