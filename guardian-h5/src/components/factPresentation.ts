export const factContextOptions = [['history', '既往病史'], ['current', '此次病情'], ['discharge', '出院结论'],
  ['planned', '计划治疗'], ['actual', '实际治疗'], ['unknown', '无法确定']] as const;

export const assertionStateOptions = [['present', '原文记载存在'], ['absent', '原文明确否定'], ['unknown', '无法确定']] as const;

const comparisonSymbols = new Map([['eq', '='], ['lt', '<'], ['le', '≤'], ['gt', '>'], ['ge', '≥']]);
const verificationLabels = new Map([['unverified', '尚未核对'], ['pending', '等待核对'], ['verified', '专业核对有效'],
  ['needs_correction', '需要更正'], ['revoked', '核对已撤销']]);

export function verificationStatusLabel(value: string): string {
  const label = verificationLabels.get(value);
  if (!label) throw new Error('事实核对状态没有对应的显示名称。');
  return label;
}

export function factContextLabel(value: string): string {
  const option = factContextOptions.find(([code]) => code === value);
  if (!option) throw new Error('事实语境没有对应的显示名称。');
  return option[1];
}

export function assertionStateLabel(value: string): string {
  const option = assertionStateOptions.find(([code]) => code === value);
  if (!option) throw new Error('事实发生状态没有对应的显示名称。');
  return option[1];
}

export function comparisonSymbol(value: string | null): string {
  if (value === null) return '比较符号未知';
  const symbol = comparisonSymbols.get(value);
  if (!symbol) throw new Error('原始比较符没有对应的显示符号。');
  return symbol;
}
