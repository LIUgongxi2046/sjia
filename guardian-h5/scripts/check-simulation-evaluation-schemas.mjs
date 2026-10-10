import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
try {
  for (const path of ['/src/api/evaluationDatasets.ts', '/src/api/evaluationRuns.ts']) await server.ssrLoadModule(path);
  const module = await server.ssrLoadModule('/src/api/schema.ts');
  const names = ['DatasetInput', 'DatasetView', 'DatasetList', 'DatasetQuery', 'PartitionInput', 'LabelInput', 'DatasetFreezeInput',
    'EvaluationIntentInput', 'EvaluationRetryInput', 'EvaluationRequestLookup', 'EvaluationRunInput', 'EvaluationRunView',
    'EvaluationDenominatorData', 'EvaluationUnitResult', 'EvaluationResultsView', 'EvaluationDenominatorsView',
    'EvaluationRunIntentInput', 'EvaluationRunRequestLookup', 'RequestView', 'WriteReceipt', 'TaskView'];
  const refs = names.map(name => `v2-protocol.schema.json#/$defs/${name}`); module.compileApiSchemas(refs);
  let responseCount = 0;
  for (const name of ['t48-simulation', 't48-boundaries', 't48-dataset-browser']) {
    const report = JSON.parse(await readFile(`../.planning-work/${name}.json`, 'utf8'));
    assert.equal(report.mode, 'SIMULATION'); assert.equal(report.status, 'PASS');
    for (const response of report.responses) module.validateApiValue(`v2-protocol.schema.json#/$defs/${response.schema}`, response.value);
    responseCount += report.responses.length;
  }
  const paths = ['src/api/evaluationDatasets.ts', 'src/api/evaluationRuns.ts', 'src/features/support/DatasetPage.tsx',
    'src/features/support/DatasetEditor.tsx', 'src/features/support/EvaluationRunPanel.tsx',
    '../backend-design/v2-protocol.schema.json', '../frontend-design/v2-types.ts',
    '../backend/case_details/evaluation_execution.py', '../backend/case_details/evaluation_runs.py'];
  const source_sha256 = Object.fromEntries(await Promise.all(paths.map(async path => [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
  await writeFile('../.planning-work/t48-frontend-schemas.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T48',
    checked_at: new Date().toISOString(), schemas: refs, actual_response_count: responseCount, source_sha256, visual_comparison: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${refs.length} 项 Schema 与 ${responseCount} 次实际响应\n`);
} finally { await server.close(); }
