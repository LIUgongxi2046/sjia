// Actual support review page, synthetic values only; no browser or real grants.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
registerHooks({ load(url, context, nextLoad) {
  if (/\.(png|webp|jpg|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLTextAreaElement', 'HTMLSelectElement', 'HTMLButtonElement', 'Event', 'KeyboardEvent']) Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { ApplicationsReviewPage } = await import('../src/features/support/ApplicationsReviewPage.tsx');
const { validateApiValue } = await import('../src/api/schema.ts');
const uuid = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const seed = { application_id: uuid(1), applicant_id: uuid(2), patient_id: null, status: 'pending', version: '1',
  evidence_ref: 'synthetic-only evidence', note: null, requested_access: 'read_write', created_at: '2026-10-11T00:00:00Z', checked_at: null, reason: null };
let current, posts, lost, paths, root;
const session = { principal_id: uuid(3), capabilities: [{ capability_id: 'decide_relation', state: 'available' }] };
globalThis.fetch = async (path, options = {}) => {
  paths.push(path);
  if (path === `/api/support/relation-applications/${uuid(1)}`) return Response.json(current);
  if (path === `/api/support/relation-applications/${uuid(1)}/decide`) {
    const payload = JSON.parse(options.body); posts.push(payload);
    assert.deepEqual(Object.keys(payload).sort(), ['decision', 'expected_version', 'patient_id', 'reason'].filter(key => payload.decision === 'approved' || key !== 'patient_id').sort());
    current = { ...current, version: '2', status: payload.decision, patient_id: payload.patient_id ?? current.patient_id, reason: payload.reason, checked_at: '2026-10-11T01:00:00Z' };
    if (lost) throw new TypeError('synthetic lost reply');
    return Response.json({ relation_id: payload.decision === 'approved' ? uuid(4) : null, status: payload.decision, version: '2' });
  }
  throw new Error(`Unexpected synthetic request ${path}`);
};
async function flush() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); }
async function mount(bound = null, available = true) {
  current = { ...seed, patient_id: bound }; posts = []; paths = []; lost = false;
  root = createRoot(document.getElementById('root'));
  const router = createMemoryRouter([{ path: '/support/applications', element: h(ApplicationsReviewPage, { session: available ? session : null, accessState: available ? 'default' : 'permission', accessError: null, onRefresh() {} }) }],
    { initialEntries: [`/support/applications?application_id=${uuid(1)}`] });
  await act(async () => root.render(h(RouterProvider, { router }))); await flush();
}
function shadow() { return document.querySelector('[data-screen-id]')?.shadowRoot; }
function input(label) { const node = [...shadow().querySelectorAll('label')].find(value => value.querySelector('span')?.textContent === label); assert.ok(node, label); return node.querySelector('input,select,textarea'); }
async function change(label, value) { const node = input(label); await act(async () => Simulate.change(node, { target: { value, checked: value } })); await flush(); }
async function click(label) { const node = [...shadow().querySelectorAll('button')].find(value => value.textContent === label); assert.ok(node, label); await act(async () => node.click()); await flush(); }
async function unmount() { await act(async () => root.unmount()); document.getElementById('root').replaceChildren(); }
await mount();
assert.equal(paths.length, 1); assert.ok(!paths.some(path => path.includes('/patients')));
await change('核验结论', 'approved'); await change('核验后的准确患儿标识', 'not-a-uuid'); await change('核验处理原因', 'Synthetic checked identity');
await change('已核对该申请与以上患儿标识；已有绑定不能更换', true); await click('提交核验结论'); assert.equal(posts.length, 0); assert.equal(input('核验后的准确患儿标识').disabled, false);
await change('核验后的准确患儿标识', uuid(5)); await change('已核对该申请与以上患儿标识；已有绑定不能更换', true); lost = true;
await click('提交核验结论'); assert.equal(posts.length, 1); assert.equal(shadow().querySelector('fieldset').disabled, true);
await click('查询同一申请'); assert.equal(posts.length, 1); assert.ok(shadow().textContent.includes('处理结果已核验')); await unmount();
await mount(uuid(5)); await change('核验结论', 'approved'); assert.equal(input('核验后的准确患儿标识').readOnly, true); await unmount();
await mount(uuid(5)); await change('核验结论', 'rejected'); await change('核验处理原因', 'Synthetic insufficient evidence'); await click('提交核验结论');
assert.equal(posts.length, 1); assert.equal(posts[0].patient_id, undefined); assert.equal(current.patient_id, uuid(5)); assert.ok(shadow().textContent.includes('处理结果已核验')); await unmount();
await mount(null, false); assert.equal(paths.length, 0); assert.ok(!shadow().textContent.includes('synthetic-only evidence')); await unmount();
assert.throws(() => validateApiValue('protocol.schema.json#/$defs/SupportApplicationDecisionInput', { expected_version: '1', decision: 'approved', reason: 'synthetic', patient_id: uuid(5), action: 'read_raw' }));
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_NONVISUAL_DOM', checks: ['exact application read without patient directory', 'invalid target rejected before dispatch', 'explicit approval sends no grant actions or fields', 'lost response queries same application without repost', 'prebound target remains read-only', 'rejection omits patient and preserves binding', 'missing service scope never loads application evidence', 'strict UI decision contract rejects grant injection'] }, null, 2));
