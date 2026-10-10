// TaskCoordinator：单任务轮询，按 status_version 识别过期结果，不把旧结果写到当前页面。
export interface TaskView {
  task_id: string;
  status: string;
  status_version: number;
  result_type?: string | null;
  result_ref?: string | null;
  error_code?: string | null;
}

export function isTerminal(task: TaskView): boolean {
  return ['completed', 'failed', 'cancelled', 'superseded', 'awaiting_confirmation'].includes(task.status);
}

export class TaskCoordinator {
  private seenVersions = new Map<string, number>();

  isStale(task: TaskView): boolean {
    const seen = this.seenVersions.get(task.task_id);
    if (seen !== undefined && task.status_version < seen) return true;
    this.seenVersions.set(task.task_id, task.status_version);
    return false;
  }

  reset(): void {
    this.seenVersions.clear();
  }
}
