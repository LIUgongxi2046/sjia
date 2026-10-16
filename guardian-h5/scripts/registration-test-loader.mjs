// Load the production TS/TSX in Node for DOM regression tests, using Vite's
// existing transformer dependency. Production bundling is unchanged.
import { registerHooks } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { transformSync } from 'rolldown/utils';

registerHooks({
  resolve(specifier, context, nextResolve) {
    try { return nextResolve(specifier, context); }
    catch (error) {
      for (const suffix of ['.ts', '.tsx', '.js']) {
        try { return nextResolve(specifier + suffix, context); } catch { /* try the next actual file */ }
      }
      throw error;
    }
  },
  load(url, context, nextLoad) {
    if (url.includes('.css')) return { format: 'module', source: 'export default "";', shortCircuit: true };
    if (url.startsWith('file:') && !url.includes('/node_modules/')) {
      const path = fileURLToPath(new URL(url));
      if (url.includes('.css')) return { format: 'module', source: 'export default "";', shortCircuit: true };
      if (/\.json$/.test(path)) return { format: 'module', source: `export default ${readFileSync(path, 'utf8')};`, shortCircuit: true };
      if (/\.tsx?$/.test(path) && existsSync(path)) {
        const result = transformSync(path, readFileSync(path, 'utf8'), { jsx: { runtime: 'automatic' } });
        if (result.errors.length) throw new Error(JSON.stringify(result.errors));
        return { format: 'module', source: result.code, shortCircuit: true };
      }
    }
    return nextLoad(url, context);
  },
});
