import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import * as ts from '../guardian-h5/node_modules/typescript/dist/api/sync/api.js';
import * as ast from '../guardian-h5/node_modules/typescript/dist/ast/is.js';
import postcss from '../guardian-h5/node_modules/postcss/lib/postcss.mjs';


const ordered = values => [...new Set(values.map(value => JSON.stringify(value)))].sort();
export function equal(actual, expected, label) {
  if (JSON.stringify(ordered(actual)) !== JSON.stringify(ordered(expected))) throw new Error(`${label} 与 Schema 不一致：${JSON.stringify(actual)} / ${JSON.stringify(expected)}`);
}
export function atoms(type, checker) {
  if (type.isUnionType()) return type.getTypes().flatMap(child => atoms(child, checker));
  if (type.flags & ts.TypeFlags.Undefined) return [{ kind: 'undefined' }];
  if (type.flags & ts.TypeFlags.Unknown) return [{ kind: 'unknown' }];
  if (type.flags & ts.TypeFlags.Null) return [{ kind: 'null', value: null }];
  if (type.flags & ts.TypeFlags.StringLiteral) return [{ kind: 'string', value: type.value }];
  if (type.flags & ts.TypeFlags.NumberLiteral) return [{ kind: 'number', value: type.value }];
  if (type.flags & ts.TypeFlags.BooleanLiteral) return [{ kind: 'boolean', value: type.value }];
  if (type.flags & ts.TypeFlags.String) return [{ kind: 'string' }];
  if (type.flags & ts.TypeFlags.Number) return [{ kind: 'number' }];
  if (type.flags & ts.TypeFlags.Boolean) return [{ kind: 'boolean' }];
  if (checker.isArrayType(type)) return [{ kind: 'array' }];
  if (type.isIntersectionType()) {
    if (type.getTypes().flatMap(child => atoms(child, checker)).some(atom => atom.kind !== 'object')) throw new Error('交集类型需要对象定义');
    return [{ kind: 'object' }];
  }
  if (type.flags & (ts.TypeFlags.Object | ts.TypeFlags.NonPrimitive)) return [{ kind: 'object' }];
  throw new Error(`未声明类型类别：${checker.typeToString(type)}`);
}

export function exportedType(program, checker, filename, name) {
  const source = program.getSourceFile(filename);
  if (!source) throw new Error(`绑定模块未读取：${filename}`);
  const symbol = checker.getSymbolAtLocation(source);
  const exported = symbol && checker.getExportsOfModule(symbol).find(item => item.name === name);
  if (!exported) throw new Error(`绑定导出缺少：${name}`);
  const target = exported.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(exported) : exported;
  return { type: checker.getDeclaredTypeOfSymbol(target), location: source };
}

export function schemaCallType(program, checker, filename, reference, validatorModule) {
  const source = program.getSourceFile(filename);
  if (!source) throw new Error(`绑定模块未读取：${filename}`);
  const validatorSource = validatorModule && program.getSourceFile(validatorModule);
  const moduleSymbol = validatorSource && checker.getSymbolAtLocation(validatorSource);
  const exported = moduleSymbol && checker.getExportsOfModule(moduleSymbol).find(symbol => symbol.name === 'validateApiValue');
  if (!exported) throw new Error('Schema 调用绑定缺少准确校验器导出');
  const unalias = symbol => symbol?.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(symbol) : symbol;
  const expected = unalias(exported);
  const matches = [];
  function visit(node) {
    if (ast.isCallExpression(node) && node.arguments.length && ast.isStringLiteral(node.arguments[0])
        && node.arguments[0].text === reference && unalias(checker.getSymbolAtLocation(node.expression))?.id === expected.id) {
      if (node.typeArguments?.length !== 1) throw new Error('Schema 调用缺少唯一显式类型');
      matches.push(node.typeArguments[0]);
    }
    node.forEachChild(visit);
  }
  visit(source);
  if (matches.length !== 1) throw new Error(`Schema 调用绑定必须唯一：${reference}；匹配 ${matches.length}`);
  return { type: checker.getTypeAtLocation(matches[0]), location: source };
}

