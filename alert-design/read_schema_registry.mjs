import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const request = JSON.parse(readFileSync(0, 'utf8'))
const require = createRequire(resolve(request.node_modules, '../package.json'))
const { parseAst } = await import(pathToFileURL(require.resolve('rolldown/parseAst')).href)
const installed = JSON.parse(readFileSync(resolve(request.node_modules, 'rolldown/package.json'), 'utf8'))
const lock = JSON.parse(readFileSync(resolve(request.root, 'guardian-h5/package-lock.json'), 'utf8'))
assert.equal(installed.version, lock.packages['node_modules/rolldown'].version)
const aliases = []
for (const name of request.sources) {
  const path = resolve(request.root, name)
  const program = parseAst(readFileSync(path, 'utf8'), { lang: 'ts' }, name)
  const imports = new Map()
  for (const statement of program.body) {
    if (statement.type === 'ImportDeclaration' && statement.source.value.endsWith('.json')) {
      assert.equal(statement.specifiers.length, 1)
      const binding = statement.specifiers[0]
      assert.equal(binding.type, 'ImportDefaultSpecifier')
      imports.set(binding.local.name, resolve(dirname(path), statement.source.value))
    }
    if (statement.type !== 'ExpressionStatement') continue
    const call = statement.expression
    if (call.type !== 'CallExpression' || call.callee.type !== 'MemberExpression'
      || call.callee.object.name !== 'validator' || call.callee.property.name !== 'addSchema') continue
    assert.equal(call.arguments.length, 2)
    assert.equal(call.arguments[0].type, 'Identifier')
    assert.equal(typeof call.arguments[1].value, 'string')
    const source = imports.get(call.arguments[0].name)
    assert.ok(source)
    aliases.push({ alias: call.arguments[1].value, path: source, registered_by: name })
  }
}
assert.ok(aliases.length > 0)
const identities = new Map()
for (const item of aliases) {
  if (identities.has(item.alias)) assert.equal(identities.get(item.alias), item.path)
  identities.set(item.alias, item.path)
}
process.stdout.write(JSON.stringify({ sources: request.sources, parser: 'rolldown/parseAst', version: installed.version,
  registration_scope: '顶层JSON import及明确字面别名', aliases }))
