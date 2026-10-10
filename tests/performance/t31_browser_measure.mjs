import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';
import { parseArgs } from 'node:util';
import process from 'node:process';
import { performance } from 'node:perf_hooks';

const root = resolve(import.meta.dirname, '../..');
const frontendRoot = join(root, 'guardian-h5');
const workRoot = join(root, '.planning-work/parallel/window4/browser-runtime');
const reportPath = join(root, 'docs/testing/T31-browser-authenticated-simulation-report.json');
const require = createRequire(join(frontendRoot, 'package.json'));
const { chromium } = require('playwright');
const { values } = parseArgs({
  options: {
    iterations: { type: 'string', default: '1' },
    port: { type: 'string', default: '18418' },
    'api-target': { type: 'string', default: 'http://127.0.0.1:8001' },
    report: { type: 'string', default: reportPath },
  },
});
const iterations = Number(values.iterations);
const port = Number(values.port);
const apiTarget = new URL(values['api-target']);
const username = process.env.SJIA_SIMULATION_USERNAME ?? 'simulation-family';
const password = process.env.SJIA_SIMULATION_PASSWORD;
if (!Number.isInteger(iterations) || iterations < 1 || iterations > 20) {
  throw new RangeError('--iterations 必须为 1 到 20 的整数。');
}
if (!Number.isInteger(port) || port < 10000 || port > 65535) {
  throw new RangeError('--port 必须为 10000 到 65535 的独立端口。');
}
if (apiTarget.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(apiTarget.hostname)
  || apiTarget.port !== '8001' || apiTarget.pathname !== '/' || apiTarget.search || apiTarget.hash) {
  throw new Error('--api-target 只允许本机主窗口模拟 API 的根地址，默认端口 8001。');
}
if (username !== 'simulation-family' || !password) {
  throw new Error('必须使用环境变量提供模拟账号 simulation-family 的密码。');
}

await mkdir(workRoot, { recursive: true });
process.env.TMPDIR = workRoot;
const origin = `http://127.0.0.1:${port}`;
const viteEntry = join(frontendRoot, 'node_modules/vite/bin/vite.js');
let preview;
let browser;
const requestStartedAt = new WeakMap();
const requestDurations = new WeakMap();

function percentile(samples, key, fraction) {
  if (samples.length < 20) return null;
  const sorted = samples.map((sample) => sample[key]).filter(Number.isFinite).sort((a, b) => a - b);
  if (!sorted.length) return null;
  return sorted[Math.max(0, Math.min(sorted.length - 1, Math.ceil(sorted.length * fraction) - 1))];
}

function summarize(samples) {
  const mean = (key) => {
    const rows = samples.map((sample) => sample[key]).filter(Number.isFinite);
    return rows.length ? rows.reduce((total, value) => total + value, 0) / rows.length : null;
  };
  return {
    samples: samples.length,
    successes: samples.filter((sample) => sample.success).length,
    failures: samples.filter((sample) => !sample.success).length,
    failure_rate: samples.length ? samples.filter((sample) => !sample.success).length / samples.length : 0,
    navigation_elapsed_ms: samples.map((sample) => sample.elapsed_ms),
    api_search_elapsed_ms: samples.map((sample) => sample.api_search_elapsed_ms),
    login_flow_elapsed_ms: samples.map((sample) => sample.login_flow_elapsed_ms ?? null),
    navigation_elapsed_p95_ms: percentile(samples, 'elapsed_ms', 0.95),
    navigation_elapsed_p99_ms: percentile(samples, 'elapsed_ms', 0.99),
    api_search_elapsed_p95_ms: percentile(samples, 'api_search_elapsed_ms', 0.95),
    api_search_elapsed_p99_ms: percentile(samples, 'api_search_elapsed_ms', 0.99),
    dom_content_loaded_ms: samples.map((sample) => sample.dom_content_loaded_ms),
    load_event_ms: samples.map((sample) => sample.load_event_ms),
    dom_content_loaded_p95_ms: percentile(samples, 'dom_content_loaded_ms', 0.95),
    dom_content_loaded_p99_ms: percentile(samples, 'dom_content_loaded_ms', 0.99),
    load_event_p95_ms: percentile(samples, 'load_event_ms', 0.95),
    load_event_p99_ms: percentile(samples, 'load_event_ms', 0.99),
    resource_transfer_bytes: samples.map((sample) => sample.resource_transfer_bytes),
    resource_transfer_bytes_p95: percentile(samples, 'resource_transfer_bytes', 0.95),
    resource_transfer_bytes_p99: percentile(samples, 'resource_transfer_bytes', 0.99),
    mean_resource_count: mean('resource_count'),
    mean_resource_transfer_bytes: mean('resource_transfer_bytes'),
    mean_resource_encoded_bytes: mean('resource_encoded_bytes'),
    long_task_count: samples.reduce((total, sample) => total + sample.long_task_count, 0),
    long_task_total_ms: samples.reduce((total, sample) => total + sample.long_task_total_ms, 0),
    long_task_max_ms: samples.reduce((maximum, sample) => Math.max(maximum, sample.long_task_max_ms), 0),
    js_heap_used_bytes_mean: mean('js_heap_used_bytes'),
  };
}

