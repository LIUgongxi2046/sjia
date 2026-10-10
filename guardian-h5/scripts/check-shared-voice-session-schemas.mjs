import { createServer } from 'vite';
import { readFile, writeFile } from 'node:fs/promises';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const voiceSchema = JSON.parse(await readFile('../backend-design/voice-input.schema.json', 'utf8'));
  const refs = Object.keys(voiceSchema.$defs).map(name => `voice-input.schema.json#/$defs/${name}`);
  refs.push('protocol.schema.json#/$defs/SessionView', 'protocol.schema.json#/$defs/PatientSelectionInput',
    'protocol.schema.json#/$defs/PatientSelectionView', 'protocol.schema.json#/$defs/TaskView',
    'protocol.schema.json#/$defs/HomeView', 'v2-protocol.schema.json#/$defs/TaskView');
  module.compileApiSchemas(refs);
  const report = { status: 'PASS', checked_at: new Date().toISOString(), scope: 'SCHEMA_COMPILATION',
    schemas: refs, business_validation: 'NOT_RUN', visual_validation: 'NOT_RUN' };
  const target = '../.planning-work/coordination/root/voice-session-schemas.json';
  await writeFile(target, JSON.stringify(report, null, 2));
  if (JSON.stringify(JSON.parse(await readFile(target, 'utf8'))) !== JSON.stringify(report)) {
    throw new Error('接口 Schema 检查报告保存重读不一致');
  }
  process.stdout.write(JSON.stringify(report) + '\n');
} finally {
  await server.close();
}