export function selectType(binding, steps, checker) {
  let { type, location } = binding;
  for (const step of steps ?? []) {
    if (step === 'nonNullable') {
      type = checker.getNonNullableType(type);
    } else if (step === 'items') {
      if (!checker.isArrayType(type)) throw new Error('内联绑定需要数组');
      const arguments_ = checker.getTypeArguments(type);
      if (arguments_.length !== 1) throw new Error('数组元素绑定不明确');
      type = arguments_[0];
    } else if (typeof step === 'string') {
      const property = checker.getPropertyOfType(type, step);
      if (!property) throw new Error(`内联绑定属性缺少：${step}`);
      type = checker.getTypeOfSymbolAtLocation(property, location);
    } else if (step && typeof step.property === 'string') {
      const property = checker.getPropertyOfType(type, step.property);
      if (!property) throw new Error(`内联绑定属性缺少：${step.property}`);
      type = checker.getTypeOfSymbolAtLocation(property, location);
    } else if (step && typeof step.discriminator === 'string' && Array.isArray(step.values)) {
      const choices = type.isUnionType() ? type.getTypes() : [type];
      const matching = choices.filter(choice => {
        const property = checker.getPropertyOfType(choice, step.discriminator);
        if (!property) return false;
        const values = atoms(checker.getTypeOfSymbolAtLocation(property, location), checker);
        return values.every(value => Object.hasOwn(value, 'value'))
          && JSON.stringify(ordered(values.map(value => value.value))) === JSON.stringify(ordered(step.values));
      });
      if (matching.length !== 1) throw new Error(`联合绑定必须唯一：${step.discriminator}；匹配 ${matching.length} 个分支`);
      type = matching[0];
    } else throw new Error('未支持的精确绑定步骤');
  }
  return { type, location };
}

export function checkObject(binding, schema, checker, name, omitted = []) {
  const { type, location } = binding;
  if (atoms(type, checker).some(atom => atom.kind !== 'object')) throw new Error(`${name} 需要对象类型`);
  const members = type.isUnionType() ? type.getTypes() : [type];
  const memberProperties = members.map(member => new Map(checker.getPropertiesOfType(member).map(property => [property.name, property])));
  const allNames = [...new Set(memberProperties.flatMap(properties => [...properties.keys()]))];
  for (const key of omitted) if (!allNames.includes(key)) throw new Error(`继承扩展属性缺少：${key}`);
  const names = allNames.filter(name => !omitted.includes(name));
  equal(names, Object.keys(schema.properties), `${name}属性`);
  for (const propertyName of names) {
    const declarations = memberProperties.map(properties => properties.get(propertyName));
    const optional = declarations.some(property => !property || property.flags & ts.SymbolFlags.Optional);
    const required = schema.required.includes(propertyName);
    if (required === optional) throw new Error(`${name}.${propertyName}必填状态不一致`);
    const memberValues = declarations.filter(Boolean).flatMap(property => {
      const values = atoms(checker.getTypeOfSymbolAtLocation(property, location), checker);
      return property.flags & ts.SymbolFlags.Optional ? values.filter(value => value.kind !== 'undefined') : values;
    });
    // Inspect every member first (so any/unsupported types still fail), then
    // apply the TypeScript union rule that unknown absorbs narrower domains.
    const values = memberValues.some(value => value.kind === 'unknown') ? [{ kind: 'unknown' }] : memberValues;
    const property = { name: propertyName };
    const expected = schema.properties[property.name];
    if (expected.kind === 'unknown') equal(values.map(value => value.kind), ['unknown'], `${name}.${property.name}显式不受限类型`);
    else if (expected.kind === 'enum') equal(values.map(value => value.value), expected.values, `${name}.${property.name}枚举`);
    else if (expected.kind === 'types') equal(values.map(value => value.kind === 'number' && expected.values.includes('integer') ? 'integer' : value.kind), expected.values, `${name}.${property.name}类型及空值`);
    else throw new Error(`${name}.${property.name}未知描述类别：${expected.kind}`);
  }
  return names.length;
}

export function checkUnion(binding, branches, name) {
  const flatten = type => type.isUnionType() ? type.getTypes().flatMap(flatten) : [type];
  // Every actual and expected member must be accounted for. No first-branch pick.
  equal(flatten(binding.type).map(type => type.id), branches.flatMap(branch => flatten(branch.type)).map(type => type.id), `${name}全部联合分支`);
}

