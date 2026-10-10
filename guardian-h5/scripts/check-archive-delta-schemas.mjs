import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  await server.ssrLoadModule('/src/api/archiveDelta.ts');
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const refs = ['Input', 'Source', 'Fact', 'Item', 'View', 'Intent', 'Request', 'Task'].map(name => `archive-delta.schema.json#/$defs/${name}`);
  refs.push('v2-protocol.schema.json#/$defs/DeltaInput', 'v2-protocol.schema.json#/$defs/DeltaView', 'v2-protocol.schema.json#/$defs/BatchList', 'v2-protocol.schema.json#/$defs/DocumentList');
  module.compileApiSchemas(refs);
  const result = JSON.parse(await readFile('../.planning-work/t46-simulation.json', 'utf8'));
  assert.equal(result.status, 'PASS'); assert.equal(result.mode, 'SIMULATION');
  for (const response of result.responses) module.validateApiValue(response.external_schema ?? `archive-delta.schema.json#/$defs/${response.schema}`, response.value);
  assert.throws(() => module.validateApiValue('archive-delta.schema.json#/$defs/Input', { baseline_batch_id: null, baseline_document_version_ids: [], clinical_level: 'SIMULATION' }));
  const paths = ['src/api/archiveDelta.ts', 'src/api/useDocumentSource.ts', 'src/api/schema.ts', 'src/features/archives/UI2DeltaPage.tsx',
    'src/features/archives/DocumentOriginalPage.tsx', 'src/features/records/UI2OriginalPage.tsx', '../backend-design/archive-delta.schema.json', '../frontend-design/archive-delta-types.ts'];
  const source_sha256 = Object.fromEntries(await Promise.all(paths.map(async path => [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
  await writeFile('../.planning-work/t46-frontend-schemas.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T46',
    checked_at: new Date().toISOString(), schemas: refs, actual_response_count: result.responses.length, source_sha256, visual_comparison: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${refs.length} 项摘要 Schema 与 ${result.responses.length} 次实际响应\n`);
} finally { await server.close(); }
