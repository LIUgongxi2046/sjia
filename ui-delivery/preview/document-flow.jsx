import React, {useEffect,useMemo,useRef,useState} from 'react';
import {Camera,Images,Plus,ChevronLeft,ChevronRight,ZoomIn,ZoomOut,RotateCw,X,Trash2,ArrowUp,ArrowDown,Check,FileText,Pill,ScanLine} from './three-d-icons.jsx';
import {scenario} from './design-scenario.js';
import {useDocuments} from './document-store.jsx';
import './document-flow.css';

const focusNames=['铁蛋白','血小板计数','AST','天门冬氨酸氨基转移酶','甘油三酯','纤维蛋白原','LDH','乳酸脱氢酶','血常规及分类','肝功能','D-二聚体','CRP'];
const choices=['铁蛋白','血小板计数','AST','甘油三酯','纤维蛋白原','LDH','血常规及分类','肝功能','D-二聚体','CRP','其他项目'];
const abnormalFlags=['升高','降低','异常'];

function OriginalImage({photo,...props}) {
  const src=useMemo(()=>URL.createObjectURL(photo.file),[photo.file]);
  useEffect(()=>()=>URL.revokeObjectURL(src),[src]);
  return <img src={src} alt={photo.name} {...props}/>;
}

function ImageViewer({photos,initial,onClose}) {
  const [index,setIndex]=useState(initial);
  const [zoom,setZoom]=useState(1);
  const [rotation,setRotation]=useState(0);
  const modal=useRef(null);
  useEffect(()=>{const trigger=document.activeElement;modal.current.showModal();return ()=>trigger?.focus();},[]);
  const photo=photos[index];
  function change(next){setIndex(next);setZoom(1);setRotation(0);}
  return <dialog ref={modal} className="original-viewer" aria-label="查看原始图片" onCancel={onClose} onKeyDown={event=>{if(event.target.tagName==='INPUT')return;if(event.key==='ArrowRight'&&index<photos.length-1)change(index+1);if(event.key==='ArrowLeft'&&index>0)change(index-1);}}>
    <header><div><strong>原始图片</strong><small>第 {index+1} / {photos.length} 张 · {photo.name}</small></div><button type="button" onClick={onClose} aria-label="关闭原图"><X/></button></header>
    <div className="original-canvas"><div style={{width:`${zoom*100}%`,minHeight:`${zoom*100}%`}}><OriginalImage photo={photo} style={{transform:`rotate(${rotation}deg)`}}/></div></div>
    <footer><button type="button" aria-label="上一张图片" disabled={index===0} onClick={()=>change(index-1)}><ChevronLeft/></button><button type="button" aria-label="缩小图片" disabled={zoom===1} onClick={()=>setZoom(Math.max(1,zoom-.5))}><ZoomOut/></button><span>{Math.round(zoom*100)}%</span><button type="button" aria-label="放大图片" disabled={zoom===3} onClick={()=>setZoom(Math.min(3,zoom+.5))}><ZoomIn/></button><button type="button" aria-label="旋转图片" onClick={()=>setRotation((rotation+90)%360)}><RotateCw/></button><button type="button" aria-label="下一张图片" disabled={index===photos.length-1} onClick={()=>change(index+1)}><ChevronRight/></button></footer>
  </dialog>;
}

export function OriginalGallery({photos,editable=false,onRemove,onMove,compact=false}) {
  const [view,setView]=useState(null);
  return <><div className={`original-gallery ${compact?'compact-gallery':''}`} aria-label="原始图片缩略图">{photos.map((photo,index)=><div className="original-tile" key={photo.id}><button type="button" className="original-thumb" aria-label={`打开原图第${index+1}张`} onClick={()=>setView(index)}><OriginalImage photo={photo}/><span>{index+1}<ZoomIn size={14}/></span></button><small>{photo.name}</small>{editable&&<div className="original-controls"><button type="button" disabled={index===0} aria-label={`前移第${index+1}张`} onClick={()=>onMove(index,-1)}><ArrowUp size={16}/></button><button type="button" disabled={index===photos.length-1} aria-label={`后移第${index+1}张`} onClick={()=>onMove(index,1)}><ArrowDown size={16}/></button><button type="button" aria-label={`移除第${index+1}张`} onClick={()=>onRemove(photo.id)}><Trash2 size={16}/></button></div>}</div>)}</div>{view!==null&&<ImageViewer photos={photos} initial={view} onClose={()=>setView(null)}/>}</>;
}

