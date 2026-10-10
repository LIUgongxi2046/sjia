import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  await server.ssrLoadModule('/src/api/archiveFacts.ts');
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const names = ['FactCandidate', 'FactCandidateSet', 'FactDecision', 'FactConfirmationInput', 'FactCorrectionInput', 'FactIntentInput',
    'FactView', 'FactList', 'PageOriginalView', 'PageView', 'RequestView', 'WriteReceipt'];
  const refs = names.map(name => `v2-protocol.schema.json#/$defs/${name}`);
  module.compileApiSchemas(refs);
  const results = await Promise.all(['t41-simulation', 't41-simulation-browser'].map(async name => JSON.parse(await readFile(`../.planning-work/${name}.json`, 'utf8'))));
  let responseCount = 0;
  for (const result of results) {
    assert.equal(result.mode, 'SIMULATION'); assert.equal(result.status, 'PASS');
    for (const response of result.responses) module.validateApiValue(`v2-protocol.schema.json#/$defs/${response.schema}`, response.value);
    responseCount += result.responses.length;
  }
  const paths = ['src/api/archiveFacts.ts', 'src/api/schema.ts', 'src/features/archives/UI2ConfirmPage.tsx',
    'src/features/archives/FactFieldsEditor.tsx', 'src/features/archives/PageOriginalPage.tsx',
    '../backend-design/v2-protocol.schema.json', '../frontend-design/v2-types.ts'];
  const source_sha256 = Object.fromEntries(await Promise.all(paths.map(async path => [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
  await writeFile('../.planning-work/t41-frontend-schemas.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T41',
    checked_at: new Date().toISOString(), schemas: refs, actual_response_count: responseCount, source_sha256, visual_comparison: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${refs.length} 项事实 Schema 与 ${responseCount} 次实际响应\n`);
} finally { await server.close(); }
