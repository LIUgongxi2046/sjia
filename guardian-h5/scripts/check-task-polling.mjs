import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { parseAst } from 'rolldown/parseAst';
import { createServer } from 'vite';

const { values } = parseArgs({ options: { source: { type: 'string' }, report: { type: 'string' } } });
assert.ok(values.source && values.report, '需要真实响应文件和全新输出路径。');
const paths = ['src/api/summaries.ts', 'src/features/archives/UI2BatchPage.tsx'];
const invalidDefaults = [];

function visit(node, action) {
  if (node === null || typeof node !== 'object') return;
  if (typeof node.type === 'string') action(node);
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) for (const child of value) visit(child, action);
    else if (value && typeof value === 'object') visit(value, action);
  }
}

for (const path of paths) {
  const program = parseAst(await readFile(path, 'utf8'), { lang: path.endsWith('.tsx') ? 'tsx' : 'ts' }, path);
  visit(program, node => {
    if (node.operator !== '??') return;
    const left = node.left.type === 'ChainExpression' ? node.left.expression : node.left;
    if (left.type === 'MemberExpression' && left.property.name === 'poll_after_ms') invalidDefaults.push(path);
  });
}
assert.deepEqual(invalidDefaults, [], '任务查询需要服务端提供合法间隔，不能采用默认间隔。');

const content = await readFile(values.source);
const evidence = JSON.parse(content);
assert.equal(evidence.result, 'PASS');
assert.equal(evidence.source, 'ACTUAL_HTTP');
assert.equal(evidence.new_model_calls, 0);
assert.ok(evidence.batches.length > 0);
const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
let measured;
try {
  const { requiredTaskPollingInterval, canContinueTaskPolling, automaticPollingBudgetMs } = await server.ssrLoadModule('/src/api/taskPolling.ts');
  const { batchPollingInterval } = await server.ssrLoadModule('/src/api/archiveBatches.ts');
  const schema = await server.ssrLoadModule('/src/api/schema.ts');
  assert.equal(automaticPollingBudgetMs, 5 * 60_000);
  let positive = 0, absent = 0, stopped = 0, historicalBudgetChecks = 0, pageCount = 0;
  const original = JSON.stringify(evidence.batches);
  for (const batch of evidence.batches) {
    schema.validateApiValue('v2-protocol.schema.json#/$defs/BatchView', batch);
    pageCount += batch.pages.length;
    const active = batch.pages.filter(page => ['queued', 'processing', 'cancel_requested'].includes(page.status));
    if (active.length === 0) {
      assert.equal(batchPollingInterval(batch), null);
      stopped += 1;
    } else assert.equal(batchPollingInterval(batch), Math.max(...active.map(page => requiredTaskPollingInterval(page.task?.poll_after_ms))));
    for (const page of batch.pages) {
      const interval = page.task?.poll_after_ms;
      if (interval === null || interval === undefined) {
        assert.throws(() => requiredTaskPollingInterval(interval), /合法查询间隔/);
        absent += 1;
      } else {
        assert.equal(requiredTaskPollingInterval(interval), interval);
        const started = performance.now();
        assert.equal(canContinueTaskPolling(performance.now() - started, interval), true);
        const elapsed = Date.parse(evidence.checked_at) - Date.parse(page.task.accepted_at);
        assert.ok(elapsed > automaticPollingBudgetMs);
        assert.equal(canContinueTaskPolling(elapsed, interval), false);
        historicalBudgetChecks += 1;
        positive += 1;
      }
    }
  }
  assert.ok(positive > 0 && absent > 0);
  assert.equal(JSON.stringify(evidence.batches), original);
  measured = { status: 'PASS', checked_at: new Date().toISOString(), source_sha256: createHash('sha256').update(content).digest('hex'),
    batches: evidence.batches.length, pages: pageCount, positive_intervals: positive, absent_intervals: absent,
    stopped_batches: stopped, historical_elapsed_budget_checks: historicalBudgetChecks,
    automatic_polling_budget_ms: automaticPollingBudgetMs, clinical_rows_inserted: 0, provider_called: false,
    visual_validation: 'NOT_RUN', full_browser_validation: 'NOT_RUN' };
} finally {
  await server.close();
}
await writeFile(resolve(values.report), JSON.stringify(measured, null, 2), { flag: 'wx', mode: 0o600 });
process.stdout.write(JSON.stringify(measured) + '\n');
