import { createServer } from 'vite';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const sources = ['src/api/notifications.ts', 'src/api/schema.ts', '../backend-design/notification.schema.json',
  '../backend-design/protocol.schema.json', 'scripts/check-notification-schemas.mjs'];
const hashes = Object.fromEntries(await Promise.all(sources.map(async path =>
  [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  const client = await server.ssrLoadModule('/src/api/notifications.ts');
  const refs = client.checkNotificationSchemas();
  const runtime = JSON.parse(await readFile('../.planning-work/t52-delivery-runtime.json', 'utf8'));
  assert.equal(runtime.status, 'PASS');
  const report = { checked_at: new Date().toISOString(), status: 'PASS', scope: 'NOTIFICATION_CLIENT_AND_SCHEMA_COMPILATION',
    schemas: refs, source_sha256: hashes, business_writes: 0, positive_notification: 'NOT_RUN', visual: 'NOT_RUN' };
  await writeFile('../.planning-work/t52-frontend-schemas.json', JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ status: report.status, schemas: refs.length, report: '.planning-work/t52-frontend-schemas.json' }) + '\n');
} finally { await server.close(); }