export function PriorityResults({labs}) {
  const [filter,setFilter]=useState('关注指标');
  const abnormal=labs.filter(item=>abnormalFlags.includes(item.flag));
  const focused=labs.filter(item=>focusNames.includes(item.name)||abnormalFlags.includes(item.flag));
  const shown=filter==='异常指标'?abnormal:filter==='全部指标'?labs:focused;
  return <section className="priority-results"><div className="document-section-title"><h2>报告指标</h2><span>{labs.length} 项</span></div><div className="segments document-filters">{['关注指标','异常指标','全部指标'].map(value=><button key={value} className={value===filter?'selected':''} aria-pressed={value===filter} onClick={()=>setFilter(value)}>{value}{value==='异常指标'?` ${abnormal.length}`:''}</button>)}</div><div className="priority-list">{[...shown].sort((a,b)=>Number(abnormalFlags.includes(b.flag))-Number(abnormalFlags.includes(a.flag))).map((item,index)=><div key={index} className={`priority-item ${abnormalFlags.includes(item.flag)?'flagged':''}`}><div><strong>{item.name==='天门冬氨酸氨基转移酶'?'AST':item.name}</strong><span>{abnormalFlags.includes(item.flag)?`报告标记：${item.flag}`:focusNames.includes(item.name)?'sJIA 关注':'报告项目'}</span></div><b>{item.value}<small>{item.unit}</small></b>{item.reference&&<p>报告参考范围：{item.reference}</p>}</div>)}</div>{shown.length===0&&<p className="document-note">{labs.length===0?'尚未登记指标，可对照原图补充。':filter==='异常指标'?'尚未登记报告异常标记。':'其他结果可在全部指标中查看。'}</p>}<p className="document-note">异常标记按报告原文保留；未标记的结果不代表正常。</p></section>;
}

export function LocalDocuments({kind,onOpen}) {
  const {documents}=useDocuments();
  const selected=documents.filter(item=>!kind||item.kind===kind);
  if(!selected.length)return null;
  return <section className="local-documents"><h2>原图资料</h2>{selected.map(document=><article className="card original-record" key={document.id}><div className="document-section-title"><h3>{document.title}</h3><span>{document.photos.length} 张</span></div><OriginalGallery photos={document.photos} compact/><div className="document-section-title"><small>{new Date(document.savedAt).toLocaleDateString('zh-CN')} · 当前设备</small><button className="text-link" onClick={()=>onOpen(document.id)}>查看记录<ChevronRight size={16}/></button></div></article>)}</section>;
}

export function LocalDocumentDetail({id,onBack}) {
  const {documents,ready,storageError}=useDocuments();
  if(storageError)return <p role="alert" className="upload-feedback error">{storageError}</p>;
  if(!ready)return <p role="status">正在读取原图资料…</p>;
  const document=documents.find(item=>item.id===id);
  if(!document)return <section className="card field-card"><h2>当前设备未找到这份资料</h2><button className="action" onClick={onBack}>返回资料列表</button></section>;
  return <><section className="card field-card"><div className="document-section-title"><h2>{document.title}</h2><span className="badge">本人确认</span></div><p className="document-note">{document.photos.length} 张原图 · 保存在当前设备</p><OriginalGallery photos={document.photos}/></section>{document.kind==='report'?<PriorityResults labs={document.labs}/>:<section className="card field-card"><h2>治疗与使用记录</h2>{[['原药名或治疗名称',document.medicine],['实际使用状态',document.usage],['使用开始',document.startedAt],['使用结束',document.endedAt],['补充说明',document.note]].map(([name,value])=><div className="detail-pair" key={name}><span>{name}</span><strong>{value||'未填写'}</strong></div>)}</section>}<details className="disclosure"><summary>时间与资料来源<Plus size={16}/></summary><div className="disclosure-content">{[['采样或发生时间',document.occurredAt],['报告出具时间',document.resultAt],['报告机构',document.organization],['录入来源','监护人上传原图'],['专业核对','待核对']].map(([name,value])=><div className="detail-pair" key={name}><span>{name}</span><strong>{value||'未填写'}</strong></div>)}</div></details><button className="action secondary" onClick={onBack}>返回资料列表</button></>;
}

