// Production HLH schemas, adapters, creation hook and components under JSDOM.
// All API responses and identities below are synthetic software test data.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';
registerHooks({ load(url, context, nextLoad) {
  if (/\.(png|webp|jpg|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/support/assessment' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLSelectElement', 'HTMLButtonElement', 'ShadowRoot', 'Node', 'KeyboardEvent', 'Event'])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
// JSDOM has no layout engine; expose a synthetic visible rect for focus trapping only.
HTMLElement.prototype.getClientRects = function () { return this.hidden ? [] : [{ width: 1, height: 1 }]; };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { HlhReferenceResult } = await import('../src/features/support/HlhReferenceResult.tsx');
const { AssessmentPage } = await import('../src/features/support/AssessmentPage.tsx');
const { validateApiValue, checkAssessmentSchemas } = await import('../src/api/schema.ts');
const { readAssessment, assessmentPlanKey, readAssessmentCreationOptions, assessmentTarget } = await import('../src/api/assessments.ts');
const { validateHlhReference } = await import('../src/api/hlh.ts');
const schema = 'v2-protocol.schema.json#/$defs/';
checkAssessmentSchemas();
const id = randomUUID, point = '2026-10-01T12:00:00Z';
const patient = id(), assessment = id(), taskId = id(), actor = id();
const fact = Object.fromEntries(['applicability','molecular','fever','splenomegaly','hemoglobin','platelet_count',
  'neutrophil_count','triglycerides','triglycerides_fasting','fibrinogen','hemophagocytosis','no_malignancy_evidence',
  'nk_activity','ferritin','soluble_cd25','age_at_hemoglobin'].map(code => [code, id()]));
const condition = (code, state) => ({ state, reason_codes: state === 'unknown' ? ['SYNTHETIC_UNKNOWN'] : [], fact_version_ids: [fact[code]] });
const criterion = (code, state, subs = {}) => ({ ...condition(code, state), code, subconditions: subs });
const all = (...conditions) => [...new Set(conditions.flatMap(item => item.fact_version_ids))];
const cyto = { hemoglobin: condition('hemoglobin','unknown'), age_at_hemoglobin: condition('age_at_hemoglobin','met'),
  platelet_count: condition('platelet_count','met'), neutrophil_count: condition('neutrophil_count','not_met') };
const lipid = { triglycerides_numeric: condition('triglycerides','met'), fasting: condition('triglycerides_fasting','unknown'),
  fasting_triglycerides: condition('triglycerides','unknown'), fibrinogen: condition('fibrinogen','not_met') };
const histology = { hemophagocytosis: condition('hemophagocytosis','unknown'), no_malignancy_evidence: condition('no_malignancy_evidence','met') };
const result = { schema_version: 'hlh-2004-result-1', kind: 'hlh_2004_reference', use_boundary: 'offline_reference_support_only',
  clinical_use_authorized: false, policy_revision_id: id(), policy_checksum: 'a'.repeat(64), input_checksum: 'b'.repeat(64),
  source_table: 'histiocyte-society-2004-table-2', source_revision_id: id(), semantics_version: 'hlh-2004-bounds-1',
  assessment_at: point, known_at: point, applicability: 'applicable', applicability_condition: condition('applicability','met'),
  molecular_condition: condition('molecular','not_met'), criteria: [criterion('fever','met'), criterion('splenomegaly','met'),
    { ...criterion('cytopenias','unknown',cyto), fact_version_ids: all(...Object.values(cyto)) },
    { ...criterion('lipids_or_fibrinogen','unknown',lipid), fact_version_ids: all(...Object.values(lipid)) },
    { ...criterion('hemophagocytosis','unknown',histology), fact_version_ids: all(...Object.values(histology)) },
    criterion('nk_activity','met'), criterion('ferritin','met'), criterion('soluble_cd25','unknown')],
  criteria_met_lower_bound: 4, criteria_met_upper_bound: 8, criteria_path: 'unknown', molecular_or_criteria: 'unknown',
  reference_result: 'insufficient', supported_paths: [], limitation_codes: ['HLH_REFERENCE_SUPPORT_NOT_DIAGNOSIS'] };
const view = { schema_version: 'static-assessment-view-2', assessment_id: assessment, patient_id: patient, kind: 'hlh',
  assessment_at: point, known_at: point, mode: 'current', task: { task_id: taskId, task_type: 'assessment', request_id: null,
    accepted_at: point, status: 'completed', status_version: '2', poll_after_ms: null, allowed_actions: [], result_type: 'assessment',
    result_ref: assessment, error_code: null }, computation_status: 'insufficient', validity: 'valid', missing_items: [],
  rule_id: id(), window_policy_id: id(), input_sha256: 'c'.repeat(64), implementation_sha256: 'd'.repeat(64),
  context_states: { population: 'not_required_by_policy', therapy: 'unknown', clinical_finding: 'not_required_by_policy', late_result: 'not_required_by_policy' },
  limitations: [], applicability: 'applicable', computed_at: point,
  inputs: Object.entries(fact).map(([code, factId]) => ({ variable_code: code, input_snapshot: { role: 'current', value: '1', unit: null,
    reference: { fact_version_id: factId, record_version_id: id(), field_id: id(), verification_id: id(), normalization_id: null,
      medical_time_id: id(), source_revision_id: id(), document_version_id: null, page_id: null } } })),
  exclusions: [], clinical_selections: [], output: { classification_conditions: null, classification_result: null,
    ms_contributions: null, ms_total: null, ms_threshold_result: null, hlh_result: result }, reason_code: null };
const optionsConfig = () => ({ config_id: id(), config_key: 'SYNTHETIC-HLH', version: '1', checksum: 'a'.repeat(64) });
const plan = { kind: 'hlh', purpose: 'software_test', rule: optionsConfig(), input_rule: optionsConfig(), window: optionsConfig(), verification: optionsConfig(),
  selection_variables: [], selection_evidence: [], prior_selections: [] };
const options = { schema_version: 'assessment-creation-options-2', patient_id: patient, display_id: 'SYNTHETIC ONLY', known_at: point, mode: 'current', plans: [plan], observation_periods: [] };
let response = view, calls = [];
globalThis.fetch = async (path, options = {}) => { calls.push({path, options}); return Response.json(response); };
validateApiValue(schema+'AssessmentView',view); validateHlhReference(view);
assert.equal((await readAssessment(assessment)).output.hlh_result.reference_result,'insufficient');
console.log('PASS production adapter accepts independent HLH unknown bounds and preserves typed evidence');
for (const change of [
  value => value.output.hlh_result.criteria_met_lower_bound = 5,
  value => value.output.hlh_result.criteria[1].code = 'fever',
  value => value.output.hlh_result.reference_result = 'criteria_not_supported',
  value => value.output.hlh_result.criteria[2].subconditions.neutrophil_count.state = 'met',
  value => value.output.hlh_result.molecular_condition.fact_version_ids = [id()],
  value => value.output.hlh_result.clinical_use_authorized = true,
  value => value.output.ms_total = '3',
  value => value.output.hlh_result = null,
]) {
  response = structuredClone(view); change(response); await assert.rejects(() => readAssessment(assessment));
}
response = structuredClone(view); response.kind='ms'; response.output.ms_contributions={};
await assert.rejects(() => readAssessment(assessment));
console.log('PASS adapter rejects contradictory counts, compound inflation, stale evidence, mixed paths and fabricated approval');
const identity = { operation_id: 'assessment_v2_create', target_key: `/support/patients/${patient}/assessments`, request_id: id(), schema_version: '2.0.0',
  payload: {kind:'hlh',assessment_at:point,known_at:point,mode:'current',rule_id:plan.rule.config_id,input_rule_id:plan.input_rule.config_id,
    window_policy_id:plan.window.config_id,verification_policy_id:plan.verification.config_id,observation_period_id:null} };
assessmentTarget(identity);
const without = structuredClone(identity); delete without.payload.input_rule_id; assert.throws(() => assessmentTarget(without));
const legacy = structuredClone(identity); legacy.payload.kind='classification'; assert.throws(() => assessmentTarget(legacy));
delete legacy.payload.input_rule_id; assessmentTarget(legacy);
const alternative = structuredClone(plan); alternative.input_rule.config_id=id();
assert.notEqual(assessmentPlanKey(plan),assessmentPlanKey(alternative));
response = {...options,plans:[plan,alternative]}; assert.equal((await readAssessmentCreationOptions(patient,point,'current')).plans.length,2);
console.log('PASS HLH plan/input binding is required and independent without changing legacy requests');
let root;
const flush = async () => act(async () => { await new Promise(done => setTimeout(done,5)); });
async function mount(element) { root=createRoot(document.getElementById('root')); await act(async () => root.render(element)); await flush(); }
async function unmount() { await act(async () => root.unmount()); document.getElementById('root').replaceChildren(); }
await mount(h(HlhReferenceResult,{result}));
assert.equal(document.querySelectorAll('ol[aria-label="八项参考条件"] > li').length,8);
assert.match(document.body.textContent,/4 \/ 8/); assert.match(document.body.textContent,/8 \/ 8/);
assert.match(document.body.textContent,/未满足标准不排除 HLH 或 MAS/);
assert.match(document.body.textContent,/不代表发生概率/); assert.match(document.body.textContent,/资料不足，暂不能判定/);
assert.ok(document.body.textContent.includes(result.source_revision_id));
await unmount();
console.log('PASS actual component renders all8 conditions, subconditions, source trace and uncertainty caveats');
response=view;
const session = {principal_id:actor,capabilities:[{capability_id:'read_assessment',state:'available'},{capability_id:'execute_assessment',state:'available'}]};
let router=createMemoryRouter([{path:'*',element:h(AssessmentPage,{session,accessState:'default',accessError:null,onRefresh:()=>{}})}],
  {initialEntries:[`/support/assessment?assessment_id=${assessment}&patient_id=${patient}&evaluation_kind=hlh`]});
await mount(h(RouterProvider,{router})); await flush();
let screen=document.querySelector('[data-screen-id="UI2-35"]').shadowRoot;
assert.equal(screen.querySelector('[role=tab][aria-selected=true]').textContent,'HLH-2004');
assert.equal(screen.querySelectorAll('ol[aria-label="八项参考条件"] > li').length,8);
const ms=[...screen.querySelectorAll('[role=tab]')].find(node=>node.textContent==='MS 评分');
await act(async()=>Simulate.click(ms)); await flush();
assert.equal(screen.querySelector('[aria-label="HLH-2004 独立参考评估"]'),null);
// This is an authorized, loaded HLH result viewed under the MS tab, not an empty
// response or an access denial. Keep the path separation explicit.
assert.match(screen.textContent,/当前选择尚未取得可显示的评估结果，请核对结果类型与任务状态。/);
assert.doesNotMatch(screen.textContent,/尚无有效评估/);
assert.equal(screen.querySelector('.rich-read-state.permission'),null);
assert.equal(screen.querySelector('[role=tab][aria-selected=true]').textContent,'MS 评分');
await unmount(); router.dispose();
console.log('PASS actual page exposes dedicated HLH tab and does not leak its output into MS');
// The real creation hook selects approved plans and submits exactly that binding.
const acceptedTask={...view.task,task_id:id(),request_id:null,status:'accepted',status_version:'1',poll_after_ms:1000,result_type:null,result_ref:null};
const intentId=id(); let submitted;
globalThis.fetch=async(path,request={})=>{
  calls.push({path,options:request});
  if(String(path).includes('/clinical-scopes?')) return Response.json({schema_version:'clinical-directory-2',action:'execute_assessment',patients:[{patient_id:patient,display_id:'SYNTHETIC ONLY',purposes:['software_test'],records:[]}]});
  if(String(path).includes('/assessment-options?')) return Response.json(options);
  if(path==='/api/v2/write-intents') { submitted=JSON.parse(request.body); acceptedTask.request_id=submitted.request_id;
    return Response.json({intent_id:intentId,request_id:submitted.request_id,generation:'1',status:'prepared',expires_at:'2099-01-01T00:00:00Z',payload_sha256:'e'.repeat(64)}); }
  if(path===`/api/v2/support/patients/${patient}/assessments`) return Response.json(acceptedTask,{status:202});
  if(String(path).includes('/assessment-tasks/')) return Response.json(acceptedTask);
  if(path==='/api/auth/bootstrap') return Response.json({csrf_token:'synthetic'});
  throw new Error(`Unexpected synthetic endpoint ${path}`);
};
router=createMemoryRouter([{path:'*',element:h(AssessmentPage,{session,accessState:'default',accessError:null,onRefresh:()=>{}})}],
  {initialEntries:[`/support/assessment?patient_id=${patient}&evaluation_kind=hlh`]});
await mount(h(RouterProvider,{router}));
screen=document.querySelector('[data-screen-id="UI2-35"]').shadowRoot;
await act(async()=>Simulate.click([...screen.querySelectorAll('button')].find(node=>node.textContent.includes('观察时段与来源')))); await flush();
const fieldByLabel=text=>[...screen.querySelectorAll('label')].find(node=>node.textContent.includes(text));
for(const text of ['评估时点','资料截止时点']) await act(async()=>Simulate.change(fieldByLabel(text).querySelector('input'),{target:{value:point}}));
await act(async()=>Simulate.click([...screen.querySelectorAll('button')].find(node=>node.textContent==='读取准确批准版本'))); await flush();
await act(async()=>Simulate.change(fieldByLabel('获准规则与窗口').querySelector('select'),{target:{value:assessmentPlanKey(plan)}}));
const submit=[...screen.querySelectorAll('button')].find(node=>node.textContent==='创建评估任务' && !node.disabled);
assert.ok(submit,'HLH creation has an available explicit submit button');
await act(async()=>Simulate.click(submit)); await flush();
assert.equal(submitted.payload.kind,'hlh'); assert.equal(submitted.payload.input_rule_id,plan.input_rule.config_id);
assert.equal(submitted.payload.rule_id,plan.rule.config_id); assert.equal(submitted.payload.molecular,undefined);
await unmount(); router.dispose();
console.log('PASS actual creation hook sends approved HLH binding only; it does not accept a client molecular assertion');
console.log('HLH frontend regression checks passed. Synthetic only; no visual or clinical acceptance claimed.');
