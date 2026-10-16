// Pure production chart-link projection; no browser automation or chart rendering.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
const { analyteComparisonGroups } = await import('../src/components/analyteComparisons.ts');
const { validateAnalyteRead } = await import('../src/api/recordSearchSchema.ts');
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const raw = (n, value, unit) => ({field_id:id(n),record_version_id:id(n+10),original_name:'synthetic',value_state:'known',raw_numeric:value,raw_text:null,qualifier:'eq',raw_unit:unit,method:'synthetic-method',medical_time:null,medical_time_id:null,direction:null,comparison:null,not_comparable_reason:null,comparison_reasons:[],origin_type:'self_report',source_organization:null,verification_states:[]});
const basis = {kind:'approved_mapping',previous_field_id:id(1),previous_value:'20',current_value:'25',unit:'small',method:'synthetic-method',config_id:id(90),config_checksum:'a'.repeat(64),previous_mapping_id:id(91),current_mapping_id:id(92),previous_source_revision_id:id(93),current_source_revision_id:id(93)};
const trend = [raw(1,'2','large'), {...raw(2,'25','small'),direction:'up',comparison:basis},raw(3,'99','small')];
validateAnalyteRead({patient_id:id(100),concept_code:'synthetic',trend});
let groups=analyteComparisonGroups(trend);
assert.equal(groups.length,1);assert.deepEqual(groups[0][1].values,[20,25,null]);assert.deepEqual(groups[0][1].links,[false,true,false]);assert.equal(trend[0].raw_numeric,'2');
assert.equal(analyteComparisonGroups([trend[0],{...trend[1],comparison:{...basis,previous_field_id:id(99)}}]).length,0);
assert.equal(analyteComparisonGroups([trend[0],{...trend[1],direction:null,comparison:null}]).length,0);
assert.equal(analyteComparisonGroups([trend[0],{...trend[1],comparison:{...basis,current_value:'Infinity'}}]).length,0);
console.log(JSON.stringify({status:'PASS',mode:'SYNTHETIC_NONVISUAL',checks:['mapped links use exact comparison values and preserve raw DTO','unrelated predecessor cannot form a link','unqualified gaps remain disconnected','nonfinite plotting values are rejected','actual DTO JSON schema accepts explicit mapping provenance']},null,2));
