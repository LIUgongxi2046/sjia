// SubmissionCoordinator：正式提交状态机（FRONT LLD 4.3）。
export type SubmissionPhase =
  | 'unavailable'
  | 'ready'
  | 'preparing'
  | 'submitting'
  | 'uncertain'
  | 'checking'
  | 'failed'
  | 'conflict'
  | 'committed'
  | 'saved';

export interface RequestViewState {
  state: 'pending' | 'processing' | 'unknown' | 'not_found' | 'failed' | 'committed';
  may_retry?: boolean;
  receipt?: { resource_id: string; resource_version?: string } | null;
}

export interface SubmissionState {
  phase: SubmissionPhase;
  intentId?: string;
  generation?: number;
  requestId?: string;
  recordPath?: string;
  errorCode?: string;
}

export const INITIAL_SUBMISSION: SubmissionState = { phase: 'unavailable' };

export function toReady(state: SubmissionState): SubmissionState {
  return { ...state, phase: 'ready', errorCode: undefined };
}

export function prepare(state: SubmissionState, intentId: string, generation: number, requestId: string): SubmissionState {
  return { ...state, phase: 'submitting', intentId, generation, requestId, errorCode: undefined };
}

export function timeout(state: SubmissionState): SubmissionState {
  return { ...state, phase: 'uncertain' };
}

export function beginCheck(state: SubmissionState): SubmissionState {
  return { ...state, phase: 'checking' };
}

export function applyQueryResult(state: SubmissionState, result: RequestViewState): SubmissionState {
  switch (result.state) {
    case 'committed':
      return { ...state, phase: 'committed' };
    case 'failed':
      return { ...state, phase: result.may_retry ? 'failed' : 'uncertain', errorCode: result.may_retry ? undefined : 'RETRY_DISALLOWED' };
    case 'pending':
    case 'processing':
    case 'unknown':
    case 'not_found':
      return { ...state, phase: 'uncertain' };
    default:
      return { ...state, phase: 'uncertain' };
  }
}

export function conflict(state: SubmissionState): SubmissionState {
  return { ...state, phase: 'conflict', errorCode: 'VERSION_CONFLICT' };
}

export function saved(state: SubmissionState, recordPath: string): SubmissionState {
  return { ...state, phase: 'saved', recordPath };
}

export function retry(state: SubmissionState, generation: number): SubmissionState {
  return { ...state, phase: 'submitting', generation, errorCode: undefined };
}
