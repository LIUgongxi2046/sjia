import { useEffect, useRef, useState } from 'react';
import { completeFileGroup, createFileGroup, uploadFile } from '../api/endpoints';
import { useAccess } from '../state/access';
import { ApiError } from '../api/client';
import { PhotoReview } from './PhotoReview';
import { Camera, Images } from './ThreeDIcon';
import { FileText, X } from 'lucide-react';
import { UI2Sheet } from './UI2Frame';
import { PrivateSourceGallery } from './PrivateSourceGallery';
import { readUploadOptions } from '../api/archiveUploads';
import type { ArchiveUploadOptions } from '../../../frontend-design/v2-types';

export interface UploadedPhoto { file_id: string; name: string; recognized_count?: number; recognition_error?: string }

export function UploadedPhotos({ photos, onChange, onUploaded, busy = false, onBusyChange, ui2 = false, therapy = false }: {
  photos: UploadedPhoto[];
  onChange: (photos: UploadedPhoto[]) => void;
  onUploaded?: (photo: UploadedPhoto) => Promise<number>;
  busy?: boolean;
  onBusyChange?: (busy: boolean) => void;
  ui2?: boolean;
  therapy?: boolean;
}) {
  const { patientId, csrfToken, principalId } = useAccess();
  const [uploadOptions, setUploadOptions] = useState<ArchiveUploadOptions | null>(null), [authorizationId, setAuthorizationId] = useState('');
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recognizingId, setRecognizingId] = useState<string | null>(null);
  const [recognitionErrors, setRecognitionErrors] = useState<Record<string, string>>({});
  const [manage, setManage] = useState(false);
  const camera = useRef<HTMLInputElement>(null);
  const album = useRef<HTMLInputElement>(null);
  const replacement = useRef<string | null>(null);
  const currentPhotos = useRef(photos);
  currentPhotos.current = photos;
  const locked = busy || uploading || recognizingId !== null;
  useEffect(() => {
    const controller = new AbortController(); setUploadOptions(null); setAuthorizationId('');
    if (patientId && principalId) void readUploadOptions(patientId, controller.signal).then(value => {
      if (!controller.signal.aborted) { setUploadOptions(value); if (value.authorizations.length === 1) setAuthorizationId(value.authorizations[0].authorization_id); }
    }).then(undefined, failure => { if (!(failure instanceof Error)) throw failure; if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [patientId, principalId]);

  async function recognize(photo: UploadedPhoto) {
    if (!onUploaded) return;
    setRecognizingId(photo.file_id);
    setRecognitionErrors(current => ({ ...current, [photo.file_id]: '' }));
    onBusyChange?.(true);
    await onUploaded(photo).then(count => {
      const next = currentPhotos.current.map(value => value.file_id === photo.file_id ? { ...value, recognized_count: count, recognition_error: undefined } : value);
      currentPhotos.current = next;
      onChange(next);
    }, failure => {
      if (!(failure instanceof Error)) throw failure;
      const reason = failure instanceof ApiError ? failure.message : failure.name === 'TimeoutError' ? '识别服务等待超时，请稍后重新识别' : failure.message;
      const message = `识别未完成：${reason}。原图与已有内容已经保留。`;
      setRecognitionErrors(current => ({ ...current, [photo.file_id]: message }));
      const next = currentPhotos.current.map(value => value.file_id === photo.file_id ? { ...value, recognition_error: message } : value);
      currentPhotos.current = next;
      onChange(next);
    }).finally(() => { setRecognizingId(null); });
  }

  async function retry(photo: UploadedPhoto) {
    if (locked) return;
    await recognize(photo).finally(() => onBusyChange?.(false));
  }

  useEffect(() => {
    const cancel = () => { replacement.current = null; onBusyChange?.(false); };
    const inputs = [camera.current, album.current];
    inputs.forEach(input => input?.addEventListener('cancel', cancel));
    return () => { inputs.forEach(input => input?.removeEventListener('cancel', cancel)); };
  }, [onBusyChange]);

  async function upload(files: File[]) {
    if (!patientId || !files.length || locked) return;
    const replaceId = replacement.current;
    replacement.current = null;
    setUploading(true);
    onBusyChange?.(true);
    setError(null);
    const operation = async () => {
      if (!uploadOptions || !authorizationId) throw new Error('请选择当前获准资料来源。');
      if ((replaceId ? photos.length - 1 : photos.length) + files.length > 10) throw new Error('每份资料最多10张照片');
      for (const file of files) {
        if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size <= 0 || file.size > 20 * 1024 * 1024) throw new Error('照片需使用 JPEG、PNG 或 WebP 格式，每张不超过20MB');
        const bitmap = await createImageBitmap(file);
        const pixels = bitmap.width * bitmap.height;
        bitmap.close();
        if (pixels > 40_000_000) throw new Error('照片解码后不能超过4000万像素');
      }
      for (const file of files) {
        const group = await createFileGroup(patientId!, csrfToken ?? undefined, authorizationId);
        const result = await uploadFile(group.group_id, crypto.randomUUID(), await file.arrayBuffer(), file.type);
        await completeFileGroup(group.group_id, csrfToken ?? undefined);
        const photo = { file_id: result.file_id, name: file.name };
        const next = replaceId
          ? currentPhotos.current.map(value => value.file_id === replaceId ? photo : value)
          : [...currentPhotos.current, photo];
        currentPhotos.current = next;
        onChange(next);
        await recognize(photo);
      }
    };
    await operation().then(undefined, failure => { setError(failure.message); }).finally(() => {
      setUploading(false);
      onBusyChange?.(false);
    });
  }

  function select(id: string | null, source: 'camera' | 'album') {
    if (locked || !patientId) return;
    replacement.current = id;
    onBusyChange?.(true);
    const input = source === 'camera' ? camera.current : album.current;
    if (input) { input.multiple = !id && source === 'album'; input.click(); }
  }

  function move(id: string, direction: -1 | 1) {
    if (locked) return;
    const next = [...photos];
    const index = next.findIndex(photo => photo.file_id === id);
    if (index < 0 || index + direction < 0 || index + direction >= next.length) throw new Error('照片页序无效');
    [next[index], next[index + direction]] = [next[index + direction], next[index]];
    onChange(next);
  }

  function recognitionStatus(photo: UploadedPhoto) {
    return recognizingId === photo.file_id ? '正在识别本页，请稍候。' : photo.recognition_error || recognitionErrors[photo.file_id]
      || (photo.recognized_count === undefined ? '原图已保存，可以开始识别。' : photo.recognized_count === 0 ? '本页未识别到可填写的项目。请核对原件，也可以手动填写。' : `本页已识别 ${photo.recognized_count} 项候选，请对照原图核对。`);
  }

  const inputs = <>
    {uploadOptions && uploadOptions.authorizations.length > 1 && <label className="field"><span>资料用途授权</span><select value={authorizationId} disabled={locked} onChange={event => setAuthorizationId(event.target.value)}><option value="">请选择准确资料来源</option>{uploadOptions.authorizations.map(value => <option key={value.authorization_id} value={value.authorization_id}>{value.source_label} · {value.evidence_ref}</option>)}</select></label>}
    <input ref={camera} aria-label="拍摄原始照片" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" disabled={locked || !patientId} hidden onChange={event => { const files = [...(event.target.files ?? [])]; event.target.value = ''; if (!files.length) onBusyChange?.(false); else void upload(files); }} />
    <input ref={album} aria-label="选择原始照片" type="file" accept="image/jpeg,image/png,image/webp" multiple disabled={locked || !patientId} hidden onChange={event => { const files = [...(event.target.files ?? [])]; event.target.value = ''; if (!files.length) onBusyChange?.(false); else void upload(files); }} />
  </>;

  if (ui2) return <>
    <section className="card photo-intro"><span className="large-icon"><Camera size={54} /></span><h2>{therapy ? '添加处方或治疗资料' : '把报告原件保留下来'}</h2><p>文字清晰、页面完整，按文书顺序选择。</p>
      <button className="button" disabled={locked || !patientId || photos.length >= 10} onClick={() => select(null, 'album')}>{uploading ? '正在保存原件' : '拍照或选择照片'}</button>{inputs}
      <small>{patientId ? '选择后保存原件并请求识别，候选内容需要本人确认。' : '登录并核验权限后才能保存原件与识别内容。'}</small></section>
    {photos.length > 0 && <section className="card form-card"><div className="section-heading"><h2>已选择的文件</h2></div>
      {photos.map(photo => <div className="selected-file" key={photo.file_id}><FileText size={18} /><span>{photo.name}</span><button aria-label={`移除 ${photo.name}`} disabled={locked} onClick={() => onChange(photos.filter(value => value.file_id !== photo.file_id))}><X size={17} /></button></div>)}
      <p className="note">已保存 {photos.length} 份原件；{therapy ? '治疗' : '报告'}内容需要核对后确认保存。</p>
      {onUploaded && photos.map(photo => <p key={photo.file_id} role={photo.recognition_error || recognitionErrors[photo.file_id] ? 'alert' : 'status'}>{photo.name}：{recognitionStatus(photo)}</p>)}
      <button className="text-action" disabled={locked} onClick={() => setManage(true)}>查看原件与调整页序</button></section>}
    {error && <p role="alert">{error}</p>}
    {manage && <UI2Sheet title="原件与页序" onClose={() => setManage(false)}>
      <PrivateSourceGallery files={photos.map((photo, index) => ({ file_id: photo.file_id, ordinal: index + 1 }))} />
      {photos.map((photo, index) => <section className="card form-card" key={photo.file_id}><h3>第 {index + 1} 页 · {photo.name}</h3><p role={photo.recognition_error || recognitionErrors[photo.file_id] ? 'alert' : 'status'}>{recognitionStatus(photo)}</p>
        <button className="text-action" disabled={locked || index === 0} onClick={() => move(photo.file_id, -1)}>向前调整</button><button className="text-action" disabled={locked || index === photos.length - 1} onClick={() => move(photo.file_id, 1)}>向后调整</button>
        <button className="text-action" disabled={locked} onClick={() => select(photo.file_id, 'camera')}>拍摄替换本页</button><button className="text-action" disabled={locked} onClick={() => select(photo.file_id, 'album')}>相册替换本页</button>
        {onUploaded && <button className="text-action" disabled={locked} onClick={() => void retry(photo)}>重新识别本页</button>}</section>)}
      <small>重新识别成功后，本页候选将更新，需要重新核对。</small></UI2Sheet>}
  </>;

  return <>
    <p className="field-hint">同一份资料最多10张照片，每张不超过20MB。可以继续添加并调整页序。</p>{inputs}
    <div className="photo-upload-actions"><button type="button" disabled={locked || photos.length >= 10} onClick={() => select(null, 'camera')}><Camera size={26} />拍摄照片</button><button type="button" disabled={locked || photos.length >= 10} onClick={() => select(null, 'album')}><Images size={26} />相册多选</button></div>
    <PhotoReview collection={{ photos: photos.map(photo => ({ id: photo.file_id, label: photo.name, url: `/api/files/${photo.file_id}/content` })), busy: locked,
      onMove: move,
      onRemove: id => onChange(photos.filter(photo => photo.file_id !== id)),
      onReplace: select,
      renderRecognition: onUploaded ? id => {
        const photo = photos.find(value => value.file_id === id)!;
        const failed = photo.recognition_error || recognitionErrors[id];
        return <div className="photo-recognition">
          <p role={failed ? 'alert' : 'status'} className={failed ? 'form-error' : 'field-hint'}>{recognizingId === id ? '正在识别本页，请稍候。' : failed || (photo.recognized_count === undefined ? '原图已保存，可以开始识别。' : photo.recognized_count === 0 ? '本页未识别到可填写的项目。请检查照片是否清晰、内容是否完整，也可以手动填写。' : `本页已识别 ${photo.recognized_count} 项候选，请对照原图核对。`)}</p>
          <div className="action-pair"><button type="button" className="text-action" disabled={locked} onClick={() => void retry(photo)}>重新识别本页</button>{Boolean(photo.recognized_count) && <a className="text-action" href="#recognition-candidates">核对识别内容</a>}</div>
          <p className="field-hint">重新识别成功后，本页候选将更新，请重新核对。</p>
        </div>;
      } : undefined,
    }} />
    {error && <p role="alert" className="form-error">{error}</p>}
  </>;
}
