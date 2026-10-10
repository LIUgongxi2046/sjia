import { useEffect, useState } from 'react';
import type { VerificationResourceView } from '../../../../frontend-design/v2-types';
import { ApiError, fetchPrivateDocument } from '../../api/client';
import type { OriginalSourceView } from '../../api/originalSourceSchema';
import { readVerificationOriginals, verificationOriginalPath } from '../../api/verifications';
import { SourceGallery } from '../../components/SourceGallery';
import { timeLabel } from '../../api/records';

export function VerificationOriginals({ resource, onRefresh }: { resource: VerificationResourceView; onRefresh: () => void }) {
  const [source, setSource] = useState<OriginalSourceView | null>(null);
  const [files, setFiles] = useState<{ id: string; ordinal: number; mediaType: string; url: string }[]>([]);
  const [error, setError] = useState<string | null>(null), [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController(), urls: string[] = [];
    setSource(null); setFiles([]); setError(null); setLoading(true);
    async function query() {
      try {
        const value = await readVerificationOriginals(resource, controller.signal);
        const images = await Promise.all(value.originals.filter(file => file.status === 'available').map(async file => {
          const query = new URLSearchParams({ checksum: file.checksum, object_version: file.object_version });
          const blob = await fetchPrivateDocument(`${verificationOriginalPath(resource)}/${file.file_id}/content?${query}`, file.media_type, controller.signal);
          const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
          if (Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('') !== file.checksum)
            throw new Error('原件内容与准确来源摘要不一致。');
          if (controller.signal.aborted) return null;
          const url = URL.createObjectURL(blob); urls.push(url);
          return { id: file.file_id, ordinal: file.ordinal, mediaType: file.media_type, url };
        }));
        if (controller.signal.aborted || document.visibilityState !== 'visible') return;
        if (images.some(file => file === null)) throw new Error('原件读取已经取消。');
        setSource(value); setFiles(images as typeof files); setLoading(false);
      } catch (failure) {
        if (controller.signal.aborted) return;
        controller.abort(); urls.forEach(url => URL.revokeObjectURL(url)); setSource(null); setFiles([]); setLoading(false);
        setError(failure instanceof Error ? failure.message : '原件读取失败。');
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) onRefresh();
      }
    }
    void query();
    return () => { controller.abort(); urls.forEach(url => URL.revokeObjectURL(url)); };
  }, [resource, refresh, onRefresh]);
  return <>
    {loading && <p role="status">正在读取准确核对版本的原件。</p>}
    {error && <section className="status-panel error" role="alert"><p>{error}</p><button className="text-action" onClick={() => setRefresh(value => value + 1)}>重新读取原件</button></section>}
    {source && <>
      <div className="pair"><span>来源医院</span><strong>{source.source_organization ?? '未知'}</strong></div>
      <div className="pair"><span>记录版本</span><strong>{source.record_version_id}</strong></div>
      {!source.originals.length && <p>本份资料尚未附原件。</p>}
      {source.originals.map(file => <section key={file.file_id} className="card form-card">
        <h3>原件顺序 {file.ordinal}</h3><p>原件版本：{file.object_version}；状态：{file.status}</p>
        <p>平台取得时间：{new Date(file.created_at).toLocaleString('zh-CN')}</p>
        <p>保存页面：{file.pages.map(page => page.ordinal).join('、') || '未知'}</p>
        {file.documents.map(document => <p key={document.document_version_id}>文书版本：{document.document_version_id}；文书形成时间：{timeLabel(document.documented_time)}</p>)}
        {file.use_authorizations.map(item => <p key={item.authorization_id}>允许用途：{item.purpose}；授权依据：{item.evidence_ref}</p>)}
        {file.purpose_copies.map(copy => <p key={copy.copy_id}>副本版本：{copy.version}；复核状态：{copy.status}；身份复核：{copy.identity_result ?? '未知'}；临床内容复核：{copy.clinical_result ?? '未知'}</p>)}
      </section>)}
      {!!files.filter(file => file.mediaType.startsWith('image/')).length && <SourceGallery ui2 photos={files.filter(file => file.mediaType.startsWith('image/'))
        .map(file => ({ id: file.id, url: file.url, label: `原件顺序 ${file.ordinal}` }))} />}
      {files.filter(file => file.mediaType === 'application/pdf').map(file => <iframe key={file.id} src={file.url} title={`PDF 原件顺序 ${file.ordinal}`} sandbox="" />)}
    </>}
  </>;
}
