import React, {useState} from 'react';
import {ChevronRight, FileText, Phone, ShieldCheck} from './three-d-icons.jsx';
import {scenario} from './design-scenario.js';
import symptomIllustration from '../assets/master/symptom-trio.png';
import './clinical-content.css';

export function SymptomIllustrations({openSheet,dense=false}) {
  return <section className={`symptom-illustrations ${dense?'symptom-compact':''}`} aria-label="发热、皮疹与关节炎示意">
    <img src={symptomIllustration} alt="发热的体温计、皮疹的皮肤斑点、关节炎的膝部示意"/>
    <div className="symptom-columns">{scenario.symptoms.map((item,index)=><button key={item.id} onClick={()=>openSheet(item.name)} aria-label={`查看${item.name}示意与记录`}><strong><span>{index+1}</span>{item.name}</strong><small>{dense&&item.id==='arthritis'?'关节不适 · 待确认':item.value}</small>{!dense&&<><em>{item.status}</em><ChevronRight size={14}/></>}</button>)}</div>
    {!dense&&<p className="symptom-caption">部位与外观仅作示意，不代表孩子的实际皮损或关节位置。</p>}
  </section>;
}

export function SymptomPins({openSheet}) {
  return scenario.symptoms.map((item,index)=><button key={item.id} className={`symptom-pin ${item.id}`} aria-label={`查看${item.name}身体标记`} onClick={()=>openSheet(item.name)}><span>{index+1}</span></button>);
}

export function SymptomDetail({name,onRecord}) {
  const symptom=scenario.symptoms.find(item=>item.name===name);
  if(!symptom)throw new Error('症状设计内容不存在');
  return <div className="symptom-detail"><span className="badge">{symptom.status}</span><h3>{symptom.value}</h3><p>{symptom.detail}</p><dl><div><dt>发生时间</dt><dd>{symptom.time}</dd></div><div><dt>录入时间</dt><dd>09/27 19:45</dd></div><div><dt>观察部位</dt><dd>{symptom.location}</dd></div><div><dt>资料来源</dt><dd>安安妈妈 · 监护人自报</dd></div><div><dt>专业核对</dt><dd>待核对，未作为评分变量</dd></div></dl><button className="action" onClick={onRecord}>查看症状原始记录</button></div>;
}

export function SymptomFields() {
  const [observations,setObservations]=useState({fever:'记录发热',rash:'观察到',arthritis:'活动不适'});
  return <section className="symptom-form" aria-label="主要症状登记">{scenario.symptoms.map(item=><fieldset key={item.id}><legend>{item.name==='关节炎'?'关节症状':item.name}</legend><label><span>观察结果</span><select value={observations[item.id]} onChange={event=>setObservations({...observations,[item.id]:event.target.value})}>{(item.id==='fever'?['记录发热','未记录发热','无法确定']:item.id==='rash'?['观察到','未观察到','无法确定']:['活动不适','观察到肿胀','活动受限','未观察到不适','无法确定']).map(value=><option key={value}>{value}</option>)}</select></label><label><span>发生时间</span><input type="datetime-local" defaultValue={`2026-09-27T${item.time.slice(6)}`}/></label>{item.id!=='fever'&&<label><span>观察部位</span><input type="text" placeholder="填写观察到的部位"/></label>}{item.id==='arthritis'&&<p>仅记录观察表现。关节炎诊断与活动性需要临床资料确认。</p>}</fieldset>)}</section>;
}

export function SymptomsSummary() {
  return <div className="symptom-summary">{scenario.symptoms.map(item=><div key={item.id}><strong>{item.name}</strong><span>{item.value}</span><small>{item.time} · {item.status}</small></div>)}</div>;
}

export function LabCompleteness({onReport}) {
  return <section className="clinical-panel"><div className="clinical-panel-title"><FileText size={18}/><h3>评估资料完整性</h3></div><p>已录入3项检验，均待专业核对；当前数据不足以进行分类或 MS 评分。</p><div className="lab-present">{scenario.labs.map(item=><div key={item.name}><span>{item.name}</span><strong>{item.value} {item.unit}</strong><small>采样 {scenario.report.sampledAt} · 待核对</small></div>)}</div><details><summary>查看待补充项目 · 7组</summary><ul>{scenario.missingLabs.map(name=><li key={name}><span>{name}</span><em>未提供</em></li>)}</ul><p>分类所需5项检验已录入3项；MS 所需4项检验已录入2项。活动性关节炎、中枢神经系统受累、出血表现尚无完整核对资料。</p></details><div className="assessment-state"><ShieldCheck size={16}/><span>sJIA 状态与发热适用前提待核对<br/>分类结果：数据不足 · MS 评分：数据不足</span></div>{onReport&&<button className="text-link" onClick={onReport}>补充已有检验报告<ChevronRight size={15}/></button>}</section>;
}

export function CareContact({onSummary}) {
  return <section className="clinical-panel care-contact"><div className="clinical-panel-title"><Phone size={18}/><h3>就医联系与准备</h3></div><p>联系随访医生时，说明体温、症状发生时间及变化，携带已有报告与治疗记录。</p><dl><div><dt>随访联系人</dt><dd>尚未配置获准联系人</dd></div><div><dt>联系电话</dt><dd>尚未配置</dd></div><div><dt>信息来源 / 核验时间</dt><dd>待项目方提供</dd></div></dl><button className="action secondary" onClick={onSummary}>查看就医摘要</button></section>;
}
