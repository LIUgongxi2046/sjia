#!/usr/bin/env node
/**
 * Offline production-presentation comparison. Node/jsdom only; never a browser.
 *
 * Usage (from guardian-h5):
 *   node scripts/render-core-visual-comparison.mjs [--baseline PATH] [--out PATH]
 *
 * Loads real TSX/CSS from the candidate and frozen baseline, mounts React into
 * jsdom, captures UI2Frame's actual ShadowRoot, and exports script-free HTML.
 * The only mocked production modules are useAccess and useRecordResource; both
 * are scoped to this process, synthetic fixtures, and exact read identities.
 * No app/session storage, credentials, database, server, or endpoint is used.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { registerHooks, syncBuiltinESMExports } from 'node:module';
import { basename, dirname, relative, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import tls from 'node:tls';
import dgram from 'node:dgram';
import { transformSync } from 'rolldown/utils';
import { JSDOM, ResourceLoader, VirtualConsole } from 'jsdom';

const candidate = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const workspace = resolve(candidate, '../..');
const args = process.argv.slice(2);
const option = (name, fallback) => {
  const index = args.indexOf(name);
  if (index < 0) return fallback;
  assert.ok(args[index + 1] && !args[index + 1].startsWith('--'), `${name} needs a path`);
  return resolve(args[index + 1]);
};
for (let i = 0; i < args.length; i += 2) assert.ok(['--baseline', '--out'].includes(args[i]), `Unknown option ${args[i]}`);
const baseline = option('--baseline', resolve(workspace, 'sjia-polish-recovery-20261013/source-0439f5d6'));
const out = option('--out', resolve(candidate, '../offline-comparison'));
assert.notEqual(candidate, baseline, 'Baseline must be a separate read-only source tree');
assert.ok(existsSync(resolve(baseline, 'guardian-h5/src/components/UI2Frame.tsx')), 'Missing baseline source');
assert.ok(!out.startsWith(`${candidate}${sep}`) && !out.startsWith(`${baseline}${sep}`), 'Output must be outside both source trees');
mkdirSync(resolve(out, 'assets'), { recursive: true });
const BOUNDARY = '合成界面样本，非真实业务结果；静态展示，未执行保存/授权/临床评估';
const LIMIT = 'Node/jsdom 静态导出，不是截图、当前登录界面或浏览器视觉验收。S03/S08/S12-B 仅为工作参照，母版尚未最终确认。';
const digest = value => createHash('sha256').update(value).digest('hex');
const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const fileDigest = path => digest(readFileSync(path));
const branchFor = path => [candidate, baseline].find(root => path.startsWith(`${root}${sep}`));
const dependencyRoot = realpathSync(resolve(candidate, 'guardian-h5/node_modules'));
const sourceRootFor = path => branchFor(path) ?? (path.startsWith(`${dependencyRoot}${sep}`) ? dependencyRoot : null);
const sourceLabelFor = path => branchFor(path) === candidate ? 'candidate' : branchFor(path) === baseline ? 'baseline' : 'dependency';
const sourceFiles = new Map(), assetFiles = new Map(), cssFiles = new Map();
const recordSource = path => {
  const sha256 = fileDigest(path);
  if (sourceFiles.has(path)) assert.equal(sourceFiles.get(path), sha256, `Source changed mid-render: ${path}`);
  sourceFiles.set(path, sha256);
};
function copyAsset(path, role = 'production-asset') {
  assert.ok(existsSync(path), `Missing local asset: ${path}`);
  const root = branchFor(path);
  assert.ok(root, `Asset outside approved source trees: ${path}`);
  const bytes = readFileSync(path), sha256 = digest(bytes);
  const name = `${sha256.slice(0, 20)}-${basename(path)}`;
  const href = `assets/${name}`;
  writeFileSync(resolve(out, href), bytes);
  assert.equal(fileDigest(resolve(out, href)), sha256, 'Copied asset bytes changed');
  assetFiles.set(`${role}:${path}`, { role, source: relative(root, path), branch: root === candidate ? 'candidate' : 'baseline', href, sha256, bytes: bytes.length });
  return href;
}
function resolveAsset(value, sourcePath) {
  if (/^(?:data:|#)/.test(value)) return value;
  assert.ok(!/^(?:https?:|\/\/|blob:)/i.test(value), `Remote asset prohibited: ${value}`);
  const root = branchFor(sourcePath);
  assert.ok(root, `No source branch for ${sourcePath}`);
  const path = value.startsWith('/assets/') ? resolve(root, 'guardian-h5/public', `.${value}`)
    : value.startsWith('file:') ? fileURLToPath(value) : resolve(dirname(sourcePath), value);
  return copyAsset(path);
}
function loadCss(path, stack = []) {
  assert.ok(!stack.includes(path), 'Cyclic CSS import');
  recordSource(path);
  const raw = readFileSync(path, 'utf8');
  cssFiles.set(path, { branch: sourceLabelFor(path), source: relative(sourceRootFor(path), path), sha256: digest(raw), bytes: Buffer.byteLength(raw) });
  return raw.replace(/@import\s+(?:url\()?['"]([^'"]+)['"]\)?\s*;/g, (_, target) => {
    assert.ok(!/^(?:https?:|\/\/)/i.test(target), 'Remote CSS import prohibited');
    return loadCss(resolve(dirname(path), target), [...stack, path]);
  }).replace(/url\(\s*(['"]?)([^)'"\s]+)\1\s*\)/g, (_, _quote, target) => `url("${resolveAsset(target, path)}")`);
}

let networkAttempts = 0;
const deniedNetwork = target => { networkAttempts++; throw new Error(`OFFLINE HARNESS: network forbidden (${String(target)})`); };
for (const [module, names] of [[http, ['request', 'get']], [https, ['request', 'get']], [net, ['connect', 'createConnection']], [tls, ['connect']], [dgram, ['createSocket']]]) {
  for (const name of names) module[name] = (...params) => deniedNetwork(`${name}:${params[0]}`);
}
net.Socket.prototype.connect = (...params) => deniedNetwork(`Socket.connect:${params[0]}`);
syncBuiltinESMExports();
globalThis.fetch = (...params) => deniedNetwork(`fetch:${params[0]}`);
class NoNetworkResourceLoader extends ResourceLoader { fetch(url) { return deniedNetwork(`jsdom:${url}`); } }
const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM('<!doctype html><div id="root"></div>', {
  url: 'https://synthetic.invalid/', resources: new NoNetworkResourceLoader(), virtualConsole,
});
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLButtonElement', 'Node', 'ShadowRoot']) {
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
}
window.fetch = globalThis.fetch;
window.XMLHttpRequest.prototype.open = (...params) => deniedNetwork(`XMLHttpRequest:${params[1]}`);
window.WebSocket = class { constructor(url) { deniedNetwork(`WebSocket:${url}`); } };
window.EventSource = class { constructor(url) { deniedNetwork(`EventSource:${url}`); } };
globalThis.WebSocket = window.WebSocket;
globalThis.EventSource = window.EventSource;
Object.defineProperty(navigator, 'sendBeacon', { value: (...params) => deniedNetwork(`sendBeacon:${params[0]}`) });
let storageWrites = 0;
for (const name of ['setItem', 'removeItem', 'clear']) window.Storage.prototype[name] = () => {
  storageWrites++; throw new Error('OFFLINE HARNESS: persistent storage mutation forbidden');
};
const storageGet = window.Storage.prototype.getItem;
window.Storage.prototype.getItem = function (key) {
  assert.equal(key, 'sjia.ui.theme', 'Only the production appearance preference may be read');
  return storageGet.call(this, key);
};
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let readCalls = 0, currentFixture = null;
const patientId = 'synthetic-patient-offline-only';
const denyAction = () => { throw new Error('OFFLINE HARNESS: business actions are not executable'); };
globalThis.__offlineVisualAccess = Object.freeze({
  principalId: 'synthetic-principal-offline-only', patientId, scopeVersion: 'synthetic-1', loading: false, error: null,
  relations: [{ patient_id: patientId, display_id: '合成患儿', relation_id: 'synthetic-relation-offline-only', status: 'active' }],
  hasCurrentAccess: () => true, refresh: denyAction, selectPatient: denyAction,
});
globalThis.__offlineVisualRead = (path, requestedPatient, schema, id, key) => {
  readCalls++;
  assert.equal(currentFixture?.type, 'record', 'Only record fixtures may call the isolated read hook');
  assert.equal(path, `/api/records/${currentFixture.value.record_id}`);
  assert.equal(requestedPatient, patientId); assert.equal(schema, 'RecordView');
  assert.equal(id, currentFixture.value.record_id); assert.equal(key, 'record_id');
  return { data: currentFixture.value, loading: false, error: null, reload: denyAction };
};
registerHooks({
  resolve(specifier, context, nextResolve) {
    try { return nextResolve(specifier, context); }
    catch (error) {
      for (const suffix of ['.ts', '.tsx', '.js']) {
        try { return nextResolve(specifier + suffix, context); } catch { /* actual extension only */ }
      }
      throw error;
    }
  },
  load(url, context, nextLoad) {
    if (!url.startsWith('file:')) return nextLoad(url, context);
    const path = fileURLToPath(new URL(url));
    if (path.endsWith('.css') && sourceRootFor(path)) return { format: 'module', source: `export default ${JSON.stringify(loadCss(path))};`, shortCircuit: true };
    if (!branchFor(path) || path.includes('/node_modules/')) return nextLoad(url, context);
    if (!existsSync(path)) return nextLoad(url, context);
    recordSource(path);
    if (path.endsWith('/state/access.tsx')) return { format: 'module', source: 'export function useAccess(){return globalThis.__offlineVisualAccess;}', shortCircuit: true };
    if (path.endsWith('/api/useRecordResource.ts')) return { format: 'module', source: 'export function useRecordResource(...args){return globalThis.__offlineVisualRead(...args);}', shortCircuit: true };
    if (path.endsWith('.css')) return { format: 'module', source: `export default ${JSON.stringify(loadCss(path))};`, shortCircuit: true };
    if (/\.(png|webp|jpe?g|svg)$/.test(path)) return { format: 'module', source: `export default ${JSON.stringify(copyAsset(path))};`, shortCircuit: true };
    if (path.endsWith('.json')) return { format: 'module', source: `export default ${readFileSync(path, 'utf8')};`, shortCircuit: true };
    if (/\.tsx?$/.test(path)) {
      const result = transformSync(path, readFileSync(path, 'utf8'), { jsx: { runtime: 'automatic' } });
      assert.equal(result.errors.length, 0, JSON.stringify(result.errors));
      return { format: 'module', source: result.code, shortCircuit: true };
    }
    return nextLoad(url, context);
  },
});
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { createMemoryRouter, RouterProvider } = await import('react-router');

