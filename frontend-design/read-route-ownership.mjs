import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, extname, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const request = JSON.parse(readFileSync(0, 'utf8'))
const root = resolve(request.root)
const require = createRequire(resolve(request.node_modules, '../package.json'))
const { parseAst } = await import(pathToFileURL(require.resolve('rolldown/parseAst')).href)
const postcss = require('postcss')
const version = JSON.parse(readFileSync(resolve(request.node_modules, 'rolldown/package.json'), 'utf8')).version
const postcssVersion = JSON.parse(readFileSync(resolve(request.node_modules, 'postcss/package.json'), 'utf8')).version
const lock = JSON.parse(readFileSync(resolve(root, 'guardian-h5/package-lock.json'), 'utf8'))
assert.equal(version, lock.packages['node_modules/rolldown'].version)
assert.equal(postcssVersion, lock.packages['node_modules/postcss'].version)
const modules = new Map()

function inspectModule(path) {
  path = resolve(path)
  if (modules.has(path)) return modules.get(path)
  assert.ok(path.startsWith(root + '/'))
  const source = readFileSync(path, 'utf8')
  const program = parseAst(source, { lang: path.endsWith('.tsx') ? 'tsx' : 'ts' }, relative(root, path))
  const result = { path, source, program, imports: new Map(), declarations: new Map(), exports: new Map(), reexports: new Map(), lazy: new Map() }
  modules.set(path, result)
  for (const statement of program.body) {
    if (statement.type === 'ExportNamedDeclaration') {
      for (const item of statement.specifiers) {
        assert.equal(item.type, 'ExportSpecifier')
        const exported = item.exported.name ?? item.exported.value
        const local = item.local.name ?? item.local.value
        if (statement.source) result.reexports.set(exported, { source: statement.source.value, symbol: local })
        else result.exports.set(exported, local)
      }
    }
    if (statement.type === 'ImportDeclaration' && statement.importKind !== 'type') {
      for (const item of statement.specifiers) {
        if (item.importKind === 'type') continue
        result.imports.set(item.local.name, { source: statement.source.value,
          symbol: item.type === 'ImportDefaultSpecifier' ? 'default' : item.imported?.name })
      }
    }
    const declaration = ['ExportNamedDeclaration', 'ExportDefaultDeclaration'].includes(statement.type)
      ? statement.declaration : statement
    if (declaration?.type === 'FunctionDeclaration') {
      result.declarations.set(declaration.id.name, declaration)
      if (statement.type === 'ExportNamedDeclaration') result.exports.set(declaration.id.name, declaration.id.name)
      if (statement.type === 'ExportDefaultDeclaration') result.exports.set('default', declaration.id.name)
    }
    if (declaration?.type === 'VariableDeclaration') {
      for (const item of declaration.declarations) {
        if (item.id.type !== 'Identifier') continue
        result.declarations.set(item.id.name, item)
        if (statement.type === 'ExportNamedDeclaration') result.exports.set(item.id.name, item.id.name)
        if (item.init?.type !== 'CallExpression' || item.init.callee.type !== 'Identifier' || item.init.callee.name !== 'lazy') continue
        const imports = []
        const selected = []
        visit(item.init, node => {
          if (node.type === 'ImportExpression') imports.push(node.source.value)
          if (node.type === 'Property' && node.key.name === 'default' && node.value.type === 'MemberExpression'
              && !node.value.computed) selected.push(node.value.property.name)
        })
        assert.equal(imports.length, 1)
        assert.ok(selected.length <= 1)
        result.lazy.set(item.id.name, { source: imports[0], symbol: selected[0] ?? 'default' })
      }
    }
  }
  return result
}

function visit(node, action) {
  if (!node || typeof node !== 'object') return
  action(node)
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(item => visit(item, action))
    else if (value && typeof value === 'object') visit(value, action)
  }
}

function resolveSource(module, reference) {
  assert.ok(reference.source.startsWith('.'))
  const base = resolve(dirname(module.path), reference.source)
  const candidates = [base, base + '.tsx', base + '.ts', resolve(base, 'index.tsx'), resolve(base, 'index.ts')].filter(existsSync)
  assert.equal(candidates.length, 1)
  return inspectModule(candidates[0])
}

function component(module, name) {
  if (/^[a-z]/.test(name)) return null
  const binding = module.lazy.get(name) ?? module.imports.get(name)
  if (binding && !binding.source.startsWith('.')) return null
  let target = binding ? resolveSource(module, binding) : module
  let exported = binding?.symbol
  const chain = []
  const seen = new Set()
  while (exported && target.reexports.has(exported)) {
    const identity = target.path + '#' + exported
    assert.ok(!seen.has(identity))
    seen.add(identity)
    chain.push({ source: relative(root, target.path), symbol: exported })
    const reference = target.reexports.get(exported)
    target = resolveSource(target, reference)
    exported = reference.symbol
  }
  const symbol = exported ? target.exports.get(exported) : name
  assert.ok(symbol && target.declarations.has(symbol), `${relative(root, target.path)}#${symbol}`)
  const declaration = target.declarations.get(symbol)
  return { local_name: name, symbol, source: relative(root, target.path),
    line: target.source.slice(0, declaration.start).split('\n').length,
    import_kind: module.lazy.has(name) ? 'lazy' : binding ? 'static' : 'local', reexports: chain }
}

function rendered(module, node) {
  if (!node) return []
  if (node.type === 'JSXElement') {
    const name = node.openingElement.name
    const current = name.type === 'JSXIdentifier' ? component(module, name.name) : null
    return [...(current ? [current] : []), ...node.children.flatMap(child => rendered(module, child))]
  }
  if (node.type === 'JSXFragment') return node.children.flatMap(child => rendered(module, child))
  if (node.type === 'JSXExpressionContainer') return rendered(module, node.expression)
  return []
}

