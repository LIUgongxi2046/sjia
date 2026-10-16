// Test-only CSS cascade calculation. It does not pretend jsdom renders CSS.
// Selectors are parsed by Lightning CSS so :is/:not/:has use their most specific
// argument, even when a less-specific argument is the one matching the element.
import { transform } from 'lightningcss';
import { parse } from 'postcss';

const compare = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
const add = (a, b) => a.map((value, index) => value + b[index]);
const maximum = selectors => selectors.map(weight).reduce((a, b) => compare(a, b) >= 0 ? a : b, [0, 0, 0]);
function weight(components) {
  return components.reduce((sum, part) => {
    if (part.type === 'id') return add(sum, [1, 0, 0]);
    if (['class', 'attribute'].includes(part.type)) return add(sum, [0, 1, 0]);
    if (['type', 'pseudo-element'].includes(part.type)) return add(sum, [0, 0, 1]);
    if (part.type === 'pseudo-class') {
      if (part.kind === 'where') return sum;
      if (['is', 'not', 'has'].includes(part.kind)) return add(sum, maximum(part.selectors));
      return add(add(sum, [0, 1, 0]), part.of ? maximum(part.of) : [0, 0, 0]);
    }
    if (['combinator', 'universal', 'namespace'].includes(part.type)) return sum;
    throw new Error(`Unsupported selector component in cascade regression: ${part.type}`);
  }, [0, 0, 0]);
}
const cache = new Map();
export function selectorSpecificity(selector) {
  if (!cache.has(selector)) {
    let parsed;
    transform({ filename: 'selector.css', code: Buffer.from(`${selector}{color:red}`), visitor: { Selector(value) { parsed = value; } } });
    if (!parsed) throw new Error(`Selector not parsed: ${selector}`);
    cache.set(selector, weight(parsed));
  }
  return cache.get(selector);
}

// Test the flattened application stylesheet at ordinary viewport settings, or
// with explicitly enabled preference media. No assumptions about layout/pixels.
export function winningDeclaration(css, element, property, mediaMatches = () => false) {
  let winner;
  parse(css).walkRules(rule => {
    for (let parent = rule.parent; parent?.type !== 'root'; parent = parent.parent) {
      if (parent.type !== 'atrule' || parent.name !== 'media' || !mediaMatches(parent.params)) return;
    }
    for (const selector of rule.selectors) {
      if (selector.includes(':host') || selector.includes('::')) continue;
      if (!element.matches(selector)) continue;
      const specificity = selectorSpecificity(selector);
      rule.walkDecls(property, declaration => {
        const candidate = { value: declaration.value, important: !!declaration.important, specificity, selector };
        if (!winner || Number(candidate.important) > Number(winner.important) || (candidate.important === winner.important && compare(specificity, winner.specificity) >= 0)) winner = candidate;
      });
    }
  });
  return winner;
}
