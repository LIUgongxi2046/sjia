// UI-G04: actual ShadowRoot theme behavior and source-style contracts.
// Synthetic DOM only: no browser pixels, backend, patient data or clinical calls.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { selectorSpecificity, winningDeclaration } from './css-cascade-test.mjs';
registerHooks({ load(url, context, nextLoad) {
  if (/\.(png|webp|jpg|svg)$/.test(url)) return { format:'module', source:`export default ${JSON.stringify(url)};`, shortCircuit:true };
  if (url.includes('.css')) return { format:'module', source:`export default ${JSON.stringify(readFileSync(fileURLToPath(new URL(url)), 'utf8'))};`, shortCircuit:true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url:'http://localhost/theme-test', pretendToBeVisual:true });
for (const name of ['window','document','navigator','HTMLElement','HTMLInputElement','HTMLButtonElement','Event','CustomEvent','StorageEvent','KeyboardEvent','Node','ShadowRoot']) {
  Object.defineProperty(globalThis, name, { value:dom.window[name], configurable:true });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// jsdom has no layout. Only this focus-target enumeration is stubbed, not focus itself.
HTMLElement.prototype.getClientRects = function () { return this.hidden ? [] : [{}]; };
const mediaListeners = new Set();
const media = { matches:false, media:'(prefers-color-scheme: dark)',
  addEventListener(type, listener) { assert.equal(type, 'change'); mediaListeners.add(listener); },
  removeEventListener(type, listener) { assert.equal(type, 'change'); mediaListeners.delete(listener); } };
window.matchMedia = query => { assert.equal(query, media.media); return media; };
const writes = []; let blockedStorage = false; let requests = 0;
const storagePrototype = Object.getPrototypeOf(window.localStorage);
const originalSet = storagePrototype.setItem;
const originalGet = storagePrototype.getItem;
storagePrototype.setItem = function (key, value) {
  writes.push([key, value]); if (blockedStorage) throw new DOMException('Denied', 'SecurityError');
  return originalSet.call(this, key, value);
};
storagePrototype.getItem = function (key) {
  if (blockedStorage) throw new DOMException('Denied', 'SecurityError');
  return originalGet.call(this, key);
};
globalThis.fetch = async () => { requests++; throw new Error('Theme checks cannot send requests'); };
const { createElement:h, act, useCallback, useState } = await import('react');
const { createRoot } = await import('react-dom/client');
const { MemoryRouter } = await import('react-router');
const { UI2Frame, UI2Sheet } = await import('../src/components/UI2Frame.tsx');
const { UI2AccountPage } = await import('../src/features/account/UI2AccountPage.tsx');
const { HomeCompanion, HomeQuickActions } = await import('../src/components/HomeVisual.tsx');
const { SupportReadStatus } = await import('../src/features/support/SupportApp.tsx');
const { richThemeStorageKey, isRichThemePreference } = await import('../src/state/richTheme.ts');
let submits = 0; let archiveActions = 0;
function Harness({ audience='family', primary='home' }) {
  const [sheet, setSheet] = useState(false);
  const close = useCallback(() => setSheet(false), []);
  return h(UI2Frame, { title:'外观回归', screenId:'THEME-TEST', audience, primary:primary || undefined, onBack:()=>{}, onNavigate:()=>{}, note:'页面说明' },
    h(HomeCompanion, { name:'合成标签', observations:[{label:'记录', value:'未读取'}], onArchive:()=>archiveActions++, onObserve:()=>{} }),
    h(HomeQuickActions, { onObserve:()=>{}, onReports:()=>{}, onTherapies:()=>{} }),
    h('form', { className:'card form-card registration-form', onSubmit:event=>{event.preventDefault();submits++;} },
      h('label', { className:'field' }, h('span', null, '合成输入'), h('input', { 'aria-label':'合成输入', defaultValue:'' })),
      h('button', {type:'submit', className:'button'}, '保存合成输入')),
    h(SupportReadStatus, { state:'permission', error:null, onRefresh:()=>{} }),
    h('button', { type:'button', className:'text-action', 'data-open-sheet':true, onClick:()=>setSheet(true) }, '打开测试面板'),
    sheet && h(UI2Sheet, { title:'测试面板', onClose:close }, h('p', null, '仅为合成面板'), h('button', {onClick:close}, '完成')));
}
const root = createRoot(document.getElementById('root'));
const host = () => document.querySelector('[data-screen-id]');
const page = () => host().shadowRoot;
const accountPage = () => document.querySelector('[data-screen-id="UI2-15"]').shadowRoot;
const selector = () => accountPage().querySelector('.rich-theme-preference');
const choose = async value => { await act(async () => { selector().querySelector(`input[value="${value}"]`).click(); }); };
const system = async dark => { await act(async () => { media.matches=dark; for (const listener of mediaListeners) listener({matches:dark}); }); };
// Keep a registration-like form mounted beside the actual account page so
// changing the real setting must update both roots without remounting the form.
const mount = async props => { await act(async () => root.render(h(MemoryRouter, null, h(Harness, props), h(UI2AccountPage)))); };
const checks = [];
try {
  assert.equal(isRichThemePreference('{"patient_id":"not-a-preference"}'), false);
  assert.equal(isRichThemePreference('DARK'), false);
  await mount({});
  assert.equal(host().dataset.themePreference, 'system'); assert.equal(host().dataset.theme, 'light');
  assert.equal(writes.length, 0); assert.equal(mediaListeners.size, 3);
  const select = selector();
  assert.equal(select.querySelector('legend').textContent, '外观设置');
  assert.deepEqual([...select.querySelectorAll('input[type="radio"]')].map(option=>option.value), ['system','light','dark']);
  assert.equal(select.disabled, false, 'appearance remains available without login or clinical access');
  assert.equal(select.getAttribute('aria-describedby'), null);
  assert.equal(accountPage().querySelectorAll('.rich-theme-preference').length, 1);
  assert.equal(page().querySelector('.rich-theme-preference'), null);
  for (const view of [page(), accountPage()]) {
    assert.equal(view.querySelector('footer'), null);
    assert.ok(!view.textContent.includes('资料与来源共同保留'));
    assert.ok(!view.textContent.includes('仅用于当前设备的界面显示'));
  }
  assert.equal(page().querySelectorAll('.bottom-nav button').length, 3);
  assert.ok(page().querySelector('style').textContent.includes('UI-G04'));
  checks.push('Actual account page alone contains one labelled native system/light/dark selector, available without clinical access; home has no appearance footer or generic helper; navigation remains three items');
  const input = page().querySelector('input[aria-label="合成输入"]'); input.value='合成未保存输入';
  await system(true); assert.equal(host().dataset.theme, 'dark'); assert.equal(page().querySelector('.device').dataset.theme, 'dark');
  await choose('light'); assert.equal(host().dataset.theme, 'light'); assert.equal(window.localStorage.getItem(richThemeStorageKey), 'light');
  await system(false); await system(true); assert.equal(host().dataset.theme, 'light');
  await choose('dark'); assert.equal(host().dataset.theme, 'dark');
  assert.equal(accountPage().querySelector('.device').dataset.theme, 'dark');
  await system(false); assert.equal(host().dataset.theme, 'dark');
  await choose('system'); assert.equal(host().dataset.theme, 'light');
  await system(true); assert.equal(host().dataset.theme, 'dark');
  assert.equal(page().querySelector('input[aria-label="合成输入"]'), input); assert.equal(input.value, '合成未保存输入'); assert.equal(submits, 0);
  checks.push('System changes update automatic mode; explicit modes override system and switching preserves unsaved input without submitting');
  const opener=page().querySelector('[data-open-sheet]');
  await act(async()=>{opener.focus();opener.click();});
  const sheet=page().querySelector('[role=dialog]');
  assert.ok(page().querySelector('.device').contains(sheet)); assert.equal(page().activeElement, sheet.querySelector('button'));
  assert.ok(page().querySelector('.phone-scroll').hasAttribute('inert')); assert.ok(page().querySelector('.bottom-nav').hasAttribute('inert'));
  await system(false); assert.equal(page().querySelector('[role=dialog]'),sheet); assert.equal(host().dataset.theme,'light');
  const sheetButtons=sheet.querySelectorAll('button'); sheetButtons[sheetButtons.length-1].focus();
  await act(async()=>sheet.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true})));
  assert.equal(page().activeElement,sheetButtons[0]);
  await act(async()=>sheet.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true})));
  assert.equal(page().querySelector('[role=dialog]'),null); assert.equal(page().activeElement,opener); assert.equal(page().querySelector('[inert]'),null);
  checks.push('Portaled sheet inherits the active device theme; system change preserves the dialog, Tab trap, inert background and Escape focus restoration');
  await act(async()=>window.dispatchEvent(new StorageEvent('storage',{key:richThemeStorageKey,newValue:'dark'})));
  assert.equal(host().dataset.theme,'dark'); assert.equal(selector().value,'dark');
  await act(async()=>window.dispatchEvent(new StorageEvent('storage',{key:richThemeStorageKey,newValue:'invalid-health-payload'})));
  assert.equal(selector().value,'system'); assert.equal(host().dataset.theme,'light');
  await choose('dark'); await mount({audience:'support',primary:''});
  assert.equal(host().dataset.theme,'dark'); assert.equal(page().querySelector('.device').dataset.audience,'support');
  assert.equal(page().querySelector('.bottom-nav'),null); assert.equal(selector().value,'dark');
  assert.equal(page().querySelector('.rich-theme-preference'),null);
  const help=page().querySelector('.header-help'); await act(async()=>{help.focus();help.click();});
  assert.ok(page().querySelector('[role=dialog][aria-label="页面说明"]'));
  await act(async()=>page().querySelector('.sheet-close').click()); assert.equal(page().activeElement,help);
  checks.push('Support uses the same appearance preference and page-help focus behavior; invalid cross-window storage values fall back to system');
  await act(async()=>root.render(null)); assert.equal(mediaListeners.size,0);
  await mount({}); assert.equal(host().dataset.theme,'dark');
  await act(async()=>root.render(null));
  originalSet.call(window.localStorage,richThemeStorageKey,'{"unexpected":"payload"}');
  await mount({}); assert.equal(selector().value,'system');
  await act(async()=>root.render(null)); blockedStorage=true;
  await mount({}); assert.equal(host().dataset.theme,'light');
  await choose('dark'); assert.equal(host().dataset.theme,'dark');
  await choose('system'); await system(true); assert.equal(host().dataset.theme,'dark');
  assert.ok(writes.every(([key,value])=>key===richThemeStorageKey && ['system','light','dark'].includes(value)));
  assert.equal(window.sessionStorage.length,0); assert.equal(requests,0); assert.equal(submits,0); assert.equal(archiveActions,0);
  checks.push('Valid preference survives remount; malformed saved values and storage denial are safe; only three validated appearance strings can be written, with zero network/clinical actions');
  await act(async()=>root.render(null)); blockedStorage=false; window.localStorage.removeItem(richThemeStorageKey);
  const modernMatchMedia=window.matchMedia; delete window.matchMedia;
  await mount({}); assert.equal(host().dataset.theme,'light'); await choose('dark'); assert.equal(host().dataset.theme,'dark');
  await act(async()=>root.render(null)); window.localStorage.removeItem(richThemeStorageKey);
  const legacyListeners=new Set(); let legacyDark=true;
  window.matchMedia=()=>({ get matches(){return legacyDark;}, addListener(listener){legacyListeners.add(listener);}, removeListener(listener){legacyListeners.delete(listener);} });
  await mount({}); assert.equal(host().dataset.theme,'dark'); assert.equal(legacyListeners.size,3);
  await act(async()=>{legacyDark=false;for(const listener of legacyListeners)listener();}); assert.equal(host().dataset.theme,'light');
  await act(async()=>root.render(null)); assert.equal(legacyListeners.size,0); window.matchMedia=modernMatchMedia;
  checks.push('Absent matchMedia falls back safely; legacy media listeners update system mode and are cleaned up');
  // Check declared source contracts, not browser-computed appearance.
  const css=readFileSync(new URL('../src/styles/rich-theme.css',import.meta.url),'utf8');
  const tokens=JSON.parse(readFileSync(new URL('../../ui-delivery/tokens.json',import.meta.url),'utf8'));
  const palette=tokens.color.dark;
  for (const [variable,value] of Object.entries({'--ink':palette.text.primary,'--muted':palette.text.secondary,'--blue':palette.action.primary,'--link':palette.action.text,'--page':palette.surface.base,'--card':palette.surface.raised,'--tint':palette.action.muted,'--line':palette.border.separator,'--control':palette.border.control,'--warning':palette.feedback.warning,'--warning-surface':palette.feedback.warningSurface,'--error':palette.feedback.error,'--success':palette.feedback.success,'--dim-surface-top':tokens.material3d.dark.surfaceTop,'--dim-surface-bottom':tokens.material3d.dark.surfaceBottom,'--dim-edge':tokens.material3d.dark.edge})) {
    assert.ok(css.toLowerCase().includes(`${variable}:${value.toLowerCase()}`),`${variable} must retain the original approved dark token`);
  }
  for (const surface of ['.home-twin','.home-child-stage','.home-entry-grid>button','.symptom-columns>button','.field input','.field select','.field textarea','.registration-disclosure','.support-banner','.rich-read-state.status-panel','.sheet','.bottom-nav','.summary-paper']) assert.ok(css.includes(surface),`Missing theme surface: ${surface}`);
  for (const mediaRule of ['prefers-contrast:more','forced-colors:active','prefers-reduced-motion:reduce']) assert.ok(css.includes(mediaRule));
  assert.ok(css.includes('.device .sheet { max-height:88dvh; }'));
  assert.ok(css.includes('.solo .device[data-primary=home] .phone-scroll>.app-header{position:static}'));
  assert.ok(css.includes('.device[data-theme=dark] .home-child-stage { background:radial-gradient(ellipse at 50% 70%,#b2d8ebaf,transparent 68%); }'));
  assert.ok(css.includes('.device[data-primary=home] .app-header:after{display:none}'));
  const richCss=readFileSync(new URL('../src/styles/rich-ui.css',import.meta.url),'utf8');
  assert.match(richCss,/\.app-header:after\{[^}]*height:46%;[^}]*pointer-events:none;z-index:-1/);
  assert.ok(css.includes('transform:none; translate:none;')); assert.ok(!css.includes('forced-color-adjust:none'));
  // A late matching string is insufficient: the :is() branch with two classes
  // raises the ordinary dark-card rule to specificity [0,4,0]. Resolve the
  // actual matched background declarations using CSS selector AST specificity.
  assert.deepEqual(selectorSpecificity('.device[data-theme=dark] :is(.home-twin,.brand-footer.rich-brand-footer)'), [0,4,0]);
  assert.deepEqual(selectorSpecificity('.device[data-theme=dark] .home-twin'), [0,3,0]);
  const materialFixture=document.createElement('div'); materialFixture.className='solo';
  materialFixture.innerHTML='<div class="device" data-theme="dark" data-primary="home"><div class="home-twin"></div></div>';
  const materialCard=materialFixture.querySelector('.home-twin');
  const applicationCss=[readFileSync(new URL('../../ui-delivery/v2/src/styles.css',import.meta.url),'utf8'),richCss,readFileSync(new URL('../src/styles/rich-pages.css',import.meta.url),'utf8'),readFileSync(new URL('../src/styles/rich-registration.css',import.meta.url),'utf8'),css].join('\n');
  for (const theme of ['light','dark']) {
    materialFixture.firstElementChild.dataset.theme=theme;
    const normal=winningDeclaration(applicationCss,materialCard,'background');
    assert.equal(normal.value,'linear-gradient(155deg,var(--dim-surface-top),color-mix(in srgb,var(--dim-surface-bottom) 80%,#b2d7ef))',`${theme} actual cascade must select the original mixed lower material`);
    const contrast=winningDeclaration(applicationCss,materialCard,'background',query=>query.includes('prefers-contrast:more'));
    assert.equal(contrast.value,'var(--card)'); assert.equal(contrast.important,true);
  }
  materialFixture.firstElementChild.dataset.theme='dark';
  const regressedCss=applicationCss.replaceAll('.device[data-theme][data-primary=home] .home-twin{','.device[data-theme=dark] .home-twin{');
  assert.equal(winningDeclaration(regressedCss,materialCard,'background').value,'linear-gradient(153deg,var(--dim-surface-top),var(--dim-surface-bottom))','restoring the old weak selector must reproduce the :is() cascade failure');
  materialCard.innerHTML='<div class="home-twin-heading"><details class="home-patient"><summary><span class="home-patient-copy">合成样本</span><svg class="three-d-icon" width="14" height="14"><image href="/assets/master/utility-3d-icons.png"/></svg></summary></details><button>健康档案<svg class="three-d-icon" width="14" height="14"><image href="/assets/master/utility-3d-icons.png"/></svg></button></div><div class="home-child-view"><img alt="合成医学图示"/></div><div class="symptom-columns"><button><svg class="three-d-icon"/></button></div>';
  const archiveControl=materialCard.querySelector('.home-twin-heading>button');
  const patientChevron=materialCard.querySelector('summary>.three-d-icon');
  const archiveGlyph=archiveControl.querySelector('.three-d-icon');
  assert.equal(winningDeclaration(applicationCss,archiveControl,'color').value,'#83baff');
  for (const glyph of [patientChevron,archiveGlyph]) {
    assert.equal(winningDeclaration(applicationCss,glyph,'filter').value,'brightness(2.4) saturate(.7)');
    assert.equal(glyph.getAttribute('width'),'14'); assert.equal(glyph.getAttribute('height'),'14');
    assert.match(glyph.querySelector('image').getAttribute('href'),/utility-3d-icons/);
    assert.equal(winningDeclaration(applicationCss,glyph,'filter',query=>query.includes('forced-colors:active')).value,'none');
  }
  assert.equal(winningDeclaration(applicationCss,materialCard.querySelector('.home-child-view>img'),'filter'),undefined);
  assert.equal(winningDeclaration(applicationCss,materialCard.querySelector('.symptom-columns .three-d-icon'),'filter'),undefined);
  assert.equal(winningDeclaration(applicationCss,archiveControl,'color',query=>query.includes('prefers-contrast:more')).value,'var(--ink)');
  assert.equal(winningDeclaration(applicationCss,archiveControl,'color',query=>query.includes('forced-colors:active')).value,'ButtonText');
  materialFixture.firstElementChild.dataset.theme='light';
  assert.equal(winningDeclaration(applicationCss,archiveControl,'color').value,'var(--link)');
  assert.equal(winningDeclaration(applicationCss,patientChevron,'filter'),undefined);
  const luminance=rgb=>rgb.map(value=>value/255).map(value=>value<=0.04045?value/12.92:((value+0.055)/1.055)**2.4).reduce((sum,value,index)=>sum+value*[0.2126,0.7152,0.0722][index],0);
  const ratio=(a,b)=>(Math.max(luminance(a),luminance(b))+0.05)/(Math.min(luminance(a),luminance(b))+0.05);
  // Actual prior synthetic-browser background samples; this bounds the chosen
  // text color only, not future browser compositing or antialiased icon pixels.
  for(const background of [[50,67,87],[51,68,88],[51,69,83],[50,68,90]]) assert.ok(ratio([131,186,255],background)>=4.5);
  assert.ok(ratio([100,173,255],[50,67,87])<4.5);
  checks.push('Dark-home utility contrast scope: actual cascade selects lighter link and only two atlas glyph filters; light/medical art unchanged; high-contrast/forced colors win; sampled-background text calculation exceeds4.5');
  checks.push('CSS AST cascade regression: both theme card backgrounds actually win over higher-specificity :is() branches, while high-contrast flattening still wins');
  checks.push('Source contracts match original dark tokens/materials and cover home/form/support/sheet surfaces plus high contrast, forced colors and reduced-motion rules');
} finally {
  await act(async()=>root.unmount()); assert.equal(mediaListeners.size,0); dom.window.close();
}
console.log(JSON.stringify({status:'PASS',mode:'SYNTHETIC_NONVISUAL_DOM_AND_SOURCE_CONTRACTS_NOT_PIXEL_EVIDENCE',checks},null,2));