function medicalTime(day = '2026-10-01', changes = {}) {
  return { precision: 'minute', raw_text: `${day} 08:30`, local_date: day, local_datetime: `${day}T08:30:00`,
    instant: `${day}T00:30:00Z`, timezone_name: 'Asia/Shanghai', utc_offset_minutes: 480,
    resolution_status: 'resolved', local_year: null, local_month: null, end_year: null, end_month: null, relative_text: null, ...changes };
}
function field(id, code, changes = {}) {
  return { field_id: id, field_code: code, concept_code: code, original_name: null, value_type: 'decimal', value_state: 'known',
    raw_numeric: '36.70', raw_text: null, raw_unit: '°C', categorical_value: null, qualifier: null,
    medical_time_id: `time-${id}`, sample_time_origin: 'explicit', medical_time: medicalTime(), confirmation_id: null,
    method: null, reference_interval: null, report_flag: null, verification_states: [], source_anchor: null, ...changes };
}
function record(id, fields, changes = {}) {
  return { patient_id: patientId, record_id: `synthetic-record-${id}`, record_version_id: `synthetic-version-${id}`, kind: 'observation',
    version: 1, origin_type: 'self_report', capture_method: 'manual', quality_status: 'pending',
    recorded_at: '2026-10-13T01:02:03.123456+00:00', source_organization: null, corrected_at: null, correction_reason: null,
    fields, fact_refs: fields.map(value => ({ field_id: value.field_id, fact_id: `synthetic-fact-${value.field_id}`, version_id: `synthetic-fact-version-${value.field_id}`, version: '1' })),
    attachments: [], report: null, allowed_actions: ['read_history'], ...changes };
}
function summary(id, count, mixed = false, mixedUnits = false) {
  const values = ['36.70', '36.8', '37.10', '36.60', '37.0', '36.90', '36.75'];
  const items = [], points = [];
  for (let i = 0; i < count; i++) {
    const day = `2026-09-${String(i + 1).padStart(2, '0')}`;
    const timeUnknown = mixed && i === 2;
    const temp = field(`${id}-temp-${i}`, 'temp', {
      raw_numeric: values[i % values.length], raw_unit: mixedUnits && i === 1 ? 'F' : '°C', qualifier: 'eq',
      medical_time: timeUnknown ? null : medicalTime(day),
      method: mixed ? null : '合成测量方法原文',
    });
    const fields = [temp,
      field(`${id}-rash-${i}`, 'rash', { value_type: 'categorical', raw_numeric: null, raw_unit: null, categorical_value: i % 3 === 0 ? 'yes' : 'no', medical_time: temp.medical_time }),
      field(`${id}-joint-${i}`, 'joint_discomfort', { value_type: 'categorical', value_state: mixed && i === 1 ? 'unknown' : 'known', raw_numeric: null, raw_unit: null, categorical_value: mixed && i === 1 ? null : 'no', medical_time: temp.medical_time }),
    ];
    for (const value of fields) items.push({ field_id: value.field_id, record_version_id: `synthetic-summary-version-${id}-${i}`,
      snapshot: { record_id: `synthetic-summary-record-${id}-${i}`, record_version_id: `synthetic-summary-version-${id}-${i}`,
        kind: 'observation', origin_type: 'self_report', source_organization: mixed ? null : '合成来源机构（仅离线样本）',
        source_definition_revision_id: mixed ? null : 'synthetic-source-definition-1',
        confirmation_snapshot_state: 'not_recorded', source_anchor_snapshot_state: 'not_recorded', field: value } });
    points.push({ field_id: temp.field_id, record_version_id: `synthetic-summary-version-${id}-${i}`,
      source_definition_revision_id: mixed ? null : 'synthetic-source-definition-1', raw_value: temp.raw_numeric,
      raw_unit: temp.raw_unit, plot_at: timeUnknown ? null : `${day}T00:30:00Z`, plottable: !timeUnknown,
      reason_codes: timeUnknown ? ['EXACT_MEDICAL_TIME_REQUIRED'] : [] });
  }
  const result = { summary_id: `synthetic-summary-${id}`, display_id: '合成患儿', from_date: '2026-09-01', to_date: '2026-09-28',
    scope_timezone: 'Asia/Shanghai', generated_at: '2026-10-13T01:00:00Z', validity: 'valid', needs_regeneration: false,
    include_unknown_time: true, include_source_status: true, record_kinds: ['observation', 'report', 'therapy'], items,
    trend_projection: { schema_version: 'summary-raw-trend-1', snapshot_sha256: digest(JSON.stringify(items)), clinical_interpretation: false,
      series: [{ field_code: 'temp', concept_code: 'temp', points, segments: points.slice(1).map((point, i) => ({
        from_field_id: points[i].field_id, to_field_id: point.field_id, connect: !mixed && !mixedUnits,
        reason_codes: mixed ? ['METHOD_UNKNOWN', 'SOURCE_DEFINITION_UNKNOWN', 'SOURCE_ORGANIZATION_UNKNOWN', 'UNPOSITIONED_TIME_GAP', ...(point.plottable && points[i].plottable ? [] : ['POINT_NOT_PLOTTABLE', 'EXACT_MEDICAL_TIME_REQUIRED'])] : mixedUnits ? ['UNIT_MISMATCH'] : [],
      })) }] } };
  return result;
}
const sharedTime = '2026-10-12 08:30 · Asia/Shanghai（UTC+08:00）';
const fixtures = [
  { id: 'home-normal', type: 'home', reference: 'S03', label: '首页：三项同一已知时间', value: { name: '合成患儿', description: '家庭记录样本', observations: [
    { label: '体温', value: '36.70 °C', time: sharedTime, context: '最近的已知时间记录' },
    { label: '皮疹', value: '未观察到', time: sharedTime, context: '最近的已知时间记录' },
    { label: '关节', value: '未观察到', time: sharedTime, context: '最近的已知时间记录' },
  ] } },
  { id: 'home-rich', type: 'home', reference: 'S03', label: '首页：长内容与混合医学时间', value: { name: '合成患儿', description: '长内容与待确认说明样本', observations: [
    { label: '体温', value: '≤36.70 °C', time: sharedTime, context: '最近的已知时间记录', notice: '另有 2 条记录医学时间待确认，未参与最新时间排序。' },
    { label: '皮疹', value: '观察到，原始描述保留且不推断严重程度', time: '2026-10-11 上午 · 时区未明确', context: '最近的已知时间记录', notice: '另有 1 条记录医学时间待确认，未参与最新时间排序。' },
    { label: '关节', value: '无法确定', time: '时间待补充', context: '原值保留，时间未知', notice: '此项原始记录尚未提供医学时间。' },
  ] } },
  { id: 'home-time-unknown', type: 'home', reference: 'S03', label: '首页：医学时间未知', value: { name: '合成患儿', description: '未知时间样本', observations: [
    { label: '体温', value: '36.70 °C', time: '时间待补充', context: '时间尚未确定' },
    { label: '皮疹', value: '无法确定', time: '时间待补充', context: '时间尚未确定' },
    { label: '关节', value: '无法确定', time: '时间待补充', context: '时间尚未确定' },
  ] } },
  { id: 'record-numeric', type: 'record', reference: 'S08', label: '详情：带比较符的原始数值', value: record('numeric', [field('numeric-temp', 'temp', { qualifier: 'le', raw_text: '≤36.70 °C（合成原文）' })]) },
  { id: 'record-text', type: 'record', reference: 'S08', label: '详情：完整长文字', value: record('text', [field('text-note', 'observation_note', {
    value_type: 'text', raw_numeric: null, raw_unit: null, concept_code: null, medical_time: null,
    raw_text: '合成观察原文：今天活动后说膝盖不舒服，休息一会儿后继续玩耍。此处只保存家属描述，不补写判断；保留数字 01.20、单位原文和标点。',
  })]) },
  { id: 'record-multifield', type: 'record', reference: 'S08', label: '详情：多字段与未知状态', value: record('multifield', [
    field('multi-temp', 'temp', { raw_numeric: '36.70', raw_unit: 'C', medical_time: medicalTime('2026-10-12', { timezone_name: null, utc_offset_minutes: null }) }),
    field('multi-rash', 'rash', { value_type: 'categorical', raw_numeric: null, raw_unit: null, categorical_value: 'no' }),
    field('multi-joint', 'joint_discomfort', { value_type: 'categorical', value_state: 'unknown', raw_numeric: null, raw_unit: null, medical_time: null }),
  ]) },
  { id: 'summary-28-observations', type: 'summary', reference: 'S12-B', label: '摘要：28 条观察、84 个字段', value: summary('dense', 28) },
  { id: 'summary-unknown-scatter', type: 'summary', reference: 'S12-B', label: '摘要：未知时间与来源不足，仅散点', value: summary('scatter', 5, true) },
  { id: 'summary-mixed-units', type: 'summary', reference: 'S12-B', label: '摘要：混合单位，原值表格回退', value: summary('mixed-units', 3, false, true) },
];
// Same 28-record/84-field raw snapshots as the connected density case.
// Frozen source metadata outside each field is explicitly unknown here; the
// full item snapshots therefore differ. No professional approval or clinical
// conclusion is supplied. These are client-binding-validated renderer fixtures,
// not a claim that the backend recomputed their segment eligibility.
const denseSnapshot = fixtures.find(fixture => fixture.id === 'summary-28-observations').value;
fixtures.push({ id: 'summary-28-scatter', type: 'summary', reference: 'S12-B',
  label: '摘要：28 条观察、84 个字段，来源不足仅散点',
  value: { ...denseSnapshot, items: denseSnapshot.items.map(item => ({ ...item, snapshot: { ...item.snapshot,
    source_organization: null, source_definition_revision_id: null } })),
    trend_projection: { ...denseSnapshot.trend_projection,
    series: denseSnapshot.trend_projection.series.map(group => ({ ...group,
      points: group.points.map(point => ({ ...point, source_definition_revision_id: null,
        reason_codes: [] })),
      segments: group.segments.map(segment => ({ ...segment, connect: false,
        reason_codes: ['SOURCE_ORGANIZATION_UNKNOWN', 'SOURCE_DEFINITION_UNKNOWN'] })),
    })),
  } },
});
fixtures.at(-1).value.trend_projection.snapshot_sha256 = digest(JSON.stringify(fixtures.at(-1).value.items));
assert.equal(JSON.stringify(fixtures.at(-1).value.items.map(item => item.snapshot.field)),
  JSON.stringify(denseSnapshot.items.map(item => item.snapshot.field)), 'Dense scatter must retain identical raw field snapshots');
