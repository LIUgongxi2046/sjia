import { createServer } from 'vite';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  await server.ssrLoadModule('/src/api/archiveSearch.ts');
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const refs = ['ArchiveSearchInput', 'SourcePage', 'Episode', 'Snippet', 'Item', 'ArchiveSearchView', 'Authorization', 'Copy']
    .map(name => `archive-search.schema.json#/$defs/${name}`);
  module.compileApiSchemas(refs);
  const result = JSON.parse(await readFile('../.planning-work/t45-simulation.json', 'utf8'));
  assert.equal(result.mode, 'SIMULATION'); assert.equal(result.status, 'PASS');
  for (const response of result.responses) module.validateApiValue('archive-search.schema.json#/$defs/ArchiveSearchView', response);
  module.validateApiValue('archive-search.schema.json#/$defs/ArchiveSearchInput', { query: '已确认原文', from_date: '2026-10-05' });
  assert.throws(() => module.validateApiValue('archive-search.schema.json#/$defs/ArchiveSearchInput', { patient_id: crypto.randomUUID() }));
  const paths = ['src/api/archiveSearch.ts', 'src/api/useArchiveSearch.ts', 'src/api/client.ts', 'src/api/schema.ts', '../backend-design/archive-search.schema.json',
    '../frontend-design/archive-search-types.ts', 'src/features/archives/UI2ArchivePage.tsx', 'src/features/archives/DocumentOriginalPage.tsx', 'src/features/archives/ArchiveOriginals.tsx',
    '../backend/archives/search.py', '../backend/archives/search_schemas.py', '../backend/api/views_archive_search.py', '../backend/files/originals.py', '../backend/core/fields.py', '../backend/archives/migrations/0007_archive_search_indexes.py'];
  const source_sha256 = Object.fromEntries(await Promise.all(paths.map(async path => [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
  await writeFile('../.planning-work/t45-frontend-schemas.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', captured_at: new Date().toISOString(),
    schemas: refs, actual_response_count: result.responses.length, source_sha256, business_writes: 0, visual_comparison: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${refs.length} 项目录 Schema 与 ${result.responses.length} 次实际查询响应\n`);
} finally { await server.close(); }
