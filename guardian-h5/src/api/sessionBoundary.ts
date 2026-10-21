export const sessionBoundaryKey = 'sjia.session.boundary.v1';
const sessionLock = 'sjia.session.mutation.v1';
const windowId = crypto.randomUUID();

export interface SessionBoundaryRecord {
  version: string;
  owner: string;
  phase: 'changing' | 'settled';
}
export type SessionEpoch = string | null;
export interface SessionMutationResult<T> { value: T; epoch: SessionEpoch }
export function ownsSessionBoundary(record: SessionBoundaryRecord | null): boolean { return record?.owner === windowId; }

export function readSessionBoundary(): { record: SessionBoundaryRecord | null; epoch: SessionEpoch } {
  const epoch = localStorage.getItem(sessionBoundaryKey);
  if (epoch === null) return { record: null, epoch };
  const value: unknown = JSON.parse(epoch);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('浏览器会话同步记录格式无效。');
  const record = value as Record<string, unknown>;
  const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
  if (Object.keys(record).sort().join(',') !== 'owner,phase,version'
    || typeof record.version !== 'string' || !uuid.test(record.version)
    || typeof record.owner !== 'string' || !uuid.test(record.owner)
    || !['changing', 'settled'].includes(record.phase as string)) throw new Error('浏览器会话同步记录字段无效。');
  return { record: record as unknown as SessionBoundaryRecord, epoch };
}

export function captureSessionEpoch(control = false): SessionEpoch {
  const boundary = readSessionBoundary();
  if (boundary.record?.phase === 'changing' && !(control && boundary.record.owner === windowId)) {
    throw new DOMException('其他窗口正在处理账号操作，请等待会话核验。', 'AbortError');
  }
  return boundary.epoch;
}

export function isSessionEpochCurrent(epoch: SessionEpoch): boolean {
  const current = readSessionBoundary();
  return current.epoch === epoch && current.record?.phase !== 'changing';
}

export function assertSessionEpoch(epoch: SessionEpoch, control = false): void {
  if (captureSessionEpoch(control) !== epoch) throw new DOMException('浏览器登录状态已改变，本次内容未被采用。', 'AbortError');
}

function writeBoundary(record: SessionBoundaryRecord): SessionEpoch {
  const epoch = JSON.stringify(record);
  localStorage.setItem(sessionBoundaryKey, epoch);
  return epoch;
}

export async function runSessionMutation<T>(action: () => Promise<T>): Promise<SessionMutationResult<T>> {
  if (!navigator.locks) throw new Error('当前浏览器缺少账号操作同步能力，请使用支持 Web Locks 的浏览器。');
  return navigator.locks.request(sessionLock, { mode: 'exclusive', ifAvailable: true }, async lock => {
    if (!lock) throw new Error('其他窗口正在处理账号操作，请等待完成后重试。');
    const record: SessionBoundaryRecord = { version: crypto.randomUUID(), owner: windowId, phase: 'changing' };
    writeBoundary(record);
    let epoch: SessionEpoch = null;
    let value: T;
    try { value = await action(); }
    finally { epoch = writeBoundary({ ...record, phase: 'settled' }); }
    return { value, epoch };
  });
}

export async function runSessionRead<T>(action: () => Promise<T>): Promise<SessionMutationResult<T>> {
  if (!navigator.locks) throw new Error('当前浏览器缺少账号操作同步能力。');
  return navigator.locks.request(sessionLock, { mode: 'shared' }, async () => {
    const epoch = captureSessionEpoch();
    const value = await action();
    assertSessionEpoch(epoch);
    return { value, epoch };
  });
}

// 取得已释放的浏览器锁后，通过实际会话请求确认中断操作的状态。
export async function recoverSessionBoundary(check: () => Promise<void>): Promise<boolean> {
  if (!navigator.locks) throw new Error('当前浏览器缺少账号操作同步能力。');
  return navigator.locks.request(sessionLock, { mode: 'exclusive', ifAvailable: true }, async lock => {
    if (!lock) return false;
    if (readSessionBoundary().record?.phase !== 'changing') return true;
    const record: SessionBoundaryRecord = { version: crypto.randomUUID(), owner: windowId, phase: 'changing' };
    writeBoundary(record);
    await check();
    writeBoundary({ ...record, phase: 'settled' });
    return true;
  });
}
