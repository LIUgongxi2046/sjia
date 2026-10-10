import { ApiError, fetchJson, fetchPrivateDocument, getSessionCsrfToken, type FieldError, type RecoveryAction } from './client';
import { validateApiValue } from './schema';
import type { ArchiveUploadOptions } from '../../../frontend-design/v2-types';
import { completeFileGroup, createFileGroup, uploadFile } from './endpoints';

export async function readUploadOptions(patientId: string, signal?: AbortSignal): Promise<ArchiveUploadOptions> {
  const value = validateApiValue<ArchiveUploadOptions>('v2-protocol.schema.json#/$defs/ArchiveUploadOptions',
    await fetchJson<unknown>(`/api/v2/patients/${encodeURIComponent(patientId)}/archive-upload-options`, { signal }));
  if (value.patient_id !== patientId) throw new Error('上传来源与当前患儿不一致。');
  return value;
}

export async function readUploadGroup(groupId: string, signal?: AbortSignal) {
  const value = validateApiValue<{ group_id: string; patient_id: string; authorization_id: string; source_revision_id: string; version: string; status: string;
    files: { file_id: string; media_type: string; purpose: string; checksum: string; size_bytes: number; ordinal: number; page_count: number }[] }>(
    'v2-protocol.schema.json#/$defs/UploadGroupView', await fetchJson<unknown>(`/api/uploads/${encodeURIComponent(groupId)}`, { signal }));
  if (value.group_id !== groupId || value.files.some(file => file.purpose !== 'original')) throw new Error('原件上传结果与准确上传组不一致。');
  return value;
}

export async function saveConfirmedVoiceText(patientId: string, sourceRevisionId: string, text: string,
  csrfToken?: string, signal?: AbortSignal) {
  if (!text.trim() || text.length > 8000) throw new Error('确认文字不能为空且不能超过 8000 个字符。');
  const options = await readUploadOptions(patientId, signal);
  const authorization = options.authorizations.find(value => value.source_revision_id === sourceRevisionId);
  if (!authorization) throw new Error('当前来源没有有效的家庭原件上传授权。');
  if (!options.media_types.includes('text/plain')) throw new Error('当前上传配置没有开放 text/plain 原件，请由支持人员核验模拟上传配置。');
  const file = new File([text], 'confirmed-voice.txt', { type: 'text/plain' });
  if (file.size > options.max_upload_bytes) throw new Error('确认文字超出当前原件上传容量。');
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  const checksum = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
  const requestKey = `sjia.voice-original.${patientId}.${sourceRevisionId}.${checksum}`;
  const remembered = sessionStorage.getItem(requestKey);
  const request = remembered ? JSON.parse(remembered) as { groupId: string; uploadId: string }
    : { groupId: crypto.randomUUID(), uploadId: crypto.randomUUID() };
  sessionStorage.setItem(requestKey, JSON.stringify(request));
  const groupId = request.groupId;
  const created = await createFileGroup(patientId, csrfToken, authorization.authorization_id, groupId);
  if (created.group_id !== groupId) throw new Error('上传组回执与本次请求标识不一致。');
  const receipt = await uploadFile(groupId, request.uploadId, await file.arrayBuffer(), 'text/plain');
  await completeFileGroup(groupId, csrfToken);
  const saved = await readUploadGroup(groupId, signal);
  if (saved.group_id !== groupId || saved.patient_id !== patientId || saved.authorization_id !== authorization.authorization_id
      || saved.source_revision_id !== sourceRevisionId || saved.status !== 'available' || saved.files.length !== 1) {
    throw new Error('确认文字原件保存后重新读取的上传组范围或状态不一致。');
  }
  const original = saved.files[0];
  if (original.file_id !== receipt.file_id || original.media_type !== 'text/plain'
      || original.checksum !== receipt.checksum || original.size_bytes !== receipt.size_bytes
      || original.purpose !== 'original' || original.page_count < 1) {
    throw new Error('确认文字原件保存回执与原件目录不一致。');
  }
  const reread = await fetchPrivateDocument(`/api/files/${encodeURIComponent(original.file_id)}/content`, 'text/plain',
    signal ?? new AbortController().signal);
  const rereadText = await reread.text();
  if (rereadText !== text || checksum !== receipt.checksum) throw new Error('确认文字原件内容或 SHA-256 重新读取不一致。');
  sessionStorage.removeItem(requestKey);
  return { group_id: groupId, file_id: original.file_id, checksum: receipt.checksum,
    source_revision_id: sourceRevisionId, authorization_id: authorization.authorization_id, confirmed_text: rereadText };
}

export async function uploadArchiveFile(groupId: string, uploadId: string, data: ArrayBuffer, mediaType: string, ordinal: number, signal?: AbortSignal) {
  const checksum = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', data)), value => value.toString(16).padStart(2, '0')).join('');
  const params = new URLSearchParams({ checksum, size: String(data.byteLength), media_type: mediaType, ordinal: String(ordinal) });
  const response = await fetch(`/api/uploads/${encodeURIComponent(groupId)}/files/${encodeURIComponent(uploadId)}?${params}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/octet-stream', 'X-CSRFToken': getSessionCsrfToken() ?? '' }, body: data,
    signal, credentials: 'same-origin', cache: 'no-store',
  });
  if (!response.ok) {
    const value = validateApiValue<{ code: string; message: string; retryable: boolean; recovery: RecoveryAction; field_errors: FieldError[] }>(
      'v2-protocol.schema.json#/$defs/UploadError', await response.json());
    throw new ApiError(response.status, value.code, value.message, value.retryable, value.recovery, value.field_errors);
  }
  const value = validateApiValue<{ file_id: string; checksum: string; size_bytes: number }>('v2-protocol.schema.json#/$defs/UploadFileReceipt', await response.json());
  if (value.checksum !== checksum || value.size_bytes !== data.byteLength) throw new Error('上传回执与完整原件字节不一致。');
  return value;
}
