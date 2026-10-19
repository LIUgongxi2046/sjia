import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const request = JSON.parse(readFileSync(0, 'utf8'))
const require = createRequire(resolve(request.node_modules, '../package.json'))
const { parseAst } = await import(pathToFileURL(require.resolve('rolldown/parseAst')).href)
const installed = JSON.parse(readFileSync(resolve(request.node_modules, 'rolldown/package.json'), 'utf8'))
const lock = JSON.parse(readFileSync(resolve(request.root, 'guardian-h5/package-lock.json'), 'utf8'))
assert.equal(installed.version, lock.packages['node_modules/rolldown'].version)
const files = {}
for (const name of request.sources) {
  const source = readFileSync(resolve(request.root, name), 'utf8')
  const program = parseAst(source, { lang: name.endsWith('.tsx') ? 'tsx' : 'ts' }, name)
  const imports = []
  const constants = new Map()
  const calls = []
  const references = []
  function collect(node) {
    if (!node || typeof node !== 'object') return
    if (node.type === 'ImportDeclaration') imports.push({ source: node.source.value,
      names: node.specifiers.map(item => item.local.name) })
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(collect)
      else if (value && typeof value === 'object') collect(value)
    }
  }
  collect(program)
  for (const statement of program.body) {
    const declaration = statement.type === 'ExportNamedDeclaration' ? statement.declaration : statement
    if (declaration?.type !== 'VariableDeclaration' || declaration.kind !== 'const') continue
    for (const item of declaration.declarations) {
      if (item.id.type === 'Identifier' && item.init) constants.set(item.id.name, item.init)
    }
  }
  function shape(node, seen = new Set()) {
    if (!node) return null
    if (typeof node.value === 'string') return node.value
    if (node.type === 'TemplateLiteral') return node.quasis.map((item, index) => item.value.cooked
      + (index < node.expressions.length ? shape(node.expressions[index], seen) ?? '{}' : '')).join('')
    if (node.type === 'BinaryExpression' && node.operator === '+') {
      const left = shape(node.left, seen)
      return left === null ? null : left + (shape(node.right, seen) ?? '{}')
    }
    if (node.type === 'Identifier' && constants.has(node.name) && !seen.has(node.name)) {
      return shape(constants.get(node.name), new Set([...seen, node.name]))
    }
    return null
  }
  function walk(node, owner = '<module>') {
    if (!node || typeof node !== 'object') return
    if ((node.type === 'FunctionDeclaration' || node.type === 'ClassDeclaration') && node.id) owner = node.id.name
    if (node.type === 'VariableDeclarator' && node.id.type === 'Identifier'
        && ['ArrowFunctionExpression', 'FunctionExpression'].includes(node.init?.type)) owner = node.id.name
    if (node.type === 'CallExpression') {
      const callee = node.callee.type === 'Identifier' ? node.callee.name
        : node.callee.type === 'MemberExpression' && !node.callee.computed ? node.callee.property.name : null
      const first = node.arguments[0]
      const value = shape(first)
      if (value?.startsWith('/api/')) {
        const options = node.arguments.find(item => item.type === 'ObjectExpression')
        const method = options?.properties.find(item => item.type === 'Property'
          && (item.key.name ?? item.key.value) === 'method')
        calls.push({ owner, callee, line: source.slice(0, node.start).split('\n').length,
          path_shape: value, method: method ? shape(method.value) : null,
          source_expression: source.slice(first.start, first.end) })
      }
      if (value?.includes('.schema.json#')) references.push({ owner, callee, reference: value,
        line: source.slice(0, node.start).split('\n').length })
    }
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(item => walk(item, owner))
      else if (value && typeof value === 'object') walk(value, owner)
    }
  }
  walk(program)
  files[name] = { imports, calls, schema_references: references }
}
process.stdout.write(JSON.stringify({ parser: 'rolldown/parseAst', version: installed.version, files,
  scope: '实际源码中的API路径及Schema调用；动态表达式保留未知范围' }))
