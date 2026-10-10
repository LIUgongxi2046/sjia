import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  await server.ssrLoadModule('/src/api/observationPeriods.ts');
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const refs = ['Input', 'Sampling', 'Interval', 'View', 'Intent', 'Request'].map(name => `observation-period.schema.json#/$defs/${name}`);
  refs.push('v2-protocol.schema.json#/$defs/ObservationPeriodInput', 'v2-protocol.schema.json#/$defs/SamplingInterval', 'v2-protocol.schema.json#/$defs/ObservationPeriodView');
  module.compileApiSchemas(refs);
  const result = JSON.parse(await readFile('../.planning-work/t47-simulation.json', 'utf8'));
  assert.equal(result.status, 'PASS'); assert.equal(result.mode, 'SIMULATION');
  for (const response of result.responses) module.validateApiValue(`observation-period.schema.json#/$defs/${response.schema}`, response.value);
  assert.throws(() => module.validateApiValue('observation-period.schema.json#/$defs/Input', {
    expected_version: null, start: null, end: null, label: null, episode_id: null, reason: 'SIMULATION', clinical_level: 'SIMULATION',
  }));
  const paths = ['src/api/observationPeriods.ts', 'src/api/schema.ts', 'src/features/records/UI2ObservationPeriodPage.tsx',
    'src/app/App.tsx', '../backend-design/observation-period.schema.json', '../frontend-design/observation-period-types.ts'];
  const source_sha256 = Object.fromEntries(await Promise.all(paths.map(async path => [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
  await writeFile('../.planning-work/t47-frontend-schemas.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T47',
    checked_at: new Date().toISOString(), schemas: refs, actual_response_count: result.responses.length, source_sha256, visual_comparison: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${refs.length} 项观察范围 Schema 与 ${result.responses.length} 次实际响应\n`);
} finally { await server.close(); }
