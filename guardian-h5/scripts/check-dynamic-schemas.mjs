import { createServer } from 'vite';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';

const sources = ['src/api/dynamic.ts', 'src/api/schema.ts', '../backend-design/v2-protocol.schema.json',
  '../alert-design/node.schema.json', '../frontend-design/v2-types.ts', 'scripts/check-dynamic-schemas.mjs'];
const hashes = Object.fromEntries(await Promise.all(sources.map(async path =>
  [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const schemas = [...module.checkDynamicSchemas(), ...module.checkAssessmentSchemas()];
  await server.ssrLoadModule('/src/api/dynamic.ts');
  await server.ssrLoadModule('/src/api/assessments.ts');
  const report = { checked_at: new Date().toISOString(), status: 'PASS', scope: 'DYNAMIC_AND_AFFECTED_STATIC_SCHEMA_COMPILATION',
    schemas, source_sha256: hashes, business_writes: 0, positive_dynamic: 'NOT_RUN', visual: 'NOT_RUN' };
  await writeFile('../.planning-work/t50-frontend-schemas.json', JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ status: report.status, schemas: schemas.length,
    report: '.planning-work/t50-frontend-schemas.json', positive_dynamic: report.positive_dynamic }) + '\n');
} finally { await server.close(); }
