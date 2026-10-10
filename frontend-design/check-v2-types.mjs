import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as ts from '../guardian-h5/node_modules/typescript/dist/api/sync/api.js';
import * as ast from '../guardian-h5/node_modules/typescript/dist/ast/is.js';
import postcss from '../guardian-h5/node_modules/postcss/lib/postcss.mjs';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const api = new ts.API({ cwd: root });
const snapshot = api.updateSnapshot({ openProjects: [path.join(root, 'frontend-design/tsconfig.json')] });
const project = snapshot.getProject(path.join(root, 'frontend-design/tsconfig.json'));
if (!project) throw new Error('当前类型项目未读取');
const program = project.program;
const diagnostics = [...program.getSyntacticDiagnostics(), ...program.getSemanticDiagnostics(), ...program.getProgramDiagnostics()];
if (diagnostics.length) throw new Error(JSON.stringify(diagnostics));
const checker = project.checker;
const source = program.getSourceFile(path.join(root, 'frontend-design/v2-types.ts'));
if (!source) throw new Error('新增类型未读取');
const declarations = new Map();
for (const statement of source.statements) {
  if (ast.isInterfaceDeclaration(statement) || ast.isTypeAliasDeclaration(statement)) declarations.set(statement.name.text, statement);
}
const inheritedSource = program.getSourceFile(path.join(root, 'frontend-design/types.ts'));
if (!inheritedSource) throw new Error('兼容类型未读取');
for (const statement of inheritedSource.statements) {
  if ((ast.isInterfaceDeclaration(statement) || ast.isTypeAliasDeclaration(statement)) && !declarations.has(statement.name.text)) declarations.set(statement.name.text, statement);
}
function atoms(type) {
  if (type.isUnionType()) return type.getTypes().flatMap(atoms);
  if (type.flags & ts.TypeFlags.Undefined) return [];
  if (type.flags & ts.TypeFlags.Null) return [{ kind: 'null', value: null }];
  if (type.flags & ts.TypeFlags.StringLiteral) return [{ kind: 'string', value: type.value }];
  if (type.flags & ts.TypeFlags.NumberLiteral) return [{ kind: 'number', value: type.value }];
  if (type.flags & ts.TypeFlags.BooleanLiteral) return [{ kind: 'boolean', value: type.value }];
  if (type.flags & ts.TypeFlags.String) return [{ kind: 'string' }];
  if (type.flags & ts.TypeFlags.Number) return [{ kind: 'number' }];
  if (type.flags & ts.TypeFlags.Boolean) return [{ kind: 'boolean' }];
  if (checker.isArrayType(type)) return [{ kind: 'array' }];
  if (type.isIntersectionType() || type.flags & (ts.TypeFlags.Object | ts.TypeFlags.NonPrimitive)) return [{ kind: 'object' }];
  throw new Error(`未声明类型类别：${checker.typeToString(type)}`);
}
function equal(actual, expected, label) {
  const ordered = values => [...new Set(values.map(value => JSON.stringify(value)))].sort();
  if (JSON.stringify(ordered(actual)) !== JSON.stringify(ordered(expected))) throw new Error(`${label} 与 Schema 不一致：${JSON.stringify(actual)} / ${JSON.stringify(expected)}`);
}
const descriptions = JSON.parse(fs.readFileSync(path.join(root, '.planning-work/t38-types-description.json'), 'utf8'));
let propertiesChecked = 0;
for (const [name, schema] of Object.entries(descriptions)) {
  const declaration = declarations.get(name);
  if (!declaration) throw new Error(`新增类型缺少：${name}`);
  const type = checker.getTypeAtLocation(declaration);
  const properties = checker.getPropertiesOfType(type);
  equal(properties.map(property => property.name), Object.keys(schema.properties), `${name}属性`);
  for (const property of properties) {
    const required = schema.required.includes(property.name);
    if (required === Boolean(property.flags & ts.SymbolFlags.Optional)) throw new Error(`${name}.${property.name}必填状态不一致`);
    const values = atoms(checker.getTypeOfSymbolAtLocation(property, declaration));
    const expected = schema.properties[property.name];
    if (expected.kind === 'enum') equal(values.map(value => value.value), expected.values, `${name}.${property.name}枚举`);
    else equal(values.map(value => value.kind === 'number' && expected.values.includes('integer') ? 'integer' : value.kind), expected.values, `${name}.${property.name}类型及空值`);
    propertiesChecked += 1;
  }
}
const tokenMap = JSON.parse(fs.readFileSync(path.join(root, 'frontend-design/v2-token-map.json'), 'utf8'));
const tokens = JSON.parse(fs.readFileSync(path.join(root, tokenMap.source), 'utf8'));
const tokenCss = postcss.parse(fs.readFileSync(path.join(root, tokenMap.cssTokenSource), 'utf8'));
const custom = new Map();
tokenCss.walkDecls(declaration => custom.set(declaration.prop, declaration.value));
for (const item of tokenMap.tokens) {
  const value = item.source.split('.').reduce((parent, key) => parent[key], tokens);
  const expected = item.unit === 'literal' ? String(value) : `${value}${item.unit}`;
  const normalize = value => value.toLowerCase().replaceAll(' ', '').replaceAll("'", '');
  if (!custom.has(item.css) || normalize(custom.get(item.css)) !== normalize(expected)) throw new Error(`CSS Token不一致：${item.css}`);
}
if (custom.get(tokenMap.shadow.css) !== `${tokens.shadow.sm},${tokenMap.shadow.additional}`) throw new Error('设计阴影不一致');
const css = postcss.parse(fs.readFileSync(path.join(root, tokenMap.stylesheet), 'utf8'));
let rules = 0;
let declarationsCount = 0;
css.walkRules(() => rules += 1);
css.walkDecls(() => declarationsCount += 1);
const version = JSON.parse(fs.readFileSync(path.join(root, 'guardian-h5/node_modules/typescript/package.json'), 'utf8')).version;
snapshot.dispose();
api.close();
process.stdout.write(`${JSON.stringify({ status: 'PASS', types: Object.keys(descriptions).length, properties: propertiesChecked, css_rules: rules, css_declarations: declarationsCount, typescript: version, runtime_validation: 'NOT_RUN', visual_validation: 'NOT_RUN', limits: ['运行时Schema继续核验格式及跨字段语义', 'CSS来源解析不证明正式页面视觉一致'] })}\n`);
