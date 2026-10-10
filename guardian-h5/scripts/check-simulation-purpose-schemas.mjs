import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  for (const path of ['/src/api/archiveUploads.ts', '/src/api/dispositions.ts', '/src/api/purpose.ts']) await server.ssrLoadModule(path);
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const names = ['ArchiveUploadOptions', 'UploadGroupReceipt', 'UploadGroupView', 'UploadFileReceipt', 'UploadError',
    'PurposeCopyInput', 'PurposeCopyView', 'PurposeCopyOptionsView', 'PurposeCopyEvidenceView', 'PurposeReviewInput', 'PurposeRevokeInput',
    'DispositionInput', 'DispositionOptions', 'DispositionView', 'DispositionApprovalInput', 'DispositionExecuteInput', 'DispositionRetainInput', 'DispositionRetryInput',
    'RequestView', 'WriteReceipt', 'TaskView'];
  const refs = names.map(name => `v2-protocol.schema.json#/$defs/${name}`); module.compileApiSchemas(refs);
  let responseCount = 0;
  for (const name of ['t40-simulation', 't40-upload-browser', 't40-model-page', 't40-disposition', 't40-rejections', 't40-disposition-browser', 't40-upload-recovery']) {
    const result = JSON.parse(await readFile(`../.planning-work/${name}.json`, 'utf8'));
    assert.equal(result.mode, 'SIMULATION'); assert.equal(result.status, 'PASS');
    for (const response of result.responses) module.validateApiValue(`v2-protocol.schema.json#/$defs/${response.schema}`, response.value);
    responseCount += result.responses.length;
  }
  const paths = ['src/api/archiveUploads.ts', 'src/api/dispositions.ts', 'src/api/purpose.ts', 'src/features/archives/ArchiveUpload.tsx',
    'src/features/account/DispositionTools.tsx', 'src/features/support/DispositionPanel.tsx', '../backend-design/v2-protocol.schema.json', '../frontend-design/v2-types.ts'];
  const source_sha256 = Object.fromEntries(await Promise.all(paths.map(async path => [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
  await writeFile('../.planning-work/t40-frontend-schemas.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T40',
    checked_at: new Date().toISOString(), schemas: refs, actual_response_count: responseCount, source_sha256, visual_comparison: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${refs.length} 项 Schema 与 ${responseCount} 次实际响应\n`);
} finally { await server.close(); }
