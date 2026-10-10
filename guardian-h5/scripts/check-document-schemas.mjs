import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  await server.ssrLoadModule('/src/api/archiveDocuments.ts');
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const names = ['DocumentInput', 'GroupingInput', 'DocumentView', 'DocumentList', 'DocumentInputsView', 'DocumentEpisodeOption',
    'DocumentIntentInput', 'DocumentRequestLookup', 'ProposedDocumentMetadata', 'ProposedEpisode', 'RequestView', 'WriteReceipt', 'FactView', 'FactList', 'FactCorrectionInput'];
  const refs = names.map(name => `v2-protocol.schema.json#/$defs/${name}`);
  module.compileApiSchemas(refs);
  const results = await Promise.all(['t43-simulation', 't43-simulation-episodes', 't43-simulation-browser', 't43-simulation-page-order', 't43-simulation-fact-episode'].map(async name => JSON.parse(await readFile(`../.planning-work/${name}.json`, 'utf8'))));
  let responseCount = 0;
  for (const result of results) {
    assert.equal(result.mode, 'SIMULATION'); assert.equal(result.status, 'PASS');
    for (const response of result.responses) module.validateApiValue(`v2-protocol.schema.json#/$defs/${response.schema}`, response.value);
    responseCount += result.responses.length;
  }
  const paths = ['src/api/archiveDocuments.ts', 'src/features/archives/UI2DocumentCreatePage.tsx',
    'src/features/archives/UI2GroupingPage.tsx', '../backend/archives/grouping.py', '../backend/api/views_document_inputs.py',
    '../backend/requests/archive_documents.py', '../backend/archives/fact_writes.py', '../backend-design/v2-protocol.schema.json', '../frontend-design/v2-types.ts'];
  const source_sha256 = Object.fromEntries(await Promise.all(paths.map(async path => [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
  await writeFile('../.planning-work/t43-frontend-schemas.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T43', checked_at: new Date().toISOString(),
    schemas: refs, actual_response_count: responseCount, source_sha256, visual_comparison: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${refs.length} 项文书 Schema 与 ${responseCount} 次实际响应\n`);
} finally { await server.close(); }
