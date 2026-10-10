import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { ApiError } from '../../api/client';
import { createFileGroup, completeFileGroup } from '../../api/endpoints';
import { readUploadGroup, readUploadOptions, uploadArchiveFile } from '../../api/archiveUploads';
import { useAccess } from '../../state/access';
import type { ArchiveUploadOptions } from '../../../../frontend-design/v2-types';

export function ArchiveUpload({ onSaved }: { onSaved: (groupId: string) => void }) {
  const access = useAccess(), [params, setParams] = useSearchParams();
  const groupId = params.get('upload_group');
  const [options, setOptions] = useState<ArchiveUploadOptions | null>(null), [authorization, setAuthorization] = useState('');
  const [queue, setQueue] = useState<{ id: string; file: File; ordinal: number }[]>([]);
  const [group, setGroup] = useState<Awaited<ReturnType<typeof readUploadGroup>> | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<Error | null>(null), [refresh, setRefresh] = useState(0);
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const pending = useRef<AbortController | null>(null), operating = useRef(false);
  const ready = visible && !!access.patientId && !!access.principalId && !access.loading && !access.error;
  useEffect(() => {
    const changed = () => { const active = document.visibilityState === 'visible'; setVisible(active);
      if (!active) { pending.current?.abort(); setOptions(null); setGroup(null); setQueue([]); setAuthorization(''); }
      else { void access.refresh(); setRefresh(value => value + 1); } };
    document.addEventListener('visibilitychange', changed);
    return () => { document.removeEventListener('visibilitychange', changed); pending.current?.abort(); };
  }, []);
  useEffect(() => {
    if (operating.current) return;
    const controller = new AbortController(); setOptions(null); setGroup(null);
    if (ready) void readUploadOptions(access.patientId!, controller.signal).then(async value => {
      const saved = groupId ? await readUploadGroup(groupId, controller.signal) : null;
      if (controller.signal.aborted) return;
      if (saved && saved.patient_id !== access.patientId) throw new Error('原件上传结果与当前患儿不一致。');
      setOptions(value); setGroup(saved);
      if (saved) setAuthorization(saved.authorization_id);
    }).then(undefined, failure => { if (!(failure instanceof Error)) throw failure; if (!controller.signal.aborted) setError(failure); });
    return () => controller.abort();
  }, [ready, access.patientId, groupId, refresh, busy]);
  async function upload() {
    if (!ready || !options || !authorization || !queue.length || operating.current) return;
    operating.current = true; setBusy(true); setError(null);
    const controller = new AbortController(); pending.current = controller;
    const id = groupId ?? crypto.randomUUID();
    if (!groupId) setParams(current => { const next = new URLSearchParams(current); next.set('upload_group', id); return next; }, { replace: true });
    const operation = async () => {
      const receipt = await createFileGroup(access.patientId!, access.csrfToken ?? undefined, authorization, id);
      if (receipt.group_id !== id) throw new Error('上传组回执与原请求身份不一致。');
      for (const item of queue) {
        if (controller.signal.aborted) return;
        if (!options.media_types.includes(item.file.type) || item.file.size <= 0 || item.file.size > options.max_upload_bytes)
          throw new Error('所选文件格式或容量超出当前批准范围。');
        await uploadArchiveFile(id, item.id, await item.file.arrayBuffer(), item.file.type, item.ordinal, controller.signal);
      }
      if (controller.signal.aborted) return;
      await completeFileGroup(id, access.csrfToken ?? undefined);
      const saved = await readUploadGroup(id, controller.signal);
      if (saved.status !== 'available' || saved.files.length < queue.length) throw new Error('上传完成结果与准确原件数量不一致。');
      if (!controller.signal.aborted) { setGroup(saved); setQueue([]); onSaved(id); }
    };
    await operation().then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) { setError(failure); if (failure instanceof ApiError && [401, 403].includes(failure.status)) { setGroup(null); setQueue([]); void access.refresh(); } }
    }).finally(() => { operating.current = false; if (pending.current === controller) pending.current = null; setBusy(false); });
  }
  async function finishSavedGroup() {
    if (!ready || !group || !group.files.length || group.status === 'available' || operating.current) return;
    operating.current = true; setBusy(true); setError(null);
    const controller = new AbortController(); pending.current = controller;
    await completeFileGroup(group.group_id, access.csrfToken ?? undefined).then(async () => {
      const saved = await readUploadGroup(group.group_id, controller.signal);
      if (saved.status !== 'available' || saved.files.length !== group.files.length) throw new Error('已保存原件与上传完成结果不一致。');
      if (!controller.signal.aborted) { setGroup(saved); onSaved(saved.group_id); }
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) setError(failure);
    }).finally(() => { operating.current = false; if (pending.current === controller) pending.current = null; setBusy(false); });
  }
  return <section className="card form-card"><h2>提供新的原件</h2>
    {error && <p role="alert">{error.message}</p>}
    {!options && !error && <p role="status">正在读取当前上传范围</p>}
    {options && <><p>当前启用 {options.media_types.join('、')}；每份文件不超过 {options.max_upload_bytes} bytes，上传组不超过 {options.max_pages} 页。</p>
      <label className="field"><span>资料用途授权</span><select value={authorization} disabled={busy || !ready || group?.status === 'available'} onChange={event => setAuthorization(event.target.value)}>
        <option value="">请选择准确资料来源</option>{options.authorizations.map(value => <option key={value.authorization_id} value={value.authorization_id}>{value.source_label} · {value.evidence_ref}</option>)}
      </select></label>
      <label className="field"><span>选择原始资料文件</span><input type="file" multiple accept={options.media_types.join(',')} disabled={!authorization || busy || !ready || group?.status === 'available'} onChange={event => {
        const files = [...(event.target.files ?? [])]; event.target.value = '';
        setQueue(files.map((file, index) => ({ id: crypto.randomUUID(), file, ordinal: (group?.files.length ?? 0) + index + 1 })));
      }} /></label>
      {queue.map(item => <p key={item.id}>文件顺序 {item.ordinal} · {item.file.name} · {item.file.size} bytes</p>)}
      {!!queue.length && <button className="button" disabled={busy || !ready || !authorization} onClick={() => void upload()}>{busy ? '正在保存原件' : '保存所选完整原件'}</button>}
    </>}
    {group && <><p role="status">上传组状态 {group.status}；已保存 {group.files.length} 份原件。</p>{group.files.map(file => <p key={file.file_id}>文件顺序 {file.ordinal} · {file.media_type} · {file.page_count} 页 · {file.file_id}</p>)}
      {group.status === 'available' && <button className="button" disabled={busy || !ready} onClick={() => onSaved(group.group_id)}>采用已保存原件</button>}</>}
    {group && group.status !== 'available' && group.files.length > 0 && <button className="button" disabled={busy || !ready || queue.length > 0} onClick={() => void finishSavedGroup()}>完成已经保存的原件组</button>}
    <button className="text-action" disabled={busy || !ready} onClick={() => { setError(null); setRefresh(value => value + 1); }}>重新读取上传结果</button>
  </section>;
}
