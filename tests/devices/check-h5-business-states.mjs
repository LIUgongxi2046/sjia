import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const script = fileURLToPath(new URL('../../guardian-h5/tests/devices/check-h5-business-states.mjs', import.meta.url));
const result = spawnSync(process.execPath, [script], { stdio: 'inherit', env: process.env });
if (result.error) throw result.error;
if (result.signal) throw new Error(`浏览器检查进程被信号终止：${result.signal}`);
if (result.status !== 0) process.exit(result.status ?? 1);
