import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { createServer } from 'node:net';
import { chromium } from 'playwright';

export async function openRealBrowser({ port, headless = false }) {
  assert.ok(Number.isInteger(port) && port > 1024 && port < 65536);
  await new Promise((resolve, reject) => {
    const probe = createServer(); probe.once('error', reject);
    probe.listen(port, '127.0.0.1', () => probe.close(error => error ? reject(error) : resolve()));
  });
  const marker = '--sjia-local-validation=' + randomUUID();
  const server = await chromium.launchServer({ channel: 'chrome', headless, host: '127.0.0.1',
    args: ['--enable-automation', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=' + port,
      '--disk-cache-size=8388608', '--window-size=430,900', marker] });
  let ready = false;
  try {
    const browser = await chromium.connectOverCDP('http://127.0.0.1:' + port, { noDefaults: true, isLocal: true, timeout: 10000 });
    const session = await browser.newBrowserCDPSession();
    const command = await session.send('Browser.getBrowserCommandLine');
    assert.ok(command.arguments.includes(marker));
    assert.ok(command.arguments.includes('--remote-debugging-port=' + port));
    const contexts = browser.contexts(); assert.equal(contexts.length, 1);
    ready = true;
    return { browser, context: contexts[0], session, pid: server.process().pid,
      noDefaults: true, close: () => server.close() };
  } finally { if (!ready) await server.close(); }
}