function TextField({label,value,onChange,type='text',...props}) {
  return <label className="form-field"><span>{label}</span><input type={type} value={value} onChange={event=>onChange(event.target.value)} {...props}/></label>;
}

export function PhotoPage({therapy,openRecord}) {
  const kind=therapy?'therapy':'report';
  const {ready,storageError,saveDocument}=useDocuments();
  const [step,setStep]=useState(new URLSearchParams(location.search).get('step')==='confirm'?'confirm':'overview');
  const [photos,setPhotos]=useState([]);
  const [feedback,setFeedback]=useState('');
  const [invalid,setInvalid]=useState(false);
  const [reading,setReading]=useState(false);
  const [removeId,setRemoveId]=useState(null);
  const [confirmed,setConfirmed]=useState(false);
  const [saving,setSaving]=useState(false);
  const [saved,setSaved]=useState(null);
  const [fields,setFields]=useState({medicine:'',usage:'无法确定',startedAt:'',endedAt:'',note:'',occurredAt:'',resultAt:'',organization:''});
  const [labs,setLabs]=useState([]);
  const camera=useRef(null),album=useRef(null),removeDialog=useRef(null);
  const chooseLock=useRef(false);
  useEffect(()=>{if(removeId)removeDialog.current.showModal();else removeDialog.current.close();},[removeId]);
  useEffect(()=>{if(storageError)setSaving(false);},[storageError]);
  useEffect(()=>{
    const cancel=()=>{setFeedback('已取消选择，当前照片保持不变。');setInvalid(false);};
    const inputs=[camera.current,album.current];
    inputs.forEach(input=>input?.addEventListener('cancel',cancel));
    return ()=>inputs.forEach(input=>input?.removeEventListener('cancel',cancel));
  });
  useEffect(()=>{
    function warn(event){if(photos.length&&!saved){event.preventDefault();event.returnValue='';}}
    function navigate(event){if(saving){event.preventDefault();return;}if(photos.length&&!saved&&!window.confirm('照片尚未保存，离开后需要重新选择。确认离开？'))event.preventDefault();}
    window.addEventListener('beforeunload',warn);
    window.addEventListener('document:navigate',navigate);
    return ()=>{window.removeEventListener('beforeunload',warn);window.removeEventListener('document:navigate',navigate);};
  },[photos.length,saved,saving]);
  function update(key,value){setFields(current=>({...current,[key]:value}));setConfirmed(false);}
  function updateLab(id,key,value){setLabs(current=>current.map(item=>item.id===id?{...item,[key]:value}:item));setConfirmed(false);}
  async function selectFiles(event) {
    const files=[...event.target.files];event.target.value='';
    if(chooseLock.current)return;
    setFeedback('');setInvalid(false);
    if(!files.length){setFeedback('已取消选择，当前照片保持不变。');return;}
    if(photos.length+files.length>10){setFeedback('每份资料最多保留10张照片，请分次整理。');setInvalid(true);return;}
    const invalidFile=files.find(file=>!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size===0||file.size>20*1024*1024);
    if(invalidFile){setFeedback(`${invalidFile.name} 无法添加，请使用20 MB以内的 JPEG、PNG 或 WebP 图片。`);setInvalid(true);return;}
    chooseLock.current=true;setReading(true);
    const decoded=await Promise.all(files.map(file=>new Promise(resolve=>{
      const image=new window.Image();const url=URL.createObjectURL(file);
      image.onload=()=>{URL.revokeObjectURL(url);resolve(image.naturalWidth*image.naturalHeight<=40000000?{id:crypto.randomUUID(),name:file.name,file,width:image.naturalWidth,height:image.naturalHeight}:null);};
      image.onerror=()=>{URL.revokeObjectURL(url);resolve(null);};
      image.src=url;
    })));
    chooseLock.current=false;setReading(false);
    if(decoded.some(item=>!item)){setFeedback('图片无法读取或超过4000万像素，请重新拍摄或选择可打开的图片。');setInvalid(true);return;}
    setPhotos(current=>[...current,...decoded]);setConfirmed(false);setStep('photos');setFeedback(`已添加${files.length}张照片，请确认文字清晰、页面完整。`);
  }
  function move(index,delta){setPhotos(current=>{const next=[...current];[next[index],next[index+delta]]=[next[index+delta],next[index]];return next;});setConfirmed(false);}
  function save(event){
    event.preventDefault();
    if(!photos.length||!confirmed||saving||!ready)return;
    if(labs.some(item=>!item.name.trim()||!item.value.trim()||!item.unit.trim())){setFeedback('请补充指标名称、原始数值和单位，或移除尚未填写的项目。');setInvalid(true);return;}
    if(fields.startedAt&&fields.endedAt&&fields.endedAt<fields.startedAt){setFeedback('使用结束时间不能早于开始时间。');setInvalid(true);return;}
    if(fields.occurredAt&&fields.resultAt&&fields.resultAt<fields.occurredAt){setFeedback('报告出具时间不能早于采样时间。');setInvalid(true);return;}
    setSaving(true);setFeedback('');
    saveDocument({id:crypto.randomUUID(),kind,title:therapy?(fields.medicine||'治疗原图资料'):'检验报告',photos,labs,...fields,savedAt:new Date().toISOString()},document=>{setSaved(document);setSaving(false);setStep('saved');});
  }
  const picker=<><input ref={camera} className="file-input" aria-label="拍照文件" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={selectFiles}/><input ref={album} className="file-input" aria-label="相册文件" type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={selectFiles}/></>;
  const actions=<div className="document-pickers"><button className="action" disabled={reading||saving} onClick={()=>{setStep('capture');setFeedback('');}}><Camera size={20}/>拍照添加</button><button className="action secondary" disabled={reading||saving} onClick={()=>album.current.click()}><Images size={20}/>从相册选择</button></div>;
  return <>{picker}<div className="patient-context"><span className="patient-dot">安</span><strong>安安</strong><span>男童 · 2岁4个月</span><span className="badge">母亲记录</span></div>
    {step!=='overview'&&step!=='saved'&&<ol className="document-steps" aria-label="上传步骤">{['添加照片','核对原图','确认内容'].map((label,index)=><li key={label} aria-current={(step==='capture'?0:step==='photos'?1:2)===index?'step':undefined}><span>{index+1}</span>{label}</li>)}</ol>}
    {(feedback||storageError)&&<p className={`upload-feedback ${invalid||storageError?'error':''}`} role={invalid||storageError?'alert':'status'}>{storageError||feedback}</p>}
    {reading&&<p role="status" className="upload-feedback">正在读取原始照片…</p>}
    {step==='overview'&&<>{actions}{photos.length>0&&<button className="action secondary" onClick={()=>setStep('photos')}>继续整理 {photos.length} 张未保存照片</button>}<p className="document-note">{therapy?'拍摄治疗清单、处方或药品包装，保留名称与使用说明。':'拍摄完整报告单，保留项目、数值、单位和参考范围。'}</p><LocalDocuments kind={kind} onOpen={openRecord}/>{therapy?<section className="card therapy-overview"><div className="document-section-title"><h2>治疗记录</h2><span className="badge">已结束</span></div><h3>{scenario.records[2].description.split(' · ')[0]}</h3><p>09/26 18:00 — 09/27 08:00</p><button className="text-link" onClick={()=>openRecord('therapy')}>查看使用记录<ChevronRight size={16}/></button><p className="document-note">该条记录尚未附原图。</p></section>:<><section className="card report-overview"><div className="document-section-title"><h2>最近检验</h2><span>09/27</span></div><p>采样 10:20 · 3项已登记</p><button className="text-link" onClick={()=>openRecord('report')}>查看报告记录<ChevronRight size={16}/></button><p className="document-note">该份记录尚未附原图。</p></section><PriorityResults labs={scenario.labs}/><details className="disclosure"><summary>查看待补充项目 · 7组<Plus size={16}/></summary><ul className="missing-labs">{scenario.missingLabs.map(name=><li key={name}>{name}<span>未提供</span></li>)}</ul></details></>}</>}
    {step==='capture'&&<section className="card capture-guide"><ScanLine size={40}/><h2>{therapy?'拍清治疗资料':'拍清整张报告'}</h2><ol><li>展平纸张，保留四角和全部文字。</li><li>保持光线均匀，确认图片没有反光和模糊。</li><li>{therapy?'药品包装需包含药名与规格；处方保留使用说明。':'保留患儿信息、采样时间、单位与参考范围。'}</li><li>多页资料可以连续添加，之后核对顺序。</li></ol><button className="action" onClick={()=>camera.current.click()} disabled={reading}><Camera size={20}/>打开系统相机</button><button className="action secondary" onClick={()=>album.current.click()} disabled={reading}>从相册选择</button><p className="document-note">相机无法打开时，可在系统设置中检查权限，或使用相册中的照片。</p><button className="text-link" onClick={()=>setStep(photos.length?'photos':'overview')}>返回当前资料</button></section>}
    {step==='photos'&&<><section className="card field-card"><div className="document-section-title"><h2>核对原始照片</h2><span>{photos.length} / 10 张</span></div><OriginalGallery photos={photos} editable onRemove={setRemoveId} onMove={move}/>{photos.length===0&&<p className="document-note">当前没有照片，请继续添加。</p>}<p className="document-note">点击缩略图查看原图；文字模糊时请移除后重新拍摄。</p></section>{actions}<button className="action" disabled={!photos.length||reading} onClick={()=>{setStep('confirm');setFeedback('');}}>下一步，确认内容<ChevronRight size={18}/></button><button className="text-link centered" onClick={()=>setStep('overview')}>返回资料列表</button></>}
    {step==='confirm'&&<form className="entry-form document-confirm" onSubmit={save}><section className="card field-card"><div className="document-section-title"><h2>原始照片</h2><button type="button" className="text-link" onClick={()=>setStep(photos.length?'photos':'capture')}>{photos.length?'管理照片':'添加照片'}</button></div>{photos.length>0&&<OriginalGallery photos={photos} compact/>}<p className="document-note">对照原图填写，尚未登记的内容保持空白。</p></section>
      {therapy?<section className="card field-card"><h2>治疗信息</h2><TextField label="原药名或治疗名称" value={fields.medicine} onChange={value=>update('medicine',value)}/><label className="form-field"><span>实际使用状态</span><select value={fields.usage} onChange={event=>update('usage',event.target.value)}>{['无法确定','持续使用','已结束','尚未使用'].map(item=><option key={item}>{item}</option>)}</select></label><details className="disclosure"><summary>使用时间与补充说明<Plus size={16}/></summary><TextField label="使用开始时间" type="datetime-local" value={fields.startedAt} onChange={value=>update('startedAt',value)}/><TextField label="使用结束时间" type="datetime-local" value={fields.endedAt} onChange={value=>update('endedAt',value)}/><TextField label="补充说明" value={fields.note} onChange={value=>update('note',value)}/></details></section>:<section className="card field-card"><div className="document-section-title"><h2>需要关注的指标</h2><span>{labs.length} 项</span></div><p className="document-note">优先登记报告标记异常的结果和 sJIA 关注项目。</p>{labs.map((item,index)=><fieldset className="lab-editor" key={item.id}><legend>指标 {index+1}</legend><label className="form-field"><span>项目</span><select value={choices.includes(item.name)?item.name:'其他项目'} onChange={event=>updateLab(item.id,'name',event.target.value==='其他项目'?'':event.target.value)}>{choices.map(name=><option key={name}>{name}</option>)}</select></label>{!choices.includes(item.name)&&<TextField label="报告项目名称" value={item.name} onChange={value=>updateLab(item.id,'name',value)}/>}<div className="lab-value-fields"><TextField label="原始数值" value={item.value} onChange={value=>updateLab(item.id,'value',value)} required/><TextField label="原始单位" value={item.unit} onChange={value=>updateLab(item.id,'unit',value)} required/></div><label className="form-field"><span>报告异常标记</span><select value={item.flag} onChange={event=>updateLab(item.id,'flag',event.target.value)}>{['未确认','无异常标记','升高','降低','异常'].map(value=><option key={value}>{value}</option>)}</select></label><details><summary>参考范围</summary><TextField label="报告参考范围" value={item.reference} onChange={value=>updateLab(item.id,'reference',value)}/></details><button type="button" className="text-link" onClick={()=>{setLabs(current=>current.filter(lab=>lab.id!==item.id));setConfirmed(false);}}>移除这项指标</button></fieldset>)}<button type="button" className="action secondary" onClick={()=>{setLabs(current=>[...current,{id:crypto.randomUUID(),name:'铁蛋白',value:'',unit:'',reference:'',flag:'未确认'}]);setConfirmed(false);}}><Plus size={18}/>添加报告指标</button></section>}
      <details className="disclosure"><summary>时间与报告来源<Plus size={16}/></summary><div className="disclosure-content"><TextField label={therapy?'资料发生时间':'采样时间'} type="datetime-local" value={fields.occurredAt} onChange={value=>update('occurredAt',value)}/><TextField label="报告出具时间" type="datetime-local" value={fields.resultAt} onChange={value=>update('resultAt',value)}/><TextField label="报告机构" value={fields.organization} onChange={value=>update('organization',value)}/></div></details><label className="check-field"><input type="checkbox" checked={confirmed} onChange={event=>setConfirmed(event.target.checked)}/><span>已核对照片属于安安，填写内容与原图一致</span></label><button type="submit" className="action" disabled={!photos.length||!confirmed||!ready||saving}>{saving?'正在保存…':'保存原图与记录'}</button><p className="document-note">保存在当前设备；本人确认与专业核对分别记录。</p></form>}
    {step==='saved'&&saved&&<><section className="saved-result"><span><Check size={28}/></span><h2>原图与记录已保存</h2><p>{saved.photos.length} 张原图已保存在当前设备，可随时打开查看。</p><button className="action" onClick={()=>openRecord(saved.id)}>查看记录</button></section><OriginalGallery photos={saved.photos}/><button className="action secondary" onClick={()=>{setPhotos([]);setSaved(null);setLabs([]);setConfirmed(false);setFields({medicine:'',usage:'无法确定',startedAt:'',endedAt:'',note:'',occurredAt:'',resultAt:'',organization:''});setStep('overview');}}>返回资料列表</button></>}
    <dialog ref={removeDialog} className="remove-photo-dialog" aria-label="移除照片" onCancel={()=>setRemoveId(null)}><h2>移除这张照片？</h2><p>请确认其他照片仍包含需要保留的原始内容。</p><button className="action secondary" onClick={()=>setRemoveId(null)}>保留照片</button><button className="action" onClick={()=>{setPhotos(current=>current.filter(photo=>photo.id!==removeId));setConfirmed(false);setRemoveId(null);setFeedback('照片已移除。');}}>确认移除</button></dialog>
  </>;
}
