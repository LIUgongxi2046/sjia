import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  await server.ssrLoadModule('/src/api/archiveBatches.ts');
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const refs = ['BatchInput', 'BatchInputsView', 'BatchProcessInput', 'BatchActionIntentInput', 'BatchIntentInput',
    'BatchView', 'BatchList', 'PageView', 'PageAttemptView', 'RequestView', 'WriteReceipt'].map(name => `v2-protocol.schema.json#/$defs/${name}`);
  module.compileApiSchemas(refs);
  let responseCount = 0;
  for (const name of ['t42-simulation', 't42-simulation-inflight', 't42-simulation-worker',
    't42-simulation-inflight-raw', 't42-simulation-worker-raw']) {
    const result = JSON.parse(await readFile(`../.planning-work/${name}.json`, 'utf8'));
    assert.equal(result.mode, 'SIMULATION');
    if (name !== 't42-simulation') assert.equal(result.status, 'PASS');
    for (const response of result.responses) module.validateApiValue(`v2-protocol.schema.json#/$defs/${response.schema}`, response.value);
    responseCount += result.responses.length;
  }
  const paths = ['src/api/archiveBatches.ts', 'src/api/schema.ts', 'src/features/archives/UI2BatchPage.tsx',
    'src/app/App.tsx', '../backend-design/v2-protocol.schema.json', '../frontend-design/v2-types.ts'];
  const source_sha256 = Object.fromEntries(await Promise.all(paths.map(async path => [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
  await writeFile('../.planning-work/t42-frontend-schemas.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T42',
    checked_at: new Date().toISOString(), schemas: refs, actual_response_count: responseCount, source_sha256, visual_comparison: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${refs.length} 项批次 Schema 与 ${responseCount} 次实际响应\n`);
} finally { await server.close(); }
