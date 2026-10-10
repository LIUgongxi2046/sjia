import { useState } from 'react';
import type { ReactNode } from 'react';
import { Disclosure } from './ClinicalUI';
import { SourceGallery } from './SourceGallery';

export interface SourcePhoto { id: string; url: string; label: string }
export interface PhotoCollection {
  photos: readonly SourcePhoto[];
  busy: boolean;
  onMove: (id: string, direction: -1 | 1) => void;
  onReplace: (id: string, source: 'camera' | 'album') => void;
  onRemove: (id: string) => void;
  renderRecognition?: (id: string) => ReactNode;
}

export function PhotoReview({ collection }: { collection: PhotoCollection | null }) {
  const [removing, setRemoving] = useState<string | null>(null);
  if (!collection) return <Disclosure title="查看照片整理方式"><p>添加照片后，在这里查看原图、调整页序，或重新拍摄。移除照片前需要确认。</p><p>当前尚未开放照片服务，没有接收或保存照片。</p></Disclosure>;
  const { photos, busy } = collection;
  if (new Set(photos.map(photo => photo.id)).size !== photos.length) throw new Error('照片标识必须唯一');
  return <section aria-label="本份资料照片" className="field-group">
    <p role="status">{busy ? '正在处理照片，请稍候。' : photos.length ? `共 ${photos.length} 页，请按原资料顺序排列。` : '尚未添加照片。'}</p>
    {photos.map((photo, index) => <section className="report-item" key={photo.id} aria-label={`照片 ${index + 1}`}>
      <h3>第 {index + 1} 页</h3>
      <SourceGallery photos={photos} initialIndex={index} />
      {collection.renderRecognition?.(photo.id)}
      <div className="action-pair"><button type="button" className="text-action" disabled={busy || index === 0} onClick={() => collection.onMove(photo.id, -1)}>向前移动</button><button type="button" className="text-action" disabled={busy || index === photos.length - 1} onClick={() => collection.onMove(photo.id, 1)}>向后移动</button></div>
      <Disclosure title="更换或移除照片"><div className="action-pair"><button type="button" className="text-action" disabled={busy} onClick={() => collection.onReplace(photo.id, 'camera')}>重新拍摄</button><button type="button" className="text-action" disabled={busy} onClick={() => collection.onReplace(photo.id, 'album')}>从相册替换</button></div>
        {removing === photo.id ? <div role="group" aria-label="确认移除照片"><p>移除后，本页对应的识别内容需要重新核对。</p><div className="action-pair"><button type="button" className="text-action" disabled={busy} onClick={() => { collection.onRemove(photo.id); setRemoving(null); }}>确认移除</button><button type="button" className="text-action" disabled={busy} onClick={() => setRemoving(null)}>保留照片</button></div></div> : <button type="button" className="text-action" disabled={busy} onClick={() => setRemoving(photo.id)}>移除照片</button>}
      </Disclosure>
    </section>)}
  </section>;
}

export interface RecognitionSource {
  photo: SourcePhoto;
  page: number;
  originalText: string;
  region: { left: number; top: number; width: number; height: number };
}

export function RecognitionSourceView({ source }: { source: RecognitionSource | null }) {
  if (!source) return <Disclosure title="对照照片原文"><p>取得识别结果后，每项内容可以展开对应照片与原文位置。无法确认的项目保留待确认状态。</p></Disclosure>;
  const { left, top, width, height } = source.region;
  if (![left, top, width, height].every(Number.isFinite) || left < 0 || top < 0 || width <= 0 || height <= 0 || left + width > 1 || top + height > 1) throw new Error('识别位置超出照片范围');
  return <Disclosure title={`对照第 ${source.page} 页原文`}>
    <p>{source.originalText}</p>
    <div className="source-photo-region"><img className="source-photo" src={source.photo.url} alt={source.photo.label} /><span aria-hidden="true" style={{ left: `${left * 100}%`, top: `${top * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` }} /></div>
    <p>标记区域对应当前项目，按照片核对名称、数值、单位和时间。</p>
  </Disclosure>;
}
