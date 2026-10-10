import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const runner = fileURLToPath(new URL('../../guardian-h5/tests/devices/check-h5-adaptation.mjs', import.meta.url));
const result = spawnSync(process.execPath, [runner], { stdio: 'inherit', env: process.env });
if (result.error) throw result.error;
if (result.status !== 0) throw new Error(`T30/T58 browser check exited with status ${result.status}`);
