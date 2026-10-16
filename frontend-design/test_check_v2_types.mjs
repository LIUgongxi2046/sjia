/** Pure TypeScript contract-tool regressions. No browser, server, database, or network. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as ts from '../guardian-h5/node_modules/typescript/dist/api/sync/api.js';
import { exportedType, selectType, checkObject, checkUnion, schemaCallType } from './check-v2-types.mjs';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const work = path.join(root, '.planning-work');
fs.mkdirSync(work, { recursive: true });
const directory = fs.mkdtempSync(path.join(work, 'c02-types-test-'));
const main = path.join(directory, 'main.ts');
const exports = path.join(directory, 'exports.ts');
const config = path.join(directory, 'tsconfig.json');
const validator = path.join(directory, 'validator.ts');
fs.writeFileSync(validator, 'export declare function validateApiValue<T>(schema: string, value: unknown): T;\n');
fs.writeFileSync(main, `
import { validateApiValue, validateApiValue as realValidateApiValue } from './validator';
export interface Item { id: string; payload?: unknown }
export interface WrongUnknown { id: string; payload?: any }
export interface WrongObject { id: string; payload?: object }
export interface RequiredPayload { id: string; payload: unknown }
export type RequiredUndefined = { id: string | undefined; payload?: unknown };
export type HiddenUnionProperties = { id: string; payload?: unknown; a: string } | { id: string; payload?: unknown; b: number };
export type Conditional = { id: string; payload?: string } | { id: string; payload: string };
export type ConditionalUnknown = { kind: 'a'; payload: unknown } | { kind: 'b'; payload: string };
export type ConditionalAny = { kind: 'a'; payload: unknown } | { kind: 'b'; payload: any };
export interface Outer { items: Item[]; maybe: Item | null }
export type Choice = { kind: 'a'; id: string } | { kind: 'b'; count: number };
export type ExtraChoice = Choice | { kind: 'c'; count: number };
export type Ambiguous = { kind: 'a'; id: string } | { kind: 'a'; count: number };
export interface A { kind: 'a'; id: string }
export interface B { kind: 'b'; count: number }
validateApiValue<Item>('synthetic#/$defs/Item', null);
validateApiValue<Item>('synthetic#/$defs/Duplicate', null);
validateApiValue<Item>('synthetic#/$defs/Duplicate', null);
realValidateApiValue<Item>('synthetic#/$defs/Alias', null);
realValidateApiValue<{ wrong: number }>('synthetic#/$defs/Shadowed', null);
export function shadow(validateApiValue: <T>(schema: string, value: unknown) => T) {
  return validateApiValue<Item>('synthetic#/$defs/Shadowed', null);
}
export function fakeOnly(validateApiValue: <T>(schema: string, value: unknown) => T) {
  return validateApiValue<Item>('synthetic#/$defs/FakeOnly', null);
}
`);
fs.writeFileSync(exports, "export type { Item as Renamed } from './main';\n");
fs.writeFileSync(config, JSON.stringify({ compilerOptions: { strict: true, noEmit: true, target: 'ES2022', module: 'ESNext', moduleResolution: 'Bundler' }, include: ['./*.ts'] }));
const api = new ts.API({ cwd: root });
const snapshot = api.updateSnapshot({ openProjects: [config] });
let tests = 0;
try {
  const project = snapshot.getProject(config);
  const { program, checker } = project;
  assert.deepEqual([...program.getSyntacticDiagnostics(), ...program.getSemanticDiagnostics()], []);
  const get = name => exportedType(program, checker, main, name);
  const item = { required: ['id'], properties: { id: { kind: 'types', values: ['string'] }, payload: { kind: 'unknown' } } };
  assert.equal(checkObject(get('Item'), item, checker, 'Item'), 2); tests++;
  for (const name of ['WrongUnknown', 'WrongObject', 'RequiredPayload', 'RequiredUndefined', 'HiddenUnionProperties']) {
    assert.throws(() => checkObject(get(name), item, checker, name)); tests++;
  }
  assert.equal(checkObject(exportedType(program, checker, exports, 'Renamed'), item, checker, 'Renamed'), 2); tests++;
  assert.throws(() => exportedType(program, checker, exports, 'Missing'), /绑定导出缺少/); tests++;
  assert.equal(checkObject(selectType(get('Outer'), [{ property: 'items' }, 'items'], checker), item, checker, 'Outer.items'), 2); tests++;
  assert.equal(checkObject(selectType(get('Outer'), ['maybe', 'nonNullable'], checker), item, checker, 'Outer.maybe'), 2); tests++;
  assert.throws(() => selectType(get('Outer'), ['missing'], checker), /属性缺少/); tests++;
  assert.throws(() => selectType(get('Item'), ['id', 'items'], checker), /需要数组/); tests++;
  const a = selectType(get('Choice'), [{ discriminator: 'kind', values: ['a'] }], checker);
  const b = selectType(get('Choice'), [{ discriminator: 'kind', values: ['b'] }], checker);
  assert.equal(checkObject(a, { required: ['kind', 'id'], properties: { kind: { kind: 'enum', values: ['a'] }, id: { kind: 'types', values: ['string'] } } }, checker, 'A'), 2); tests++;
  checkUnion(get('Choice'), [a, b], 'Choice'); tests++;
  assert.equal(checkObject(get('Conditional'), { required: ['id'], properties: { id: { kind: 'types', values: ['string'] }, payload: { kind: 'types', values: ['string'] } } }, checker, 'Conditional'), 2); tests++;
  const unknownUnion = { required: ['kind', 'payload'], properties: { kind: { kind: 'enum', values: ['a', 'b'] }, payload: { kind: 'unknown' } } };
  assert.equal(checkObject(get('ConditionalUnknown'), unknownUnion, checker, 'ConditionalUnknown'), 2); tests++;
  assert.throws(() => checkObject(get('ConditionalAny'), unknownUnion, checker, 'ConditionalAny'), /未声明类型类别/); tests++;
  assert.throws(() => checkUnion(get('Choice'), [a], 'MissingBranch'), /全部联合分支/); tests++;
  assert.throws(() => checkUnion(get('ExtraChoice'), [a, b], 'ExtraBranch'), /全部联合分支/); tests++;
  assert.throws(() => selectType(get('Choice'), [{ discriminator: 'kind', values: ['c'] }], checker), /匹配 0/); tests++;
  assert.throws(() => selectType(get('Ambiguous'), [{ discriminator: 'kind', values: ['a'] }], checker), /匹配 2/); tests++;
  assert.equal(checkObject(schemaCallType(program, checker, main, 'synthetic#/$defs/Item', validator), item, checker, 'Call'), 2); tests++;
  assert.throws(() => schemaCallType(program, checker, main, 'synthetic#/$defs/Duplicate', validator), /匹配 2/); tests++;
  assert.throws(() => schemaCallType(program, checker, main, 'synthetic#/$defs/Missing', validator), /匹配 0/); tests++;
  assert.equal(checkObject(schemaCallType(program, checker, main, 'synthetic#/$defs/Alias', validator), item, checker, 'Alias'), 2); tests++;
  assert.throws(() => checkObject(schemaCallType(program, checker, main, 'synthetic#/$defs/Shadowed', validator), item, checker, 'Shadowed'), /属性/); tests++;
  assert.throws(() => schemaCallType(program, checker, main, 'synthetic#/$defs/FakeOnly', validator), /匹配 0/); tests++;
  assert.throws(() => schemaCallType(program, checker, main, 'synthetic#/$defs/Item'), /准确校验器导出/); tests++;
  assert.throws(() => checkObject(get('Item'), item, checker, 'BadBaseProjection', ['missing']), /继承扩展属性缺少/); tests++;
  console.log(JSON.stringify({ status: 'PASS', tests, browser: false, database: false, network: false }));
} finally {
  snapshot.dispose(); api.close(); fs.rmSync(directory, { recursive: true });
}
