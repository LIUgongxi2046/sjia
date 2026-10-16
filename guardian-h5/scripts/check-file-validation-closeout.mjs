// No browser, provider or bytes from outside this synthetic test.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
const {completeValidatedFileGroup}=await import('../src/api/fileValidation.ts');
const groupId=randomUUID(),fileId=randomUUID(),patientId=randomUUID(),intentId=randomUUID(),taskId=randomUUID();
const group={group_id:groupId,patient_id:patientId,version:'1',status:'initiated',authorization_id:randomUUID(),source_revision_id:randomUUID(),
 files:[{file_id:fileId,ordinal:1,media_type:'image/png',purpose:'original',checksum:'a'.repeat(64),size_bytes:12,page_count:1}]};
const intent={intent_id:intentId,request_id:groupId,generation:'1',status:'prepared',expires_at:'2099-01-01T00:00:00Z',payload_sha256:'a'.repeat(64)};
const task=status=>({task_id:taskId,task_type:'file_validation',request_id:groupId,accepted_at:'2026-10-11T00:00:00Z',status,status_version:status==='completed'?'3':status==='running'?'2':'1',
 poll_after_ms:['accepted','queued','running'].includes(status)?1:null,allowed_actions:['accepted','queued','running'].includes(status)?['task_cancel']:[],
 result_type:status==='completed'?'file_group':null,result_ref:status==='completed'?groupId:null,error_code:null});
const target=`/api/uploads/${groupId}/complete`;
let posts=0,polls=0,preparations=[],lose=false,wrong=false,deny=false,neverFinish=false,regress=false;
globalThis.fetch=async(path,options={})=>{
 if(path===`/api/uploads/${groupId}`)return Response.json(group);
 if(path==='/api/write-intents'){const data=JSON.parse(options.body);preparations.push(data);assert.deepEqual(data.payload,{expected_version:'1',file_ids:[fileId]});assert.equal(data.request_id,groupId);return Response.json(intent);}
 if(path===target){posts++;assert.equal(options.headers['Idempotency-Key'],groupId);if(lose){lose=false;throw new TypeError('SYNTHETIC LOST ACCEPTANCE');}return Response.json({...task('accepted'),...(regress?{status_version:'3'}:{})});}
 if(path===`/api/tasks/${taskId}`){polls++;if(deny)return Response.json({code:'CASE_ACTION_DENIED',message:'Synthetic revoked scope',retryable:false,recovery:'none',field_errors:[]},{status:403});return Response.json(neverFinish?task('running'):{...task('completed'),...(regress?{status_version:'2'}:{}),...(wrong?{result_ref:randomUUID()}: {})});}
 throw new Error(`Unexpected ${path}`);
};
assert.deepEqual(await completeValidatedFileGroup(groupId),{group_id:groupId,status:'available'});assert.equal(posts,1);assert.equal(polls,1);
lose=true;await assert.rejects(completeValidatedFileGroup(groupId));await completeValidatedFileGroup(groupId);assert.deepEqual(preparations[1],preparations[2]);assert.equal(posts,3);
wrong=true;await assert.rejects(completeValidatedFileGroup(groupId),/不一致/);wrong=false;
deny=true;await assert.rejects(completeValidatedFileGroup(groupId),/revoked/);deny=false;
const before=posts;await assert.rejects(completeValidatedFileGroup(groupId,undefined,undefined,randomUUID()),/原件组/);assert.equal(posts,before);
regress=true;await assert.rejects(completeValidatedFileGroup(groupId),/倒退/);regress=false;
neverFinish=true;const controller=new AbortController();const running=completeValidatedFileGroup(groupId,undefined,controller.signal);setTimeout(()=>controller.abort(),5);await assert.rejects(running,error=>error.name==='AbortError');
assert.equal(new Set(preparations.map(value=>value.request_id)).size,1);
console.log(JSON.stringify({status:'PASS',mode:'SYNTHETIC_TASK_POLLING',checks:['upload completion waits for exact file-validation task','lost acceptance resubmits same frozen group/version/files/request key','wrong result group rejected','revoked scope stops polling','caller abort cancels polling without declaring success','selected patient mismatch is rejected before preparing a write','older task status_version cannot replace newer progress']},null,2));
