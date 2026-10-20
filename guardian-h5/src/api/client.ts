// 同源 Fetch 客户端：CSRF、幂等头、AbortController 与稳定错误映射。
import { assertSessionEpoch, captureSessionEpoch } from './sessionBoundary';
export type RecoveryAction = 'none' | 'reauthenticate' | 'reload' | 'query_request' | 'query_task' | 'retry_after' | 'review_input';

let sessionCsrfToken: string | null = null;
export function setSessionCsrfToken(token: string | null) { sessionCsrfToken = token; }
export function getSessionCsrfToken() { return sessionCsrfToken; }

export interface FieldError {
  path: string;
  code: string;
  message: string;
}

export interface ApiErrorBody {
  schema_version: string;
  code: string;
  message: string;
  trace_id: string;
  retryable: boolean;
  recovery: RecoveryAction;
  request_id: string | null;
  task_id: string | null;
  field_errors: FieldError[];
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public retryable: boolean,
    public recovery: RecoveryAction,
    public fieldErrors: FieldError[] = [],
  ) {
    super(message || code);
    this.name = 'ApiError';
  }
}

export interface RequestGuard {
  signal?: AbortSignal;
  /** Optional live authorization fence for clinical async continuations. */
  isCurrent?: () => boolean;
}

export function checkRequestGuard(guard: RequestGuard = {}) {
  guard.signal?.throwIfAborted();
  if (guard.isCurrent && !guard.isCurrent()) throw new DOMException('当前访问核验已暂停或范围已改变。', 'AbortError');
}

export interface RequestOptions extends RequestGuard {
  sessionControl?: boolean;
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  csrfToken?: string;
  idempotencyKey?: string;
  writeIntent?: string;
  intentGeneration?: string;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** Validate the deployed legacy error envelope before adopting retry actions. */
export async function parseError(response: Response): Promise<ApiError> {
  let value: unknown;
  try { value = await response.json(); } catch { value = null; }
  const actions: RecoveryAction[] = ['none', 'reauthenticate', 'reload', 'query_request', 'query_task', 'retry_after', 'review_input'];
  const string = (item: unknown): item is string => typeof item === 'string' && item.length > 0;
  const valid = isObject(value) && string(value.code) && string(value.message)
    && (value.retryable === undefined || typeof value.retryable === 'boolean')
    && (value.recovery === undefined || actions.includes(value.recovery as RecoveryAction))
    && (value.field_errors === undefined || Array.isArray(value.field_errors) && value.field_errors.every(field =>
      isObject(field) && typeof field.path === 'string' && string(field.code) && (field.message === undefined || string(field.message))));
  if (!valid) return new ApiError(response.status, `HTTP_${response.status}`, `请求失败（${response.status}），错误响应格式无效。`, false, 'none');
  const body = value as { code: string; message: string; retryable?: boolean; recovery?: RecoveryAction; field_errors?: Array<{ path: string; code: string; message?: string }> };
  return new ApiError(response.status, body.code, body.message, body.retryable ?? false, body.recovery ?? 'none',
    (body.field_errors ?? []).map(field => ({ path: field.path, code: field.code, message: field.message ?? field.code })));
}

/** Transport envelope only. Domain callers still validate their exact schema. */
export async function readJsonObject(response: Response, signal?: AbortSignal): Promise<Record<string, unknown>> {
  signal?.throwIfAborted();
  let value: unknown;
  try { value = await response.json(); } catch {
    signal?.throwIfAborted();
    throw new Error('接口响应不是有效 JSON，本次内容未被采用。');
  }
  signal?.throwIfAborted();
  if (!isObject(value)) throw new Error('接口响应必须是 JSON 对象，本次内容未被采用。');
  return value;
}

export async function fetchJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const epoch = captureSessionEpoch(options.sessionControl);
  const check = () => { checkRequestGuard(options); assertSessionEpoch(epoch, options.sessionControl); };
  await Promise.resolve();
  check();
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  const csrfToken = options.csrfToken ?? sessionCsrfToken;
  if (csrfToken && options.method && options.method !== 'GET') headers['X-CSRFToken'] = csrfToken;
  if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey;
  if (options.writeIntent) headers['X-Write-Intent'] = options.writeIntent;
  if (options.intentGeneration) headers['X-Intent-Generation'] = options.intentGeneration;

  const response = await fetch(path, {
    method: options.method ?? 'GET',
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
    credentials: 'same-origin',
  });

  check();
  if (!response.ok) {
    const failure = await parseError(response);
    check();
    throw failure;
  }
  if (response.status === 204) return undefined as T;
  const value = await readJsonObject(response, options.signal) as T;
  check();
  return value;
}

export async function fetchPrivateImage(path: string, signal: AbortSignal): Promise<Blob> {
  const epoch = captureSessionEpoch();
  const response = await fetch(path, { headers: { Accept: 'image/png,image/jpeg,image/webp,application/json' },
    signal, credentials: 'same-origin', cache: 'no-store' });
  assertSessionEpoch(epoch);
  if (!response.ok) throw await parseError(response);
  const mediaType = response.headers.get('Content-Type');
  if (!mediaType || !['image/png', 'image/jpeg', 'image/webp'].includes(mediaType)) throw new Error('资料内容类型与图片读取要求不一致。');
  const value = await response.blob();
  signal.throwIfAborted(); assertSessionEpoch(epoch);
  return value;
}

export async function fetchPrivateDocument(path: string, mediaType: string, signal: AbortSignal): Promise<Blob> {
  const epoch = captureSessionEpoch();
  if (!['image/png', 'image/jpeg', 'image/webp', 'application/pdf', 'text/plain'].includes(mediaType)) throw new Error('当前原件媒体类型没有登记查看方式。');
  const response = await fetch(path, { headers: { Accept: `${mediaType},application/json` }, signal, credentials: 'same-origin', cache: 'no-store' });
  assertSessionEpoch(epoch);
  if (!response.ok) throw await parseError(response);
  if (response.headers.get('Content-Type') !== mediaType) throw new Error('原件内容类型与准确文件版本不一致。');
  const value = await response.blob();
  signal.throwIfAborted(); assertSessionEpoch(epoch);
  return value;
}

export async function postAudio(path: string, audio: Blob, signal: AbortSignal): Promise<{ text: string; duration_seconds: number }> {
  const epoch = captureSessionEpoch();
  const headers: Record<string, string> = { Accept: 'application/json', 'Content-Type': audio.type };
  if (sessionCsrfToken) headers['X-CSRFToken'] = sessionCsrfToken;
  const response = await fetch(path, { method: 'POST', body: audio, headers, signal, credentials: 'same-origin' });
  assertSessionEpoch(epoch);
  if (!response.ok) throw await parseError(response);
  const value = await response.json();
  signal.throwIfAborted(); assertSessionEpoch(epoch);
  return value;
}