function freeze(value) { if (value && typeof value === 'object') { Object.freeze(value); for (const child of Object.values(value)) freeze(child); } return value; }
freeze(fixtures);
const fixtureJson = JSON.stringify(fixtures);
writeFileSync(resolve(out, 'synthetic-fixtures.json'), JSON.stringify({ boundary: BOUNDARY, fixtures }, null, 2));
const referenceAssets = {};
for (const name of ['S03', 'S08', 'S12-B']) referenceAssets[name] = copyAsset(resolve(baseline, `ui-delivery/screens/png/${name}.png`), 'original-raw-reference');

function rawFields(fixture) {
  const fields = fixture.type === 'record' ? fixture.value.fields : fixture.type === 'summary' ? fixture.value.items.map(item => item.snapshot.field) : [];
  return fields.map(value => ({ field_id: value.field_id, raw_numeric: value.raw_numeric, raw_unit: value.raw_unit, raw_text: value.raw_text,
    categorical_value: value.categorical_value, qualifier: value.qualifier, value_state: value.value_state, medical_time: value.medical_time }));
}
const csp = "default-src 'none'; img-src 'self' file: data:; style-src 'unsafe-inline'; font-src 'self'; script-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-src 'self' file: about:;";
function neutralize(fragment, sourceRoot) {
  assert.equal(fragment.querySelectorAll('script,iframe,object,embed,form').length, 0, 'Unexpected executable/submittable element');
  let inertLinks = 0, controls = 0;
  for (const element of fragment.querySelectorAll('*')) {
    for (const attribute of [...element.attributes]) assert.ok(!/^on/i.test(attribute.name), `Inline handler: ${attribute.name}`);
    for (const attr of ['src', 'href', 'xlink:href', 'poster']) {
      const value = element.getAttribute(attr);
      if (!value) continue;
      if (element.localName === 'a' && attr === 'href') {
        element.removeAttribute('href'); element.setAttribute('role', 'link'); element.setAttribute('aria-disabled', 'true');
        element.setAttribute('title', '静态样本：未连接业务页面'); inertLinks++; continue;
      }
      if (value.startsWith('/assets/')) element.setAttribute(attr, resolveAsset(value, resolve(sourceRoot, 'guardian-h5/src/components/ThreeDIcon.tsx')));
      else assert.ok(value.startsWith('assets/') || value.startsWith('data:') || value.startsWith('#'), `Unexpected asset URL: ${value}`);
    }
    if (element.localName === 'button') { element.setAttribute('type', 'button'); element.setAttribute('data-static-control', 'inert'); controls++; }
  }
  // Button styling/disabled state remains the actual production rendering. There
  // are no scripts or event handlers to execute; native details remains usable.
  return { inertLinks, controls };
}
function pageHtml(title, inner, notes) {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="${escape(csp)}"><title>${escape(title)} · 离线合成样本</title><style>html,body{margin:0;background:#e6ebf1;font:13px/1.5 system-ui,sans-serif;color:#17283e}.external-boundary{position:sticky;top:0;z-index:2;box-sizing:border-box;width:100%;padding:10px 12px;background:#fff3cc;border-bottom:1px solid #c6a34d}.external-boundary strong{display:block;font-size:12px}.external-boundary p{margin:4px 0 0;font-size:11px}.sample-frame{display:block;width:390px;max-width:100%;height:844px;border:0;margin:0 auto;background:white}.sample-notes{max-width:390px;box-sizing:border-box;padding:10px 12px;margin:auto;font-size:12px}</style></head><body><aside class="external-boundary"><strong>${BOUNDARY}</strong><p>${escape(title)}</p></aside><iframe class="sample-frame" title="${escape(title)}，390 × 844 静态展示区" sandbox="" srcdoc="${escape(inner)}"></iframe><p class="sample-notes">${escape(notes)} ${LIMIT}</p></body></html>`;
}
const results = [], bindingChecks = [];
for (const [branch, sourceRoot] of [['baseline', baseline], ['candidate', candidate]]) {
  const load = path => import(pathToFileURL(resolve(sourceRoot, `guardian-h5/src/${path}`)).href);
  const [{ UI2Frame }, { HomeCompanion, HomeQuickActions }, { UI2RecordDetailPage }, { SummarySnapshotPresentation, summaryRecordCount }, { SummaryTrend }, records, { verifySummaryTrendBinding }] = await Promise.all([
    load('components/UI2Frame.tsx'), load('components/HomeVisual.tsx'), load('features/records/UI2RecordDetailPage.tsx'),
    load('features/summaries/SummarySnapshotPresentation.tsx'), load('features/summaries/SummaryTrend.tsx'), load('api/records.ts'), load('api/summaries.ts'),
  ]);
  for (const fixture of fixtures) {
    currentFixture = fixture;
    const before = JSON.stringify(fixture.value), rawBefore = JSON.stringify(rawFields(fixture));
    let element, route;
    if (fixture.type === 'home') {
      route = '/home';
      element = h(UI2Frame, { title: '首页', screenId: 'UI2-01', note: BOUNDARY, audience: 'family', primary: 'home',
        kicker: '10月13日星期二', onBack: denyAction, onNavigate: denyAction },
      h(HomeCompanion, { ...fixture.value, onArchive: denyAction, onObserve: denyAction, onViewObservation: denyAction,
        patientPicker: h('p', null, '离线合成患儿，无可执行的账户切换') }),
      h(HomeQuickActions, { onObserve: denyAction, onReports: denyAction, onTherapies: denyAction }));
    } else if (fixture.type === 'record') {
      records.validateRecordFactReferences(fixture.value);
      route = `/records/${fixture.value.record_id}`; element = h(UI2RecordDetailPage);
    } else {
      verifySummaryTrendBinding(fixture.value);
      assert.ok(fixture.value.trend_projection.series.every(group => group.points.every(point => !point.plottable || point.reason_codes.length === 0)));
      // Regression probes must fail in the real production binding validator.
      for (const corrupt of [
        value => { value.trend_projection.series[0].points.find(point => point.plottable).reason_codes = ['SOURCE_DEFINITION_UNKNOWN']; },
        value => { const point = value.trend_projection.series[0].points.find(point => point.plottable); value.items.find(item => item.field_id === point.field_id).snapshot.field.qualifier = null; },
        value => { value.trend_projection.series[0].points[0].source_definition_revision_id = 'synthetic-mismatched-source-definition'; },
      ]) { const invalid = structuredClone(fixture.value); corrupt(invalid); assert.throws(() => verifySummaryTrendBinding(invalid)); }
      bindingChecks.push({ branch, fixture: fixture.id, validator: 'api/summaries.ts#verifySummaryTrendBinding', status: 'PASS',
        rejects_plottable_reasons: true, rejects_nonexact_qualifier: true, rejects_source_identity_mismatch: true });
      route = `/summaries/${fixture.value.summary_id}`;
      element = h(UI2Frame, { title: '就医摘要', screenId: 'UI2-14', note: BOUNDARY, audience: 'family', onBack: denyAction, onNavigate: denyAction },
        h(SummarySnapshotPresentation, { summary: fixture.value, trend: h(SummaryTrend, { summary: fixture.value }) }));
    }
    const router = createMemoryRouter([{ path: fixture.type === 'record' ? '/records/:recordId' : '*', element }], { initialEntries: [route] });
    const root = createRoot(document.getElementById('root'));
    try {
      await act(async () => root.render(h(RouterProvider, { router })));
      await act(async () => { await Promise.resolve(); });
      const host = document.querySelector('[data-screen-id]'), shadow = host?.shadowRoot;
      assert.ok(shadow?.querySelector('.device'), `${fixture.id}: production ShadowRoot did not mount`);
      assert.ok(shadow.querySelector('style').textContent.length > 20000, 'Real production CSS is required');
      const text = shadow.querySelector('.device').textContent, raw = rawFields(fixture), values = [];
      for (const value of raw) {
        if (value.raw_numeric !== null) assert.ok(text.includes(value.raw_numeric), `${fixture.id}: raw numeric missing ${value.raw_numeric}`);
        if (value.raw_unit !== null) assert.ok(text.includes(value.raw_unit), `${fixture.id}: raw unit missing ${value.raw_unit}`);
        if (value.raw_text !== null) assert.ok(text.includes(value.raw_text), `${fixture.id}: raw text missing`);
        const expected = records.fieldValue(value);
        assert.ok(text.includes(expected), `${fixture.id}: display value missing ${expected}`);
        values.push(expected);
      }
      if (fixture.type === 'home') for (const observation of fixture.value.observations) {
        for (const key of ['value', 'time', 'notice']) if (observation[key]) assert.ok(text.includes(observation[key]), `${fixture.id}: supplied ${key} missing`);
      }
      if (fixture.type === 'record') assert.equal(shadow.querySelectorAll('.rich-field-primary').length, fixture.value.fields.length);
      let recordCounts = null, pointCount = null, segmentCount = null, numericLabelCount = null, summaryHierarchy = null;
      if (fixture.type === 'summary') {
        assert.equal(shadow.querySelectorAll('.rich-summary-field').length, fixture.value.items.length, 'All original fields must remain in DOM');
        recordCounts = ['observation', 'report', 'therapy'].map(kind => summaryRecordCount(fixture.value, kind));
        assert.deepEqual([...shadow.querySelectorAll('.rich-summary-counts strong')].map(node => node.textContent.trim()), recordCounts.map(count => `${count} 条`));
        pointCount = shadow.querySelectorAll('[data-summary-point]').length;
        segmentCount = shadow.querySelectorAll('[data-summary-segment]').length;
        numericLabelCount = shadow.querySelectorAll('.rich-summary-trend svg text').length;
        if (fixture.id === 'summary-28-observations') { assert.deepEqual(recordCounts, [28, 0, 0]); assert.equal(raw.length, 84); assert.equal(pointCount, 28); assert.equal(segmentCount, 27); }
        if (fixture.id === 'summary-28-scatter') {
          assert.deepEqual(recordCounts, [28, 0, 0]); assert.equal(raw.length, 84);
          assert.equal(pointCount, 28); assert.equal(segmentCount, 0); assert.equal(numericLabelCount, 0);
          assert.ok(text.includes('不连接为趋势'));
          assert.ok(fixture.value.trend_projection.series.every(group => group.segments.every(segment =>
            !segment.connect && segment.reason_codes.includes('SOURCE_DEFINITION_UNKNOWN') && segment.reason_codes.includes('SOURCE_ORGANIZATION_UNKNOWN'))));
        }
        if (fixture.id === 'summary-unknown-scatter') { assert.equal(pointCount, 4); assert.equal(segmentCount, 0); assert.ok(text.includes('不连接为趋势')); assert.ok(text.includes('时间待补充')); }
        if (fixture.id === 'summary-mixed-units') { assert.equal(pointCount, 0); assert.equal(segmentCount, 0); assert.ok(text.includes('原始单位不同')); }
        if (branch === 'candidate') {
          const panel = shadow.querySelector('details.rich-summary-observations');
          assert.ok(panel && !panel.open, 'Observation snapshot panel must start collapsed');
          const groups = [...panel.querySelectorAll('details.rich-summary-record')];
          const expectedVersions = [...new Set(fixture.value.items.map(item => item.record_version_id))];
          assert.deepEqual(groups.map(group => group.dataset.summaryRecordVersion), expectedVersions, 'Immutable snapshot version order changed');
          assert.equal(groups.length, recordCounts[0]);
          for (const group of groups) {
            assert.ok(!group.open, 'Immutable record details must start collapsed');
            const groupItems = fixture.value.items.filter(item => item.record_version_id === group.dataset.summaryRecordVersion);
            assert.equal(group.querySelectorAll('.rich-summary-field').length, groupItems.length);
            const heading = group.querySelector(':scope > summary');
            assert.ok(!/最新|最近|最后/.test(heading.textContent), 'First-field header must not assert latest chronology');
            assert.equal(heading.querySelector('.rich-summary-record-preview').textContent,
              `${records.fieldLabel(groupItems[0].snapshot.field)}：${records.fieldValue(groupItems[0].snapshot.field)}`);
          }
          for (const article of panel.querySelectorAll('.rich-summary-field')) assert.ok(article.closest('details.rich-summary-record'), 'Original field not retained inside native record details');
          summaryHierarchy = { snapshot_panel_closed: true, immutable_record_groups: groups.length,
            all_record_groups_closed: true, all_raw_fields_inside_native_details: true,
            snapshot_version_order_preserved: true, first_field_header_claims_latest: false };
        }
      }
      assert.equal(JSON.stringify(fixture.value), before, 'Render mutated a synthetic input');
      assert.equal(JSON.stringify(rawFields(fixture)), rawBefore, 'Raw strings changed during render');
      if (fixture.type === 'summary') assert.ok([...shadow.querySelectorAll('a')].every(link => link.getAttribute('href')?.startsWith('/records/versions/')), 'Summary links must point only to fixed versions before neutralization');
      const fragment = document.createElement('div'); fragment.innerHTML = shadow.innerHTML;
      const safety = neutralize(fragment, sourceRoot);
      const detailsCount = fragment.querySelectorAll('details').length;
      const clonedHost = host.cloneNode(false);
      const template = document.createElement('template'); template.setAttribute('shadowrootmode', 'open');
      template.innerHTML = fragment.innerHTML; clonedHost.appendChild(template);
      const inner = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="${escape(csp)}"><style>html,body{margin:0;padding:0}</style></head><body>${clonedHost.outerHTML}</body></html>`;
      const file = `${branch}-${fixture.id}.html`;
      writeFileSync(resolve(out, file), pageHtml(`${branch === 'candidate' ? '候选展示' : '冻结基线'} · ${fixture.label}`, inner,
        '展示区使用实际生产组件及 CSS。链接与业务操作不可执行；原生详情可展开。'));
      results.push({ branch, fixture: fixture.id, label: fixture.label, reference: fixture.reference, file,
        fixture_sha256: digest(before), raw_fields_sha256: digest(rawBefore), raw_field_count: raw.length,
        display_values: values, record_counts: recordCounts, point_count: pointCount, connected_segment_count: segmentCount, numeric_label_count: numericLabelCount,
        details_count: detailsCount, summary_hierarchy: summaryHierarchy, captured_shadow_sha256: digest(shadow.innerHTML), exported_html_sha256: fileDigest(resolve(out, file)), ...safety });
    } finally { await act(async () => root.unmount()); router.dispose(); }
  }
}
assert.equal(JSON.stringify(fixtures), fixtureJson, 'Shared fixtures mutated');
for (const fixture of fixtures) {
  const before = results.find(result => result.branch === 'baseline' && result.fixture === fixture.id);
  const after = results.find(result => result.branch === 'candidate' && result.fixture === fixture.id);
  assert.equal(before.fixture_sha256, after.fixture_sha256); assert.equal(before.raw_fields_sha256, after.raw_fields_sha256);
  assert.deepEqual(before.display_values, after.display_values); assert.deepEqual(before.record_counts, after.record_counts);
  assert.equal(before.point_count, after.point_count); assert.equal(before.connected_segment_count, after.connected_segment_count);
  assert.equal(before.numeric_label_count, after.numeric_label_count);
}
for (const branch of ['baseline', 'candidate']) {
  const dense = results.find(result => result.branch === branch && result.fixture === 'summary-28-observations');
  const scatter = results.find(result => result.branch === branch && result.fixture === 'summary-28-scatter');
  assert.equal(dense.raw_fields_sha256, scatter.raw_fields_sha256, 'Dense scatter raw snapshot differs');
  assert.deepEqual(dense.display_values, scatter.display_values, 'Dense scatter original display values differ');
}
assert.equal(fileDigest(resolve(candidate, 'guardian-h5/src/features/summaries/SummaryTrend.tsx')),
  fileDigest(resolve(baseline, 'guardian-h5/src/features/summaries/SummaryTrend.tsx')), 'Production trend source changed');
assert.equal(networkAttempts, 0, 'Network was attempted');
assert.equal(storageWrites, 0, 'Persistent storage was modified');
assert.deepEqual(errors, [], 'jsdom reported rendering errors');
for (const [path, before] of sourceFiles) assert.equal(fileDigest(path), before, `Source changed mid-render; rerun after freeze: ${path}`);
for (const row of assetFiles.values()) assert.equal(fileDigest(resolve(out, row.href)), row.sha256);
const indexRows = fixtures.map(fixture => `<section class="comparison" id="${fixture.id}"><h2>${fixture.label}</h2><div class="columns"><article><h3>原始参照 PNG · ${fixture.reference}</h3><a href="${referenceAssets[fixture.reference]}"><img width="390" src="${referenceAssets[fixture.reference]}" alt="${fixture.reference} 原始参照，按原始字节复制"></a><p>原图，仅宽度等比展示，未裁切或重绘。</p></article>${['baseline', 'candidate'].map(branch => `<article><h3>${branch === 'candidate' ? '候选生产展示' : '冻结基线生产展示'}</h3><iframe width="390" height="944" title="${fixture.label} ${branch}" sandbox="" src="${branch}-${fixture.id}.html"></iframe><p><a href="${branch}-${fixture.id}.html">独立打开 · 390 × 844 展示区</a></p></article>`).join('')}</div></section>`).join('\n');
writeFileSync(resolve(out, 'index.html'), `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="${escape(csp)}"><title>sJIA 离线合成界面对照</title><style>html,body{margin:0;background:#e6ebf1;color:#17283e;font:14px/1.6 system-ui,sans-serif}header{position:sticky;top:0;z-index:5;padding:12px 20px;background:#fff3cc;border-bottom:1px solid #c6a34d}header p{margin:3px 0}main{padding:20px}nav{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}a{color:#1f5b98}.comparison{margin-bottom:44px;scroll-margin-top:130px}.comparison h2{font-size:19px}.columns{display:grid;grid-template-columns:repeat(3,390px);gap:22px;align-items:start;overflow-x:auto}.columns h3{font-size:14px}.columns img{display:block;width:390px;height:auto}.columns iframe{display:block;width:390px;height:944px;border:1px solid #aab7c5;background:white}.columns p{font-size:12px}footer{padding:20px}</style></head><body><header><strong>${BOUNDARY}</strong><p>${LIMIT}</p></header><main><nav>${fixtures.map(fixture => `<a href="#${fixture.id}">${fixture.label}</a>`).join('')}</nav><p>参照 / 冻结基线 / 候选。两侧生产展示使用完全相同的合成输入，来自实际 UI2Frame ShadowRoot 的静态内容。当前仅完成结构、原值、资源字节与离线边界验证。</p>${indexRows}</main><footer>验证记录：<a href="validation.json">validation.json</a> · <a href="README.md">使用说明与限制</a> · <a href="synthetic-fixtures.json">合成输入</a></footer></body></html>`);
// Parse every saved export without running scripts or loading resources. jsdom
// does not render declarative shadow DOM, so validate the template explicitly.
for (const result of results) {
  const outer = new JSDOM(readFileSync(resolve(out, result.file), 'utf8'));
  assert.ok(outer.window.document.querySelector('.external-boundary').textContent.includes(BOUNDARY));
  const frame = outer.window.document.querySelector('iframe');
  assert.equal(frame.getAttribute('sandbox'), '');
  const inner = new JSDOM(frame.getAttribute('srcdoc'));
  const template = inner.window.document.querySelector('template[shadowrootmode="open"]');
  assert.ok(template, 'Captured ShadowRoot template missing from export');
  const content = template.content;
  assert.equal(content.querySelectorAll('script,iframe,object,embed,form,a[href],[formaction]').length, 0, 'Executable action or application destination in export');
  assert.equal(content.querySelectorAll('details').length, result.details_count, 'Native details were lost');
  for (const element of content.querySelectorAll('[src],image[href]')) {
    const href = element.getAttribute('src') ?? element.getAttribute('href');
    assert.ok(href.startsWith('assets/') && existsSync(resolve(out, href)), `Nonlocal or absent exported asset: ${href}`);
  }
  for (const style of content.querySelectorAll('style')) {
    assert.ok(!/@import\s|https?:\/\//i.test(style.textContent), 'Unresolved CSS import or remote CSS source');
    for (const match of style.textContent.matchAll(/url\(["']?([^"')]+)["']?\)/g)) if (!match[1].startsWith('data:')) assert.ok(existsSync(resolve(out, match[1])), 'Missing CSS asset');
  }
  assert.equal(inner.window.document.querySelectorAll('script').length, 0);
  assert.equal(outer.window.document.querySelectorAll('script').length, 0);
  inner.window.close(); outer.window.close();
}
const validation = {
  status: 'PASS_OFFLINE_DOM_EXPORT_ONLY', boundary: BOUNDARY, limitations: LIMIT,
  generated_at: new Date().toISOString(), harness_sha256: fileDigest(fileURLToPath(import.meta.url)), node_version: process.version, viewport: { width: 390, height: 844 },
  browser_used: false, screenshots_created: false, authenticated_ui_evidence: false, clinical_evaluation_executed: false,
  network_attempts: networkAttempts, persistent_storage_writes: storageWrites, isolated_record_hook_calls: readCalls, exported_html_reparse: 'PASS', production_trend_source_unchanged: true, dense_scatter_uses_identical_84_raw_field_snapshots: true, dense_scatter_frozen_source_metadata_differs: true,
  summary_binding_checks: bindingChecks, backend_segment_eligibility_verified: false,
  sources: { baseline, candidate }, fixtures: fixtures.length, exported_pages: results.length, results,
  css: [...cssFiles.values()], assets: [...assetFiles.values()],
  loaded_sources: [...sourceFiles].map(([path, sha256]) => ({ branch: sourceLabelFor(path), source: relative(sourceRootFor(path), path), sha256 })),
};
writeFileSync(resolve(out, 'validation.json'), JSON.stringify(validation, null, 2));
writeFileSync(resolve(out, 'README.md'), `# sJIA offline comparison\n\n${BOUNDARY}\n\n${LIMIT}\n\n## Run\n\nFrom the candidate guardian-h5 directory:\n\n\`node scripts/render-core-visual-comparison.mjs --baseline "${baseline}" --out "${out}"\`\n\nOpen index.html later in a browser only when authorized. No browser was used to build or validate this export. Each standalone page has an external persistent synthetic boundary and a sandboxed 390 × 844 iframe. The captured frame uses a declarative Shadow DOM template and actual production CSS. A modern browser with declarative Shadow DOM support is required for later viewing. Native details can expand; application buttons have no JavaScript and links have no business destination. No server, backend, database, credentials, or saved session is required. No API script is included.\n\n## What was checked\n\n- ${fixtures.length} frozen synthetic inputs through both original and candidate production presentation (${results.length} standalone pages).\n- Real HomeCompanion + HomeQuickActions + UI2Frame; real UI2RecordDetailPage + UI2FamilyFrame; real SummarySnapshotPresentation + SummaryTrend + UI2Frame.\n- Narrow module hooks replace only useAccess/useRecordResource inside this Node process. They never set real account/session state or localStorage. The record hook accepts only each exact synthetic record identity.\n- All CSS imports read real files; local @import is recursively inlined (including production tokens). No empty CSS stubs.\n- Actual ShadowRoot is captured after React layout/effects, then exported with local resource URLs. CSS selectors and component markup are not replaced with a proposed design.\n- Original S03/S08/S12-B PNGs and all artwork remain byte-identical, with SHA-256 and source paths in validation.json.\n- Candidate observation snapshot panel and immutable record groups begin collapsed; every raw field remains inside native details. Original snapshot version order is retained; first-field previews do not assert latest chronology. Production SummaryTrend source is byte-identical to baseline.\n- Same synthetic inputs and raw values before/after; unknown time/value stays unknown. Text and raw numeric spelling, trailing zeroes, units, and comparison qualifiers are retained.\n- 28 distinct observation records retain 84 fields, 28 numeric points, 27 synthetically enabled segments; identical 84 raw field snapshots are retained in a separate insufficient-source fixture with explicitly different frozen source metadata with 28 points, 0 segments, and 0 numeric labels; the mixed-unknown-time scattered case retains 4 points and 0 segments; mixed units render an original-value table without numeric coordinates.\n- Every summary fixture passes the actual production api/summaries.ts verifySummaryTrendBinding validator on baseline and candidate. Regression probes confirm it rejects nonempty reasons on plottable points, nonexact qualifiers, and mismatched source identities. Plottable point reasons remain empty; insufficient-source segment reasons use SOURCE_ORGANIZATION_UNKNOWN and SOURCE_DEFINITION_UNKNOWN.\n- Summary projections are synthetic renderer variants. Client field/point binding was validated; backend segment eligibility was not independently recomputed or validated. The connected and source-insufficient density cases retain identical raw field snapshots but explicitly differ in frozen source metadata outside fields.\n- All synthetic input structures are deep-frozen. Source files are checked for mid-run changes.\n- fetch, XMLHttpRequest, WebSocket, EventSource, sendBeacon, Node HTTP/TCP/TLS/UDP, and jsdom resource fetches reject any network attempt; zero attempts occurred. Storage mutation is prohibited and none occurred.\n- Exported pages have no scripts, submit forms, executable handlers, API destinations, or private source links. A restrictive CSP and sandbox reinforce the static boundary.\n\n## What was not checked\n\nNo browser layout/overflow, screenshot/pixel matching, responsive geometry, authenticated page, permissions, network service, database business operation, or clinical effect has been tested. jsdom does not implement layout. These files are a reproducible offline review aid, not evidence of current runtime UI or a final mother-design approval. Baseline may differ from original PNG data and scroll position. Home is the production shared presentation (not the permission-bound home route). Summary is its production snapshot/trend presentation in its frame (not the summary task workflow).\n`);
dom.window.close();
console.log(JSON.stringify({ status: validation.status, output: out, fixtures: fixtures.length, pages: results.length,
  network_attempts: networkAttempts, storage_writes: storageWrites, copied_asset_count: assetFiles.size,
  real_css_files: cssFiles.size, browser_used: false }, null, 2));
