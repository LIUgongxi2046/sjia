import { createServer } from 'vite';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  await server.ssrLoadModule('/src/api/archiveRelations.ts');
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const refs = ['RelationInput', 'RelationChangeInput', 'RelationVerificationInput', 'RelationIntentInput',
    'RelationRequestLookup', 'RelationSource', 'RelationCandidate', 'RelationDirectory', 'RelationRequestView']
    .map(name => `archive-relations.schema.json#/$defs/${name}`);
  module.compileApiSchemas(refs);
  const manifest = JSON.parse(await readFile('../.simulation-work/t44-fixture.json', 'utf8'));
  assert.equal(manifest.mode, 'SIMULATION');
  const identity = { operation_id: 'archive_relation_create', target_key: `/patients/${manifest.patient_id}/archive-relations`,
    request_id: crypto.randomUUID(), schema_version: '2.0.0', payload: { relation_type: 'same_bytes',
      left_fact_version_id: null, right_fact_version_id: null, left_file_id: manifest.files[0], right_file_id: manifest.files[1],
      status: 'proposed', evidence_ref: manifest.evidence_ref, reason: 'SIMULATION资料版本确认' } };
  module.validateApiValue('archive-relations.schema.json#/$defs/RelationIntentInput', identity);
  module.validateApiValue('archive-relations.schema.json#/$defs/RelationInput', identity.payload);
  assert.throws(() => module.validateApiValue('archive-relations.schema.json#/$defs/RelationInput', { ...identity.payload, verification_id: crypto.randomUUID() }));
  const paths = ['src/api/archiveRelations.ts', 'src/api/schema.ts', '../backend-design/archive-relations.schema.json',
    '../frontend-design/archive-relations-types.ts', 'src/features/archives/UI2DuplicatesPage.tsx', 'src/features/support/RelationVerificationTools.tsx'];
  const source_sha256 = Object.fromEntries(await Promise.all(paths.map(async path => [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
  await writeFile('../.planning-work/t44-frontend-schemas.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS',
    captured_at: new Date().toISOString(), schemas: refs, source_sha256, business_writes: 0, visual_comparison: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${refs.length} 项资料关系 Schema 和客户端模块\n`);
} finally { await server.close(); }
