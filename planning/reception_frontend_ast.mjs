import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const request = JSON.parse(readFileSync(0, 'utf8'))
const require = createRequire(resolve(request.node_modules, '../package.json'))
const parserPath = require.resolve('rolldown/parseAst')
const { parseAst } = await import(pathToFileURL(parserPath).href)
const installed = JSON.parse(readFileSync(resolve(request.node_modules, 'rolldown/package.json'), 'utf8'))
const lock = JSON.parse(readFileSync(resolve(request.root, 'guardian-h5/package-lock.json'), 'utf8'))
assert.equal(installed.version, lock.packages['node_modules/rolldown'].version)
const files = {}
for (const name of request.files) {
  const source = readFileSync(resolve(request.root, name), 'utf8')
  const program = parseAst(source, { lang: 'tsx', preserveParens: false }, name)
  const symbols = {}
  const imports = []
  for (const statement of program.body) {
    if (statement.type === 'ImportDeclaration') imports.push(statement.source.value)
    const declaration = statement.type === 'ExportNamedDeclaration' ? statement.declaration : statement
    if (!declaration) continue
    const line = source.slice(0, declaration.start).split('\n').length
    if (declaration.type === 'FunctionDeclaration' || declaration.type === 'ClassDeclaration') {
      symbols[declaration.id.name] = { line, kind: declaration.type }
    }
    if (declaration.type === 'VariableDeclaration') {
      for (const item of declaration.declarations) {
        if (item.id.type === 'Identifier') symbols[item.id.name] = { line, kind: declaration.type }
      }
    }
  }
  files[name] = { symbols, imports }
}
process.stdout.write(JSON.stringify({ parser: 'rolldown/parseAst', version: installed.version, files }))
