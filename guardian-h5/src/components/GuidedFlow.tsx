import { useId, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ChevronLeft as ArrowLeft, ChevronRight as ArrowRight, Camera, Images as ImagePlus, ScanLine } from './ThreeDIcon';
import { Button } from 'antd-mobile';
import { Disclosure, Section } from './ClinicalUI';
import { PhotoReview } from './PhotoReview';
import type { PhotoCollection } from './PhotoReview';

export interface GuideStep {
  title: string;
  description: string;
  content: ReactNode;
}

export function GuidedFlow({ label, steps }: { label: string; steps: GuideStep[] }) {
  const [current, setCurrent] = useState(0);
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const previous = useRef(0);
  if (steps.length < 2) throw new Error('引导流程至少需要两个步骤');
  useLayoutEffect(() => {
    if (previous.current !== current) {
      heading.current?.focus();
      heading.current?.scrollIntoView({ block: 'start', behavior: 'instant' });
    }
    previous.current = current;
  }, [current]);
  return <section className="guided-flow" aria-label={label}>
    <header className="guide-heading"><h2 ref={heading} tabIndex={-1}>{steps[current].title}</h2><span aria-label={`第 ${current + 1} 步，共 ${steps.length} 步`}>{current + 1} / {steps.length}</span></header>
    {steps.map((step, index) => <div key={step.title} id={`${id}-${index}`} className="guide-panel" hidden={index !== current}>{step.content}</div>)}
    <div className="guide-controls">{current > 0 && <button className="guide-back" type="button" onClick={() => setCurrent(current - 1)}><ArrowLeft size={18} aria-hidden="true" />返回照片</button>}{current < steps.length - 1 && <button className="text-action guide-preview" type="button" onClick={() => setCurrent(current + 1)}>预览确认页面<ArrowRight size={16} aria-hidden="true" /></button>}</div>
  </section>;
}

export function PhotoIntake({ kind, collection = null }: { kind: '检验报告' | '治疗资料'; collection?: PhotoCollection | null }) {
  const id = useId();
  return <>
    <section className="photo-intake" aria-label={`${kind}拍照入口`}>
      <span className="camera-symbol"><Camera size={30} strokeWidth={1.5} aria-hidden="true" /></span>
      <p>{kind === '检验报告' ? '拍下完整报告，保持文字清楚。' : '拍下处方或治疗记录。'}</p>
      <div className="photo-actions"><Button color="primary" block disabled><Camera size={20} aria-hidden="true" />拍照添加</Button><Button block disabled><ImagePlus size={20} aria-hidden="true" />从相册选择</Button></div>
      <input id={`${id}-camera`} type="file" accept="image/*" capture="environment" disabled hidden aria-label={`拍摄${kind}`} />
      <input id={`${id}-album`} type="file" accept="image/*" multiple disabled hidden aria-label={`从相册选择${kind}`} />
    </section>
    <PhotoReview collection={collection} />
    <Disclosure title="怎样拍摄更容易识别"><ol className="step-list"><li><div><strong>拍摄完整页面</strong><p>保留报告名称、项目、单位、日期与来源。</p></div></li><li><div><strong>检查文字是否清楚</strong><p>保持光线均匀，文字模糊或反光时重新拍摄。</p></div></li><li><div><strong>一份资料对应一组照片</strong><p>多页内容按顺序添加，不混入其他患儿资料。</p></div></li></ol></Disclosure>
  </>;
}

export function RecognitionState() {
  return <Section title="尚未取得识别结果" icon={ScanLine}><p className="section-copy">识别服务尚未开放。识别后，优先显示需要你确认的内容。</p></Section>;
}