async function startPreview() {
  preview = spawn(process.execPath, [viteEntry, 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: frontendRoot,
    env: { ...process.env, SJIA_API_TARGET: apiTarget.origin },
    stdio: ['ignore', 'pipe', 'inherit'],
  });
  await new Promise((resolveReady, rejectReady) => {
    const timeout = setTimeout(() => rejectReady(new Error('Vite preview 在启动期限内未报告本地地址。')), 15000);
    preview.stdout.on('data', (chunk) => {
      if (chunk.toString().includes(origin)) {
        clearTimeout(timeout);
        resolveReady();
      }
    });
    preview.once('exit', (code) => {
      clearTimeout(timeout);
      rejectReady(new Error(`Vite preview 提前退出，状态码 ${code}。`));
    });
  });
}

async function readThroughUi(page, mode) {
  let response;
  let apiDuration;
  if (mode === 'first_visit') {
    const loginStarted = performance.now();
    await page.goto(`${origin}/login`, { waitUntil: 'load', timeout: 30000 });
    await page.getByRole('textbox', { name: '账号' }).fill(username);
    await page.locator('input[name="password"]').fill(password);
    await page.getByRole('button', { name: '登录账号' }).click();
    await page.waitForURL('**/home', { timeout: 30000 });
    const loginFlowElapsedMs = performance.now() - loginStarted;
    const identity = await page.evaluate(async () => {
      const [sessionResponse, relationResponse] = await Promise.all([
        fetch('/api/session', { credentials: 'same-origin', cache: 'no-store' }),
        fetch('/api/relations', { credentials: 'same-origin', cache: 'no-store' }),
      ]);
      if (!sessionResponse.ok || !relationResponse.ok) throw new Error('已登录会话或关联资料读取失败。');
      return { session: await sessionResponse.json(), relationList: await relationResponse.json() };
    });
    if (identity.session.account_status !== 'active' || !identity.session.principal_id) {
      throw new Error('真实 API 返回的模拟账号会话没有处于 active 状态。');
    }
    const now = Date.now();
    const activeRelation = identity.relationList.relations.find((relation) => relation.status === 'active'
      && Date.parse(relation.valid_from) <= now
      && (!relation.valid_until || Date.parse(relation.valid_until) > now));
    if (!activeRelation) {
      throw new Error('真实 API 返回的模拟账号没有当前有效患儿关联。');
    }
    const searchPromise = page.waitForResponse((candidate) => {
      const url = new URL(candidate.url());
      return url.origin === origin && url.pathname.startsWith('/api/v2/patients/')
        && url.pathname.endsWith('/archives/search') && candidate.request().method() === 'POST';
    }, { timeout: 30000 });
    const navigationStarted = performance.now();
    const navigation = await page.goto(`${origin}/archives`, { waitUntil: 'load', timeout: 30000 });
    response = await searchPromise;
    apiDuration = requestDurations.get(response.request());
    if (!Number.isFinite(apiDuration)) throw new Error('首访档案 API 请求没有有效的本机单调时钟计时。');
    await page.getByText(/符合条件 \d+ 份文书/).waitFor({ timeout: 30000 });
    const body = await response.body();
    const payload = JSON.parse(body.toString('utf8'));
    const renderedCount = await page.locator('section[aria-label$="文书"]').count();
    if (response.status() !== 200 || !Array.isArray(payload.items) || payload.items.length !== renderedCount) {
      throw new Error('首访真实档案搜索响应与页面呈现数量不一致。');
    }
    if (new URL(response.url()).pathname.split('/')[4] !== activeRelation.patient_id) {
      throw new Error('首访档案搜索请求患儿与真实授权关联不一致。');
    }
    return {
      success: navigation?.status() === 200 && response.status() === 200,
      http_status: navigation?.status() ?? 0,
      elapsed_ms: performance.now() - navigationStarted,
      api_search_elapsed_ms: apiDuration,
      api_search_status: response.status(),
      matched_count: payload.matched_count,
      item_count: payload.items.length,
      rendered_document_count: renderedCount,
      response_bytes: body.byteLength,
      response_sha256: createHash('sha256').update(body).digest('hex'),
      saved_items_sha256: createHash('sha256').update(JSON.stringify(payload.items)).digest('hex'),
      patient_reference_matches_authorized_relation: true,
      principal_present: true,
      login_flow_elapsed_ms: loginFlowElapsedMs,
    };
  }

  const searchPromise = page.waitForResponse((candidate) => {
    const url = new URL(candidate.url());
    return url.origin === origin && url.pathname.startsWith('/api/v2/patients/')
      && url.pathname.endsWith('/archives/search') && candidate.request().method() === 'POST';
  }, { timeout: 30000 });
  const navigationStarted = performance.now();
  const navigation = await page.reload({ waitUntil: 'load', timeout: 30000 });
  response = await searchPromise;
  apiDuration = requestDurations.get(response.request());
  if (!Number.isFinite(apiDuration)) throw new Error('回访档案 API 请求没有有效的本机单调时钟计时。');
  await page.getByText(/符合条件 \d+ 份文书/).waitFor({ timeout: 30000 });
  const body = await response.body();
  const payload = JSON.parse(body.toString('utf8'));
  const renderedCount = await page.locator('section[aria-label$="文书"]').count();
  if (response.status() !== 200 || !Array.isArray(payload.items) || payload.items.length !== renderedCount) {
    throw new Error('回访真实档案搜索响应与页面呈现数量不一致。');
  }
  return {
    success: navigation?.status() === 200 && response.status() === 200,
    http_status: navigation?.status() ?? 0,
    elapsed_ms: performance.now() - navigationStarted,
    api_search_elapsed_ms: apiDuration,
    api_search_status: response.status(),
    matched_count: payload.matched_count,
    item_count: payload.items.length,
    rendered_document_count: renderedCount,
    response_bytes: body.byteLength,
    response_sha256: createHash('sha256').update(body).digest('hex'),
    saved_items_sha256: createHash('sha256').update(JSON.stringify(payload.items)).digest('hex'),
  };
}

