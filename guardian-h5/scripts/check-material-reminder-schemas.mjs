import { createServer } from 'vite';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';

const sources = ['src/api/schema.ts', 'src/api/materialReminders.ts', 'src/api/personalRecordTasks.ts',
  'src/api/materialNotices.ts', 'src/features/account/UI2RemindersPage.tsx',
  'src/features/account/PersonalRecordTaskTools.tsx', 'src/features/account/MaterialReminderNotices.tsx',
  '../backend-design/personal-record-task.schema.json', '../backend-design/material-reminder-notice.schema.json',
  '../backend-design/v2-protocol.schema.json', '../backend-design/protocol.schema.json',
  '../backend-design/notification.schema.json', 'scripts/check-material-reminder-schemas.mjs'];
const hashes = Object.fromEntries(await Promise.all(sources.map(async path =>
  [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  const api = await server.ssrLoadModule('/src/api/schema.ts');
  await server.ssrLoadModule('/src/api/personalRecordTasks.ts');
  await server.ssrLoadModule('/src/api/materialNotices.ts');
  await server.ssrLoadModule('/src/api/materialReminders.ts');
  const refs = ['protocol.schema.json#/$defs/IntentView'];
  for (const name of ['personal-record-task', 'material-reminder-notice']) {
    const schema = JSON.parse(await readFile(`../backend-design/${name}.schema.json`, 'utf8'));
    refs.push(...Object.keys(schema.$defs).map(definition => `${name}.schema.json#/$defs/${definition}`));
  }
  refs.push(...['ReminderInput', 'ReminderChangeInput', 'ReminderTargetQuery', 'ReminderTargetView', 'ReminderTargetList',
    'ReminderView', 'ReminderList', 'ReminderIntentInput', 'ReminderRequestLookup', 'WriteReceipt', 'RequestView']
    .map(name => `v2-protocol.schema.json#/$defs/${name}`));
  api.compileApiSchemas(refs);
  const report = { checked_at: new Date().toISOString(), status: 'PASS', schemas: refs,
    source_sha256: hashes, business_writes: 0, positive_reminder: 'NOT_RUN', visual: 'NOT_RUN' };
  await writeFile('../.planning-work/t53-frontend-schemas.json', JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ status: 'PASS', schemas: refs.length, report: '.planning-work/t53-frontend-schemas.json' }) + '\n');
} finally { await server.close(); }
