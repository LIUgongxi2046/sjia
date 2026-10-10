import { createServer } from 'vite';
import { writeFile } from 'node:fs/promises';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const schemas = module.checkPurposeSchemas();
  const report = { checked_at: new Date().toISOString(), status: 'PASS', scope: 'SCHEMA_COMPILATION', schemas,
    clinical_data_created: false, business_validation: 'NOT_RUN', visual_validation: 'NOT_RUN' };
  await writeFile('../.planning-work/t40-frontend.json', JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
} finally {
  await server.close();
}