async function captureRuntime(page, visit, failedRequests) {
  await page.waitForTimeout(250);
  const browserMetrics = await page.evaluate(() => {
    const navigation = performance.getEntriesByType('navigation')[0];
    const resources = performance.getEntriesByType('resource');
    const tasks = window.__window4LongTasks ?? [];
    const heap = performance.memory?.usedJSHeapSize ?? null;
    return {
      dom_content_loaded_ms: navigation?.domContentLoadedEventEnd ?? null,
      load_event_ms: navigation?.loadEventEnd ?? null,
      resource_count: resources.length,
      resource_transfer_bytes: resources.reduce((total, item) => total + (item.transferSize ?? 0), 0),
      resource_encoded_bytes: resources.reduce((total, item) => total + (item.encodedBodySize ?? 0), 0),
      api_resource_count: resources.filter((item) => new URL(item.name).pathname.startsWith('/api/')).length,
      long_task_count: tasks.length,
      long_task_total_ms: tasks.reduce((total, duration) => total + duration, 0),
      long_task_max_ms: tasks.length ? Math.max(...tasks) : 0,
      js_heap_used_bytes: Number.isFinite(heap) ? heap : null,
    };
  });
  return { ...visit, ...browserMetrics, failed_resource_requests: failedRequests };
}

