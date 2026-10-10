import { createServer } from 'vite';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const sources = ['src/api/publication.ts', 'src/api/schema.ts', '../backend-design/publication.schema.json',
  '../backend-design/protocol.schema.json', 'scripts/check-publication-schemas.mjs'];
const hashes = Object.fromEntries(await Promise.all(sources.map(async path =>
  [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  await server.ssrLoadModule('/src/api/schema.ts');
  const client = await server.ssrLoadModule('/src/api/publication.ts');
  const refs = client.checkPublicationSchemas();
  const runtime = JSON.parse(await readFile('../.planning-work/t51-ui-runtime.json', 'utf8'));
  assert.equal(runtime.status, 'PASS');
  for (const route of runtime.routes) assert.equal(client.publicationTarget(route.operation, route.resource_id ?? undefined), route.target);
  const report = { checked_at: new Date().toISOString(), status: 'PASS', scope: 'PUBLICATION_CLIENT_AND_SCHEMA_COMPILATION', schemas: refs,
    routes_checked: runtime.routes.length, runtime_source: '.planning-work/t51-ui-runtime.json',
    source_sha256: hashes, business_writes: 0, positive_publication: 'NOT_RUN', visual: 'NOT_RUN' };
  await writeFile('../.planning-work/t51-frontend-schemas.json', JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ status: report.status, schemas: refs.length, report: '.planning-work/t51-frontend-schemas.json' }) + '\n');
} finally { await server.close(); }
