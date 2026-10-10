// 同源 Fetch 客户端：CSRF、幂等头、AbortController 与稳定错误映射。
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

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
  csrfToken?: string;
  idempotencyKey?: string;
  writeIntent?: string;
  intentGeneration?: string;
}

async function parseError(response: Response): Promise<ApiError> {
  let body: Partial<ApiErrorBody> | null = null;
  try {
    body = (await response.json()) as Partial<ApiErrorBody>;
  } catch {
    body = null;
  }
  return new ApiError(
    response.status,
    body?.code ?? `HTTP_${response.status}`,
    body?.message ?? `请求失败（${response.status}）`,
    body?.retryable ?? false,
    body?.recovery ?? 'none',
    body?.field_errors ?? [],
  );
}

export async function fetchJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  await Promise.resolve();
  options.signal?.throwIfAborted();
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

  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function fetchPrivateImage(path: string, signal: AbortSignal): Promise<Blob> {
  const response = await fetch(path, { headers: { Accept: 'image/png,image/jpeg,image/webp,application/json' },
    signal, credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) throw await parseError(response);
  const mediaType = response.headers.get('Content-Type');
  if (!mediaType || !['image/png', 'image/jpeg', 'image/webp'].includes(mediaType)) throw new Error('资料内容类型与图片读取要求不一致。');
  return response.blob();
}

export async function fetchPrivateDocument(path: string, mediaType: string, signal: AbortSignal): Promise<Blob> {
  if (!['image/png', 'image/jpeg', 'image/webp', 'application/pdf', 'text/plain'].includes(mediaType)) throw new Error('当前原件媒体类型没有登记查看方式。');
  const response = await fetch(path, { headers: { Accept: `${mediaType},application/json` }, signal, credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) throw await parseError(response);
  if (response.headers.get('Content-Type') !== mediaType) throw new Error('原件内容类型与准确文件版本不一致。');
  return response.blob();
}

export async function postAudio(path: string, audio: Blob, signal: AbortSignal): Promise<{ text: string; duration_seconds: number }> {
  const headers: Record<string, string> = { Accept: 'application/json', 'Content-Type': audio.type };
  if (sessionCsrfToken) headers['X-CSRFToken'] = sessionCsrfToken;
  const response = await fetch(path, { method: 'POST', body: audio, headers, signal, credentials: 'same-origin' });
  if (!response.ok) throw await parseError(response);
  return await response.json();
}
