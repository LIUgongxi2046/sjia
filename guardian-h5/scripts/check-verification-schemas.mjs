import { createServer } from 'vite';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';

const sources = ['src/api/verifications.ts', 'src/api/schema.ts', '../backend-design/v2-protocol.schema.json',
  '../frontend-design/v2-types.ts', 'src/api/assessments.ts', 'scripts/check-verification-schemas.mjs'];
const hashes = Object.fromEntries(await Promise.all(sources.map(async path =>
  [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const schemas = module.checkVerificationSchemas();
  const assessmentSchemas = module.checkAssessmentSchemas();
  await server.ssrLoadModule('/src/api/verifications.ts');
  await server.ssrLoadModule('/src/api/assessments.ts');
  const report = { checked_at: new Date().toISOString(), status: 'PASS', scope: 'VERIFICATION_SCHEMA_COMPILATION',
    schemas, assessment_schemas: assessmentSchemas, source_sha256: hashes, business_writes: 0, positive_verification: 'NOT_RUN', visual: 'NOT_RUN' };
  await writeFile('../.planning-work/t49-verification-schemas.json', JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ status: report.status, schemas: schemas.length,
    report: '.planning-work/t49-verification-schemas.json', positive_verification: report.positive_verification }) + '\n');
} finally { await server.close(); }