try {
  await startPreview();
  browser = await chromium.launch({
    headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  });
  const blocked = { off_origin_requests: 0 };
  const firstVisits = [];
  const repeatVisits = [];
  const pageErrors = [];
  const apiResponses = [];

  for (let index = 0; index < iterations; index += 1) {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
    await context.addInitScript(() => {
      window.__window4LongTasks = [];
      if ('PerformanceObserver' in window && PerformanceObserver.supportedEntryTypes?.includes('longtask')) {
        new PerformanceObserver((list) => {
          window.__window4LongTasks.push(...list.getEntries().map((entry) => entry.duration));
        }).observe({ type: 'longtask', buffered: true });
      }
    });
    const page = await context.newPage();
    let failedRequests = 0;
    page.on('request', (request) => requestStartedAt.set(request, performance.now()));
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('requestfailed', (request) => {
      const requestUrl = new URL(request.url());
      if (requestUrl.origin === origin && !requestUrl.pathname.startsWith('/api/')) failedRequests += 1;
    });
    page.on('response', (response) => {
      const requestStart = requestStartedAt.get(response.request());
      if (requestStart !== undefined) requestDurations.set(response.request(), performance.now() - requestStart);
      const responseUrl = new URL(response.url());
      if (responseUrl.origin === origin && responseUrl.pathname.startsWith('/api/')) {
        apiResponses.push({ method: response.request().method(), path: responseUrl.pathname,
          status: response.status(), content_length: Number(response.headers()['content-length'] ?? 0) });
      }
    });
    await page.route('**/*', (route) => {
      const requestUrl = new URL(route.request().url());
      if (requestUrl.origin !== origin) {
        blocked.off_origin_requests += 1;
        return route.abort();
      }
      return route.continue();
    });

    const first = await readThroughUi(page, 'first_visit');
    firstVisits.push(await captureRuntime(page, first, failedRequests));
    const repeat = await readThroughUi(page, 'repeat_visit');
    repeatVisits.push(await captureRuntime(page, repeat, failedRequests));
    if (first.saved_items_sha256 !== repeat.saved_items_sha256
      || first.item_count !== repeat.item_count || first.matched_count !== repeat.matched_count) {
      throw new Error('真实模拟档案记录在首次读取与回访读取之间发生变化。');
    }
    await context.close();
  }

  const archiveSearchResponses = apiResponses.filter((entry) => entry.path.endsWith('/archives/search'));
  const loginResponses = apiResponses.filter((entry) => entry.method === 'POST' && entry.path.endsWith('/api/auth/login'));
  const report = {
    task: 'T31',
    classification: 'SIMULATION_AUTHENTICATED_H5_MEASUREMENT',
    measured_at: new Date().toISOString(),
    runtime: {
      node: process.version,
      playwright: require('playwright/package.json').version,
      browser: 'Google Chrome headless',
      browser_version: browser.version(),
      viewport: { width: 390, height: 844, device_scale_factor: 1 },
      screenshots: 0,
      visual_inspection: 'NOT_RUN',
    },
    target: {
      simulation_target: true,
      origin,
      api_target: apiTarget.origin,
      database: 'sjia_simulation',
      username,
      iterations,
      first_visit_contexts: iterations,
      repeat_visits: iterations,
      api_requests_are_live: true,
      external_requests: 0,
      browser_profile_temp_root: workRoot,
    },
    browser_network: { off_origin_requests_blocked: blocked.off_origin_requests, intercepted_api_requests: 0, mocked_responses: 0 },
    api: {
      response_count: apiResponses.length,
      status_counts: Object.fromEntries([...new Set(apiResponses.map((entry) => entry.status))]
        .map((status) => [String(status), apiResponses.filter((entry) => entry.status === status).length])),
      login_request_count: loginResponses.length,
      login_statuses: loginResponses.map((entry) => entry.status),
      archive_search_requests: archiveSearchResponses,
      archive_search_count: archiveSearchResponses.length,
      saved_record_readback: {
        first_visit_count: firstVisits[0].item_count,
        repeat_visit_count: repeatVisits[0].item_count,
        saved_items_sha256_equal: firstVisits[0].saved_items_sha256 === repeatVisits[0].saved_items_sha256,
        first_visit_response_sha256: firstVisits[0].response_sha256,
        repeat_visit_response_sha256: repeatVisits[0].response_sha256,
        saved_items_sha256: firstVisits[0].saved_items_sha256,
        rendered_item_count_matches_api: firstVisits.every((visit) => visit.rendered_document_count === visit.item_count)
          && repeatVisits.every((visit) => visit.rendered_document_count === visit.item_count),
      },
    },
    sample_policy: { p95_p99_reported_only_when_sample_count_at_least_20: true },
    first_visit: summarize(firstVisits),
    repeat_visit: summarize(repeatVisits),
    javascript_page_errors: { count: pageErrors.length, messages: [...new Set(pageErrors)].slice(0, 10) },
    saved_and_reread: true,
    scope: { simulated_data_only: true, real_capacity: 'NOT_RUN', clinical_scale: 'NOT_RUN' },
  };
  if (loginResponses.length !== iterations || loginResponses.some((entry) => entry.status !== 200)
    || archiveSearchResponses.length !== iterations * 2 || blocked.off_origin_requests !== 0
    || report.api.saved_record_readback.saved_items_sha256_equal !== true
    || report.api.saved_record_readback.rendered_item_count_matches_api !== true) {
    throw new Error('实际 API 请求、模拟档案回读或浏览器外网限制检查未通过。');
  }
  const outputPath = resolve(values.report);
  await mkdir(resolve(outputPath, '..'), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`);
  const reread = JSON.parse(await readFile(outputPath, 'utf8'));
  if (JSON.stringify(reread) !== JSON.stringify(report)) throw new Error('浏览器性能报告保存后回读不一致。');
  process.stdout.write(`${JSON.stringify(report)}\n`);
} finally {
  if (browser) await browser.close();
  if (preview && preview.exitCode === null) {
    preview.kill('SIGTERM');
    await once(preview, 'exit');
  }
}
