import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ImageViewer, type MultiImageViewerRef } from 'antd-mobile';
import { ChevronLeft, ChevronRight, Image, RotateCw, X } from './ThreeDIcon';
import type { SourcePhoto } from './PhotoReview';

export function SourceGallery({ photos, initialIndex = 0, ui2 = false, autoOpen = false, onClose }: {
  photos: readonly SourcePhoto[]; initialIndex?: number; ui2?: boolean; autoOpen?: boolean; onClose?: () => void;
}) {
  const [open, setOpen] = useState(autoOpen);
  const [index, setIndex] = useState(initialIndex);
  const [rotation, setRotation] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const slides = useRef<MultiImageViewerRef>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    dialog.current?.showModal();
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previous; };
  }, [open]);

  if (!photos.length) return <p className="field-hint">本份资料尚未附原图。</p>;
  const close = () => dialog.current?.close();
  return <>
    <button ref={trigger} type="button" className={ui2 ? 'list-row' : 'source-gallery-trigger'} onClick={() => { setIndex(initialIndex); setRotation(0); setOpen(true); }}>
      {ui2 ? <><span className="row-icon"><Image size={30} /></span><span className="row-text"><strong>查看原始资料</strong><small>每项内容都保留原文出处</small></span><ChevronRight size={17} /></> : <><img className="source-photo" src={photos[initialIndex].url} alt={photos[initialIndex].label} loading="lazy" /><span>全屏查看原图 · 共 {photos.length} 页</span></>}
    </button>
    {open && createPortal(<dialog ref={dialog} className="source-gallery-dialog" aria-label="原图查看器" onClose={() => { setOpen(false); trigger.current?.focus(); onClose?.(); }}>
      <ImageViewer.Multi ref={slides} images={photos.map(photo => photo.url)} defaultIndex={initialIndex} visible maxZoom={5} onClose={close}
        onIndexChange={value => { setIndex(value); setRotation(0); }}
        imageRender={(url, { ref, index: imageIndex }) => <img ref={ref} src={url} alt={photos[imageIndex].label} draggable={false} style={{ rotate: imageIndex === index ? `${rotation}deg` : '0deg' }} />} />
        <div className="source-gallery-controls"><p role="status">第 {index + 1} 页，共 {photos.length} 页 · 双指缩放原图</p><div>
          <button type="button" aria-label="上一张原图" disabled={index === 0} onClick={() => slides.current?.swipeTo(index - 1)}><ChevronLeft size={24} />上一张</button>
          <button type="button" onClick={() => setRotation(value => (value + 90) % 360)}><RotateCw size={24} />旋转</button>
          <button type="button" aria-label="下一张原图" disabled={index === photos.length - 1} onClick={() => slides.current?.swipeTo(index + 1)}><ChevronRight size={24} />下一张</button>
          <button type="button" onClick={close} autoFocus><X size={24} />关闭原图</button>
        </div></div>
    </dialog>, document.body)}
  </>;
}