export const explicitBindings = {
  Page: { module: 'frontend-design/types.ts', name: 'Page' },
  ...Object.fromEntries(['UploadFileReceipt', 'UploadError', 'UploadGroupView'].map(name => [name, {
    module: 'guardian-h5/src/api/archiveUploads.ts', callSchema: `v2-protocol.schema.json#/$defs/${name}`,
  }])),
  PageAttemptViewBase: { name: 'PageAttemptView', omit: ['pricing_scope'] },
  PageOriginalAuthorization: { name: 'PageOriginalView', steps: ['use_authorizations', 'items'] },
  PageOriginalCopy: { name: 'PageOriginalView', steps: ['purpose_copies', 'items'] },
  UploadAuthorizationOption: { name: 'ArchiveUploadOptions', steps: ['authorizations', 'items'] },
  AssessmentView: { name: 'AssessmentViewWire' },
  // Schema describes the unprojected wire values; UI types remain narrower.
  ...Object.fromEntries(['AssessmentSelectionCandidate', 'AssessmentCreationPlan', 'AssessmentCreationOptions',
    'DynamicCreationPlan', 'DynamicCreationOptions'].map(name => [name, { name: `${name}Wire` }])),
  ClinicalIntervalScope: { name: 'ClinicalIntervalMetricSpec', steps: ['scope'] },
  ClinicalIntervalSubgroup: { name: 'ClinicalIntervalMetricSpec', steps: ['scope', 'subgroup', 'nonNullable'] },
  DynamicSignalInput: { name: 'DynamicSignal', steps: ['inputs', 'items'] },
  DynamicAutomaticTrigger: { name: 'DynamicView', steps: ['automatic_trigger', 'nonNullable'] },
  DynamicCombinations: { name: 'DynamicView', steps: ['combinations', 'nonNullable'] },
  DynamicLogicalGroup: { name: 'DynamicGroup', steps: [{ discriminator: 'operator', values: ['all', 'any', 'at_least'] }] },
  DynamicWeightedGroup: { name: 'DynamicGroup', steps: [{ discriminator: 'operator', values: ['weighted'] }] },
  ...Object.fromEntries(['RegroupInput', 'RegroupOutput', 'RegroupResult', 'RegroupReceipt', 'RegroupOptions'].map(name => [name, { module: 'frontend-design/archive-regroup-types.ts', name }])),
  RegroupSource: { module: 'frontend-design/archive-regroup-types.ts', name: 'RegroupInput', steps: ['sources', 'items'] },
  RegroupIntentInput: { module: 'frontend-design/archive-regroup-types.ts', name: 'RegroupIdentity' },
  RegroupRequestView: { module: 'frontend-design/archive-regroup-types.ts', name: 'RegroupRequest' },
};

export function runChecks(root, descriptionPath) {
  const api = new ts.API({ cwd: root });
  const projects = ['frontend-design/tsconfig.json', 'guardian-h5/tsconfig.json'].map(name => path.join(root, name));
  const snapshot = api.updateSnapshot({ openProjects: projects });
  try {
    const project = snapshot.getProject(projects[0]);
    const application = snapshot.getProject(projects[1]);
    if (!project || !application) throw new Error('当前类型项目未读取');
    const diagnostics = [project, application].flatMap(item => [...item.program.getSyntacticDiagnostics(), ...item.program.getSemanticDiagnostics(), ...item.program.getProgramDiagnostics()]);
    if (diagnostics.length) throw new Error(JSON.stringify(diagnostics));
    const resolve = name => {
      const mapping = explicitBindings[name] ?? { name };
      const module = mapping.module ?? 'frontend-design/v2-types.ts';
      let selected = project;
      if (!selected.program.getSourceFile(path.join(root, module))) selected = application;
      const binding = mapping.callSchema
        ? schemaCallType(selected.program, selected.checker, path.join(root, module), mapping.callSchema, path.join(root, 'guardian-h5/src/api/schema.ts'))
        : exportedType(selected.program, selected.checker, path.join(root, module), mapping.name);
      return { ...selectType(binding, mapping.steps, selected.checker), checker: selected.checker, mapping };
    };
    const descriptions = JSON.parse(fs.readFileSync(descriptionPath, 'utf8'));
    let propertiesChecked = 0;
    const checked = [];
    for (const [name, schema] of Object.entries(descriptions)) {
      const binding = resolve(name);
      if (schema.union_refs) checkUnion(binding, schema.union_refs.map(resolve), name);
      else propertiesChecked += checkObject(binding, schema, binding.checker, name, binding.mapping.omit);
      checked.push(name);
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
    return { status: 'PASS', types: Object.keys(descriptions).length, properties: propertiesChecked, css_rules: rules, css_declarations: declarationsCount, typescript: version, runtime_validation: 'NOT_RUN', visual_validation: 'NOT_RUN', limits: ['运行时Schema继续核验格式及跨字段语义', 'CSS来源解析不证明正式页面视觉一致'] };
  } finally { snapshot.dispose(); api.close(); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
  const { values } = parseArgs({ options: { 'description-path': { type: 'string', default: path.join(root, '.planning-work/t38-types-description.json') } } });
  const report = runChecks(root, values['description-path']);
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}
