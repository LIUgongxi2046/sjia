import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';
import { parseAst } from 'rolldown/parseAst';

const { values } = parseArgs({ options: { report: { type: 'string' } } });
assert.ok(values.report, '需要全新输出路径。');
const filename = 'src/api/fileValidation.ts';
const content = await readFile(filename, 'utf8');
const program = parseAst(content, { lang: 'ts' }, filename);
const declarations = program.body.filter(node => node.type === 'ExportNamedDeclaration'
  && node.declaration?.type === 'FunctionDeclaration').map(node => node.declaration);
assert.ok(declarations.some(node => node.id.name === 'completeValidatedFileGroup'), '原件校验入口需要存在。');

function descendants(node) {
  if (node === null || typeof node !== 'object') return [];
  const result = typeof node.type === 'string' ? [node] : [];
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) result.push(...value.flatMap(descendants));
    else if (value && typeof value === 'object') result.push(...descendants(value));
  }
  return result;
}

const loops = declarations.flatMap(node => descendants(node.body)).filter(node => node.type === 'WhileStatement');
assert.ok(loops.length > 0);
for (const loop of loops) {
  assert.ok(descendants(loop.body).some(node => node.type === 'CallExpression'
    && node.callee.type === 'Identifier' && node.callee.name === 'canContinueTaskPolling'),
  '原件校验的自动查询需要采用完整预算检查。');
}
const report = { status: 'PASS', checked_at: new Date().toISOString(), kind: 'STATIC',
  requirement: 'FRONT V2 前台自动查询上限五分钟', source: filename,
  source_sha256: createHash('sha256').update(content).digest('hex'), bounded_loops: loops.length,
  business_validation: 'NOT_RUN', visual_validation: 'NOT_RUN' };
await writeFile(values.report, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
process.stdout.write(JSON.stringify(report) + '\n');
