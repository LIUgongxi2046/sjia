import { createServer } from 'vite';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';

const sources = ['src/api/assessments.ts', 'src/api/schema.ts', '../backend-design/v2-protocol.schema.json',
  '../frontend-design/v2-types.ts', 'scripts/check-assessment-schemas.mjs'];
const hashes = Object.fromEntries(await Promise.all(sources.map(async path =>
  [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const schemas = module.checkAssessmentSchemas();
  await server.ssrLoadModule('/src/api/assessments.ts');
  const report = { checked_at: new Date().toISOString(), status: 'PASS', scope: 'ASSESSMENT_SCHEMA_COMPILATION',
    schemas, source_sha256: hashes, business_writes: 0, positive_assessment: 'NOT_RUN', visual: 'NOT_RUN' };
  await writeFile('../.planning-work/t49-frontend-schemas.json', JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ status: report.status, schemas: schemas.length,
    report: '.planning-work/t49-frontend-schemas.json', positive_assessment: report.positive_assessment }) + '\n');
} finally { await server.close(); }
