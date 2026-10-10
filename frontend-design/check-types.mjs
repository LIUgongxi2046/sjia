import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import * as ts from '../guardian-h5/node_modules/typescript/dist/api/sync/api.js';
import * as ast from '../guardian-h5/node_modules/typescript/dist/ast/is.js';

const directory = path.dirname(fileURLToPath(import.meta.url));
const root = path.dirname(directory);
const sourcePath = path.join(directory, 'types.ts');
const work = path.join(root, '.front-work');
fs.mkdirSync(work, { recursive: true });
process.env.TMPDIR = work;
const api = new ts.API({ cwd: root });
const snapshot = api.updateSnapshot({ openProjects: [path.join(directory, 'tsconfig.json'), path.join(root, 'guardian-h5/tsconfig.json')] });
const project = snapshot.getProject(path.join(directory, 'tsconfig.json'));
if (!project) throw new Error('前端类型项目未读取');
const program = project.program;
const diagnostics = [...program.getSyntacticDiagnostics(), ...program.getSemanticDiagnostics(), ...program.getProgramDiagnostics()];
if (diagnostics.length) throw new Error(JSON.stringify(diagnostics));
const checker = project.checker;
const source = program.getSourceFile(sourcePath);
if (!source) throw new Error('类型文件未读取');
const declarations = new Map();
for (const statement of source.statements) {
  if (ast.isInterfaceDeclaration(statement) || ast.isTypeAliasDeclaration(statement)) {
    declarations.set(statement.name.text, statement);
  }
}
const protocolPath = path.join(root, 'backend-design/protocol.schema.json');
const datasetPath = path.join(root, 'data-design/dataset.schema.json');
function typeAtoms(type) {
  if (type.isUnionType()) return type.getTypes().flatMap(typeAtoms);
  if (type.isIntersectionType()) {
    const parts = type.getTypes().flatMap(typeAtoms);
    if (!parts.length || parts.some(part => part.kind !== 'object')) throw new Error('交集类型需要对象定义');
    return [{ kind: 'object' }];
  }
  if (type.flags & ts.TypeFlags.Undefined) return [];
  if (type.flags & ts.TypeFlags.Null) return [{ kind: 'null', value: null }];
  if (type.flags & ts.TypeFlags.StringLiteral) return [{ kind: 'string', value: type.value }];
  if (type.flags & ts.TypeFlags.NumberLiteral) return [{ kind: 'number', value: type.value }];
  if (type.flags & ts.TypeFlags.BooleanLiteral) return [{ kind: 'boolean', value: type.value }];
  if (type.flags & ts.TypeFlags.String) return [{ kind: 'string' }];
  if (type.flags & ts.TypeFlags.Number) return [{ kind: 'number' }];
  if (type.flags & ts.TypeFlags.Boolean) return [{ kind: 'boolean' }];
  if (checker.isArrayType(type)) return [{ kind: 'array' }];
  if (type.flags & (ts.TypeFlags.Object | ts.TypeFlags.NonPrimitive)) return [{ kind: 'object' }];
  throw new Error(`无法检查类型：${checker.typeToString(type)}`);
}
function sorted(values) { return [...new Set(values.map(value => JSON.stringify(value)))].sort(); }
function requireEqual(actual, expected, label) {
  if (JSON.stringify(sorted(actual)) !== JSON.stringify(sorted(expected))) {
    throw new Error(`${label} 与源 Schema 不一致：${JSON.stringify(actual)} / ${JSON.stringify(expected)}`);
  }
}
const mappings = [
  ['MedicalTime', 'medicalTime', datasetPath], ['SourceReference', 'sourceReference', datasetPath],
  ['ClinicalField', 'clinicalField', datasetPath], ['ReportMetadata', 'reportMetadata', datasetPath],
  ...[
    'FieldInput', 'FamilyField', 'ReportInput', 'Confirmation', 'RecordInput', 'CorrectionInput',
    'FamilyRecord', 'Capability', 'SessionView', 'RelationView', 'ApplicationInput', 'ApplicationView',
    'RecordSearch', 'TrendComparison', 'TrendView', 'DictionarySearch', 'DictionaryView',
    'DraftField', 'DraftPayload', 'DraftInput', 'DraftUpdate', 'DraftView', 'IntentInput', 'IntentView',
    'WriteReceipt', 'RequestView', 'FileDeclaration', 'FileGroupInput', 'FileView', 'FilePageView',
    'FileGroupView', 'FileGroupComplete', 'RecognitionInput', 'AnchorView', 'CandidateField',
    'CandidateSetView', 'TaskView', 'FamilyClassification', 'FamilyMs', 'FamilyClinicalProjection',
    'HomeView', 'AlertView', 'SummaryInput', 'SummaryView',
    'AuthorView', 'AuthBootstrap', 'ApplicationAction', 'RequestRecoveryQuery', 'SourceResolution',
    'FieldConfigQuery', 'FieldDefinition', 'FieldConfigView', 'HistoryQuery', 'HistoryEntry',
  ].map(name => [name, name, protocolPath]),
  ['ApiError', 'Error', protocolPath],
];
const descriptions = JSON.parse(execFileSync('python3', [path.join(directory, 'describe-schema.py')], {
  cwd: root,
  env: { ...process.env, PYTHONPATH: path.join(root, '.data-work/deps') },
  input: JSON.stringify(mappings), encoding: 'utf8',
}));
let checkedProperties = 0;
for (const [name] of mappings) {
  const node = declarations.get(name);
  if (!node) throw new Error(`类型定义缺少：${name}`);
  const schema = descriptions.types[name];
  const type = checker.getTypeAtLocation(node);
  const properties = checker.getPropertiesOfType(type);
  requireEqual(properties.map(property => property.name), Object.keys(schema.properties), `${name} 属性`);
  for (const property of properties) {
    const required = (schema.required ?? []).includes(property.name);
    const optional = Boolean(property.flags & ts.SymbolFlags.Optional);
    if (required === optional) throw new Error(`${name}.${property.name} 必填标记不一致`);
    const expected = schema.properties[property.name];
    const atoms = typeAtoms(checker.getTypeOfSymbolAtLocation(property, node));
    if (expected.kind === 'enum') {
      requireEqual(atoms.map(atom => atom.value), expected.values, `${name}.${property.name} 枚举`);
    } else {
      const kinds = atoms.map(atom => atom.kind === 'number' && expected.values.includes('integer') ? 'integer' : atom.kind);
      requireEqual(kinds, expected.values, `${name}.${property.name} 类型和空值`);
    }
    checkedProperties += 1;
  }
}
const taskStatus = declarations.get('TaskStatus');
requireEqual(typeAtoms(checker.getTypeAtLocation(taskStatus)).map(atom => atom.value), descriptions.task_status, 'TaskStatus');
const version = JSON.parse(fs.readFileSync(path.join(root, 'guardian-h5/node_modules/typescript/package.json'), 'utf8')).version;
const applicationProject = snapshot.getProject(path.join(root, 'guardian-h5/tsconfig.json'));
if (!applicationProject) throw new Error('H5 项目未读取');
const application = applicationProject.program.getSourceFile(path.join(root, 'guardian-h5/src/app/App.tsx'));
if (!application) throw new Error('App 路由文件未读取');
const routePaths = [];
let rootRedirect = false;
function visitRoute(node) {
  if (ast.isJsxSelfClosingElement(node)) {
    const attributes = node.attributes.properties.filter(ast.isJsxAttribute);
    if (node.tagName.text === 'Route') {
      const routePath = attributes.find(attribute => attribute.name.text === 'path');
      if (routePath) {
        if (!routePath.initializer || !ast.isStringLiteral(routePath.initializer)) throw new Error('路由路径需要显式字面值');
        routePaths.push(routePath.initializer.text === '*' ? '*' : `/${routePath.initializer.text}`);
      }
      if (attributes.some(attribute => attribute.name.text === 'index')) routePaths.push('/');
    }
    if (node.tagName.text === 'Navigate') {
      const target = attributes.find(attribute => attribute.name.text === 'to');
      rootRedirect = Boolean(target?.initializer && ast.isStringLiteral(target.initializer) && target.initializer.text === '/home' && attributes.some(attribute => attribute.name.text === 'replace'));
    }
  }
  node.forEachChild(visitRoute);
}
visitRoute(application);
if (!rootRedirect) throw new Error('根路径需要 replace 导向首页');
snapshot.dispose();
api.close();
process.stdout.write(`${JSON.stringify({
  status: 'PASS', types: mappings.length, properties: checkedProperties, typescript: version,
  checks: ['exact_properties', 'required_optional', 'top_level_types_nullability', 'direct_enums', 'TaskStatus', 'strict_compile'],
  runtime_validation: 'NOT_RUN',
  current_source_routes: routePaths.sort(), root_redirect: '/home',
  limits: ['嵌套条件与跨字段语义仍由运行时 JSON Schema 和领域校验负责', 'ContactView 联合由 TypeScript 编译检查'],
}, null, 2)}\n`);