function routes(module, owner, prefix = '') {
  const function_ = module.declarations.get(owner)
  assert.equal(function_?.type, 'FunctionDeclaration')
  const results = []
  function walk(node, parentPath = '', inherited = []) {
    if (!node || typeof node !== 'object') return
    if (node.type === 'JSXElement' && node.openingElement.name.type === 'JSXIdentifier'
        && node.openingElement.name.name === 'Route') {
      const attributes = node.openingElement.attributes
      const path = attributes.find(item => item.type === 'JSXAttribute' && item.name.name === 'path')
      const element = attributes.find(item => item.type === 'JSXAttribute' && item.name.name === 'element')
      const own = rendered(module, element?.value)
      const active = own.length ? own : inherited
      const current = path ? [parentPath, path.value.value].filter(Boolean).join('/') : parentPath
      if (path) results.push({ path: !prefix && current === '*' ? '*' : prefix + '/' + current, owner, components: active,
        source: relative(root, module.path), line: module.source.slice(0, node.start).split('\n').length,
        inherited_element: !own.length && inherited.length > 0 })
      node.children.forEach(child => walk(child, current, active))
      return
    }
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(item => walk(item, parentPath, inherited))
      else if (value && typeof value === 'object') walk(value, parentPath, inherited)
    }
  }
  walk(function_.body)
  return results
}

const application = inspectModule(resolve(root, 'guardian-h5/src/app/App.tsx'))
const rootRoutes = routes(application, 'App')
const support = rootRoutes.filter(item => item.components.some(value => value.symbol === 'SupportApp'))
assert.equal(support.length, 1)
assert.equal(support[0].path, '/support/*')
const supportSource = support[0].components.find(item => item.symbol === 'SupportApp').source
const familyRoutes = routes(application, 'FamilyApp')
const supportRoutes = routes(inspectModule(resolve(root, supportSource)), 'SupportApp', '/support')
for (const name of request.sources ?? []) inspectModule(resolve(root, name))
const assets = new Map()
const styles = new Map()
const styleOrder = []
const publicAssets = []
for (const module of modules.values()) {
  visit(module.program, node => {
    if (node.type !== 'VariableDeclarator' || node.id.type !== 'Identifier'
        || typeof node.init?.value !== 'string' || !node.init.value.startsWith('/assets/')) return
    const path = resolve(root, 'guardian-h5/public', '.' + node.init.value)
    assert.ok(path.startsWith(resolve(root, 'guardian-h5/public') + '/'))
    assert.ok(existsSync(path))
    publicAssets.push({ path: relative(root, path), url: node.init.value, imported_by: relative(root, module.path),
      binding: node.id.name, design_source: 'ui-delivery' + node.init.value })
  })
  for (const [name, binding] of module.imports) {
    if (!binding.source.startsWith('.')) continue
    const url = new URL(binding.source, pathToFileURL(module.path))
    const path = fileURLToPath(url)
    const extension = extname(path)
    if (!['.png', '.jpg', '.jpeg', '.svg', '.webp', '.css'].includes(extension)) continue
    assert.ok(path.startsWith(root + '/'))
    assert.ok(existsSync(path))
    const record = { path: relative(root, path), imported_by: relative(root, module.path), binding: name }
    const collection = extension === '.css' ? styles : assets
    if (!collection.has(record.path)) collection.set(record.path, [])
    collection.get(record.path).push(record)
  }
  if (relative(root, module.path) === 'guardian-h5/src/components/UI2Frame.tsx') {
    visit(module.program, node => {
      if (node.type !== 'JSXElement' || node.openingElement.name.type !== 'JSXIdentifier'
          || node.openingElement.name.name !== 'style') return
      const order = node.children.filter(child => child.type === 'JSXExpressionContainer').map(child => {
        assert.equal(child.expression.type, 'Identifier')
        const binding = module.imports.get(child.expression.name)
        assert.ok(binding)
        const path = fileURLToPath(new URL(binding.source, pathToFileURL(module.path)))
        assert.equal(extname(path), '.css')
        return relative(root, path)
      })
      styleOrder.push(order)
    })
  }
}
assert.equal(styleOrder.length, 1)
const css = []
for (const [path, imports] of styles) {
  const document = postcss.parse(readFileSync(resolve(root, path), 'utf8'), { from: path })
  let rules = 0
  let declarations = 0
  const companion = []
  document.walkRules(() => rules += 1)
  document.walkDecls(declaration => {
    declarations += 1
    if (declaration.prop === '--home-companion-height') companion.push({ selector: declaration.parent.selector,
      value: declaration.value, line: declaration.source.start.line })
  })
  css.push({ path, imports, rules, declarations, companion })
}
process.stdout.write(JSON.stringify({ parser: 'rolldown/parseAst', version, postcss_version: postcssVersion, root_routes: rootRoutes,
  family_routes: familyRoutes, support_routes: supportRoutes,
  source_files: [...modules.keys()].map(path => relative(root, path)).sort(),
  source_symbols: Object.fromEntries([...modules.values()].map(module => [relative(root, module.path), [...module.declarations.keys()]])),
  asset_imports: [...assets.entries()].map(([path, imports]) => ({ path, imports })),
  public_assets: publicAssets, css_sources: css, frame_style_order: styleOrder[0],
  business_validation: 'NOT_RUN', visual_validation: 'NOT_RUN' }))
