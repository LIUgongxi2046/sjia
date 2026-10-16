/** Real compiler API: synthetic positive/negative declarations and actual app bindings. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as ts from '../../../guardian-h5/node_modules/typescript/dist/api/sync/api.js';
import * as ast from '../../../guardian-h5/node_modules/typescript/dist/ast/is.js';
import { exportedType, checkObject } from '../../check-v2-types.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..'), here=path.join(root,'.planning-work/c02-contract-tests');
const config=path.join(here,'tsconfig.json'), appConfig=path.join(root,'guardian-h5/tsconfig.json');
const api=new ts.API({cwd:root});
const snapshot=api.updateSnapshot({openProjects:[config,appConfig]});
try {
 const p=snapshot.getProject(config), app=snapshot.getProject(appConfig);
 const fixture=path.join(here,'strict-contract-fixtures.ts'), content=fs.readFileSync(fixture,'utf8');
 const diagnostics=[...p.program.getSyntacticDiagnostics(),...p.program.getSemanticDiagnostics(),...p.program.getProgramDiagnostics()];
 const manifest=JSON.parse(fs.readFileSync(path.join(here,'strict-fixtures-manifest.json'),'utf8'));
 const bad=new Map();
 for(const d of diagnostics) {
  assert.equal(d.fileName,fixture,JSON.stringify(d));
  const line=content.slice(0,d.pos).split('\n').length;
  const c=manifest.find(c=>c.line===line);assert.ok(c,JSON.stringify(d));assert.equal(c.valid,false,JSON.stringify(d));
  bad.set(c.id,(bad.get(c.id)||0)+1);
 }
 for(const c of manifest) assert.equal(bad.has(c.id),!c.valid,c.label);
 const appDiagnostics=[...app.program.getSyntacticDiagnostics(),...app.program.getSemanticDiagnostics(),...app.program.getProgramDiagnostics()];
 assert.deepEqual(appDiagnostics,[]);
 const expected={
  'guardian-h5/src/api/verifications.ts':{VerificationCreationQuery:1,VerificationRequestLookup:1},
  'guardian-h5/src/api/assessments.ts':{AssessmentCreationQuery:1,ClinicalDirectoryQuery:2},
  'guardian-h5/src/api/dynamic.ts':{AssessmentCreationQuery:1,DynamicRequestLookup:1,DynamicTriggerQuery:2},
  'guardian-h5/src/features/support/VerificationOriginals.tsx':{VerificationOriginalQuery:1},
 };
 const checker=app.checker;
 const v2=app.program.getSourceFile(path.join(root,'frontend-design/v2-types.ts'));
 const exports=checker.getExportsOfModule(checker.getSymbolAtLocation(v2));
 const unalias=s=>s?.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(s) : s;
 let actualBindings=0;
 for(const [file,names] of Object.entries(expected)) {
  const source=app.program.getSourceFile(path.join(root,file)), found={};
  function visit(n) {
   if(ast.isSatisfiesExpression(n)) {
    const symbol=unalias(checker.getSymbolAtLocation(n.type.typeName));
    if(symbol && symbol.name in names) {
     assert.equal(symbol.id,exports.find(s=>s.name===symbol.name).id,`${file}: accurate shared symbol`);
     assert.equal(checker.isTypeAssignableTo(checker.getTypeAtLocation(n.expression),checker.getTypeAtLocation(n.type)),true);
     found[symbol.name]=(found[symbol.name]||0)+1;actualBindings++;
    }
   }
   n.forEachChild(visit);
  }
  visit(source);assert.deepEqual(found,names,file);
 }
 const endpoints=app.program.getSourceFile(path.join(root,'guardian-h5/src/api/endpoints.ts'));
 let receiptBindings=0;
 function visit(n) {
  if(ast.isTypeReferenceNode(n) && n.typeName.text==='UploadGroupReceipt') {
   assert.equal(unalias(checker.getSymbolAtLocation(n.typeName)).id,exports.find(s=>s.name==='UploadGroupReceipt').id);receiptBindings++;
  }
  n.forEachChild(visit);
 }
 visit(endpoints);assert.equal(receiptBindings,2);
 const desc=JSON.parse(fs.readFileSync(path.join(root,'.planning-work/t38-types-description.json'),'utf8'));
 for(const name of ['UploadGroupReceipt','VerificationCreationQuery','VerificationOriginalQuery','VerificationRequestLookup','AssessmentCreationQuery','ClinicalDirectoryQuery','DynamicRequestLookup','DynamicTriggerQuery','AssessmentObservationTime'])
  checkObject(exportedType(app.program,checker,path.join(root,'frontend-design/v2-types.ts'),name),desc[name],checker,name);
 checkObject(exportedType(app.program,checker,path.join(root,'frontend-design/v2-types.ts'),'AssessmentViewWire'),desc.AssessmentView,checker,'AssessmentViewWire');
 fs.writeFileSync(path.join(here,'strict-diagnostics.json'),JSON.stringify(diagnostics,null,2)+'\n');
 console.log(JSON.stringify({status:'PASS',strict_cases:manifest.length,positive_cases:manifest.filter(x=>x.valid).length,negative_cases:bad.size,actual_satisfies_bindings:actualBindings,actual_receipt_return_bindings:receiptBindings,exact_shared_schema_types:10},null,2));
} finally {api.close();}
