export const automaticPollingBudgetMs = 5 * 60_000;

export function requiredTaskPollingInterval(value: number | null | undefined): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value <= 0) {
    throw new Error('任务没有提供合法查询间隔，请重新读取当前进度。');
  }
  return value;
}

export function canContinueTaskPolling(elapsedMs: number, intervalMs: number): boolean {
  if (!Number.isFinite(elapsedMs) || elapsedMs < 0) throw new Error('任务查询的经过时间无效。');
  return elapsedMs + requiredTaskPollingInterval(intervalMs) <= automaticPollingBudgetMs;
}
