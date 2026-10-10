import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { copyFile, mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import { resolve, relative, extname } from 'node:path';

const root = resolve('..');
const destination = resolve(root, 'docs/testing/evidence/QA02');
const artifacts = {
  'qa02-all-tests.log': 'backend-tests.log',
  'qa02-local.json': 'local-business.json',
  'qa02-local.log': 'local-business.log',
  'qa02-photos.json': 'photos.json',
  'qa02-photos.log': 'photos.log',
  'design-check.json': 'design-check.json',
  'http-security.json': 'http-security.json',
  'qa02-build.log': 'build.log',
  'qa02-django-check.log': 'django-check.log',
  'qa02-migrations.log': 'migrations.log',
  'simulated-report.png': 'simulated-report.png',
};
await mkdir(destination, { recursive: true });
for (const [source, target] of Object.entries(artifacts)) await copyFile(resolve(root, '.qa-work', source), resolve(destination, target));
const backendLog = await readFile(resolve(destination, 'backend-tests.log'), 'utf8');
assert.match(backendLog, /Ran 244 tests/);
assert.match(backendLog, /\nOK\n/);
const local = JSON.parse(await readFile(resolve(destination, 'local-business.json'), 'utf8'));
const photos = JSON.parse(await readFile(resolve(destination, 'photos.json'), 'utf8'));
assert.equal(local.results.length, 11);
assert.equal(photos.results.length, 5);
assert.ok([...local.results, ...photos.results].every(result => result.status === 'PASS'));
const files = [];
async function collect(directory, extensions = null) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.name === '.venv' || entry.name === '__pycache__') continue;
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) await collect(path, extensions);
    else if (entry.isFile() && (!extensions || extensions.includes(extname(path)))) files.push(path);
  }
}
await collect(resolve(root, 'backend'), ['.py']);
await collect(resolve(root, 'guardian-h5/src'));
await collect(resolve(root, 'guardian-h5/scripts'), ['.mjs', '.py']);
await collect(resolve(root, 'guardian-h5/dist'));
await collect(destination);
for (const file of ['guardian-h5/package.json', 'guardian-h5/package-lock.json', 'guardian-h5/vite.config.ts', 'planning/delivery-ledger.json', 'docs/testing/QA02-report.md', 'docs/testing/acceptance-matrix.md', 'docs/development/QA02-local-completion.md']) files.push(resolve(root, file));
const entries = [];
for (const path of [...new Set(files)].sort()) {
  if (path === resolve(destination, 'manifest.json')) continue;
  const bytes = await readFile(path);
  entries.push({ path: relative(root, path), bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') });
}
await writeFile(resolve(destination, 'manifest.json'), JSON.stringify({
  generatedAt: new Date().toISOString(), dataMode: 'SIMULATION', inputAuthorization: '2026-10-04用户授权本地模拟资料',
  environment: { frontend: local.baseURL, backend: 'http://127.0.0.1:8000', database: 'PostgreSQL/sjia_dev/5433', broker: 'Redis/6379', node: process.version, visual: 'NOT_RUN' },
  counts: { backend: 244, localBusiness: 11, photos: 5, anonymousDOM: 190, httpSecurity: 106 },
  businessStartedAt: [local.startedAt, photos.startedAt].sort()[0], businessFinishedAt: [local.finishedAt, photos.finishedAt].sort().at(-1),
  boundaries: ['模拟资料验收', '真实患者与临床Gold未验收', '实际设备与生产环境未验收'], files: entries,
}, null, 2));
console.log(JSON.stringify({ files: entries.length, destination, status: 'PASS' }));
