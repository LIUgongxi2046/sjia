import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { copyFile, mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import { resolve, relative, extname } from 'node:path';

const root = resolve('..');
const destination = resolve(root, 'docs/testing/evidence/QA03');
const sources = {
  'QA03/readonly-interactions/results.json': 'readonly-results.json',
  'QA03/readonly-interactions.log': 'readonly.log',
  'QA03/alert-existing/results.json': 'alert-results.json',
  'QA03/alert-existing.log': 'alert.log',
  'QA03/ui-updated/inspection.json': 'ui-inspection.json',
  'QA03/ui-inspection-updated.log': 'ui-inspection.log',
  'design-check.json': 'design-check.json',
  'QA03/design-check.log': 'design-check.log',
  'http-security.json': 'http-security.json',
  'QA03/http-security.log': 'http-security.log',
  'QA03/frontend-build.log': 'build.log',
  'QA03/django-check.log': 'django-check.log',
  'QA03/dependency-check.log': 'dependency-check.log',
  'QA03/recognition-inputs.json': 'recognition-inputs.json',
  'QA03/recognition-inputs.log': 'recognition-inputs.log',
  'QA03/existing-files.json': 'existing-files.json',
  'QA03/existing-files.log': 'existing-files.log',
  'QA03/backend-tests.log': 'backend-tests.log',
  'QA03/local.json': 'local-business.json',
  'QA03/local.log': 'local-business.log',
  'QA03/photos.json': 'photos.json',
  'QA03/photos.log': 'photos.log',
  'QA03/voice-ui.json': 'voice-ui.json',
  'QA03/voice-ui.log': 'voice-ui.log',
  'QA03/voice-evals.json': 'voice-evals.json',
  'QA03/voice-evals.log': 'voice-evals.log',
  'QA03/ocr-evals.json': 'ocr-evals.json',
  'QA03/ocr-evals.log': 'ocr-evals.log',
  'QA03/recognition-jobs.json': 'recognition-jobs.json',
  'QA03/recognition-jobs.log': 'recognition-jobs.log',
  'QA03/job-failures.json': 'job-failures.json',
  'QA03/job-failures.log': 'job-failures.log',
  'QA03/trends.json': 'trends.json',
  'QA03/trends.log': 'trends.log',
};
await mkdir(resolve(destination, 'screens'), { recursive: true });
for (const [source, target] of Object.entries(sources)) await copyFile(resolve(root, '.qa-work', source), resolve(destination, target));
for (const file of await readdir(resolve(root, '.qa-work/QA03/readonly-interactions'))) {
  if (file.endsWith('.png')) await copyFile(resolve(root, '.qa-work/QA03/readonly-interactions', file), resolve(destination, 'screens', file));
}
await copyFile(resolve(root, '.qa-work/QA03/alert-existing/S11-B.png'), resolve(destination, 'screens/S11-B.png'));
await mkdir(resolve(destination, 'ocr-inputs'), { recursive: true });
for (const file of await readdir(resolve(root, '.qa-work/QA03/ocr-inputs'))) await copyFile(resolve(root, '.qa-work/QA03/ocr-inputs', file), resolve(destination, 'ocr-inputs', file));
const readJson = async name => JSON.parse(await readFile(resolve(destination, name), 'utf8'));
const readonly = await readJson('readonly-results.json');
const alert = await readJson('alert-results.json');
const design = await readJson('design-check.json');
const http = await readJson('http-security.json');
const layout = await readJson('ui-inspection.json');
const recognition = await readJson('recognition-inputs.json');
const existingFiles = await readJson('existing-files.json');
const expanded = {};
for (const [name, count] of Object.entries({ 'local-business': 11, photos: 5, 'voice-ui': 4, 'voice-evals': 50,
  'ocr-evals': 10, 'recognition-jobs': 5, 'job-failures': 5, trends: 2 })) {
  const result = await readJson(`${name}.json`);
  assert.equal(result.results.length, count, name);
  assert.ok(result.results.every(item => item.status === 'PASS'), name);
  expanded[name] = count;
}
assert.match(await readFile(resolve(destination, 'backend-tests.log'), 'utf8'), /Ran 258 tests[\s\S]*\nOK\n/);
assert.equal(recognition.results.length, 35);
assert.ok(recognition.results.every(item => item.status === 'PASS'));
assert.equal(existingFiles.results.length, 13);
assert.ok(existingFiles.results.every(item => item.status === 'PASS'));
assert.equal(readonly.results.length, 99);
assert.ok(readonly.results.every(item => item.status === 'PASS'));
assert.deepEqual(readonly.mutations, []);
assert.deepEqual(readonly.errors, []);
assert.equal(alert.status, 'PASS');
assert.ok(design.results.every(item => item.status === 'PASS'));
assert.ok(http.results.every(item => item.status === 'PASS'));
assert.equal(layout.results.length, 17);
assert.deepEqual(layout.errors, []);
assert.match(await readFile(resolve(destination, 'build.log'), 'utf8'), /built in/);
assert.match(await readFile(resolve(destination, 'django-check.log'), 'utf8'), /no issues/);
assert.match(await readFile(resolve(destination, 'dependency-check.log'), 'utf8'), /No broken requirements/);
const files = [];
async function collect(directory, extensions = null) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (['.venv', '__pycache__'].includes(entry.name)) continue;
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) await collect(path, extensions);
    else if (entry.isFile() && (!extensions || extensions.includes(extname(path)))) files.push(path);
  }
}
await collect(resolve(root, 'backend'), ['.py']);
await collect(resolve(root, 'guardian-h5/src'));
await collect(resolve(root, 'guardian-h5/scripts'), ['.mjs']);
await collect(resolve(root, 'guardian-h5/dist'));
await collect(destination);
for (const path of ['backend/requirements.txt', 'backend/requirements.lock.txt', 'guardian-h5/package.json', 'guardian-h5/package-lock.json', 'docs/testing/QA03-report.md', 'docs/testing/QA03-test-matrix.md', 'docs/development/QA03-comprehensive-retest.md']) files.push(resolve(root, path));
const entries = [];
for (const path of [...new Set(files)].sort()) {
  if (path === resolve(destination, 'manifest.json')) continue;
  const bytes = await readFile(path);
  entries.push({ path: relative(root, path), bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') });
}
await writeFile(resolve(destination, 'manifest.json'), JSON.stringify({
  generatedAt: new Date().toISOString(), decision: 'NO_GO',
  inputScope: 'user-authorized isolated synthetic accounts, cases and non-patient images; actual Ark responses; existing local readonly evidence',
  environment: { frontend: 'http://127.0.0.1:5174', backend: 'http://127.0.0.1:8001', browser: layout.browser, node: process.version, database: 'PostgreSQL/sjia_qa03/5433', unitTestDatabase: 'test_sjia_dev', redisDatabase: 3, readonlyFrontend: 'http://127.0.0.1:5173' },
  checks: { backend: 258, ...expanded, readonly: readonly.results.length, existingAlert: alert.checks.length, anonymousUI: design.results.length, anonymousHTTP: http.results.length, layoutPages: layout.results.length, recognitionInputs: recognition.results.length, existingFiles: existingFiles.results.length },
  outstanding: ['real mobile devices and recording hardware', 'clinical Gold and professional approval', 'actual notification delivery, capacity and production recovery', 'actual Provider throttling, network timeout and service interruption'],
  files: entries,
}, null, 2));
console.log(JSON.stringify({ destination, files: entries.length, status: 'PASS' }));
