export const alertViews = {
  pending: {title:'等待评估',label:'等待资料处理',tone:'slate',icon:'clock',body:'本次记录已经提交，评估尚未开始。',action:'查看已提交资料',result:false},
  evaluating: {title:'正在评估',label:'处理中',tone:'blue',icon:'loading',body:'正在处理本次资料，完成后更新提示。',action:'查看评估进度',result:false},
  incomplete: {title:'需要补充资料',label:'资料不完整',tone:'amber',icon:'info',body:'部分资料缺少采样时间，请核对报告原文。',action:'补充采样时间',result:false},
  clear: {title:'当前没有有效预警',label:'当前提示',tone:'teal',icon:'shield',body:'继续查看和记录孩子的情况。此状态不代表健康结论。',action:'查看评估记录',result:true},
  attention: {title:'铁蛋白历次结果持续上升',label:'变化关注',tone:'amber',icon:'info',body:'09/20 → 09/27：310 → 386 ng/mL。四次报告转录均待专业核对，变化方向不代表 MAS 结论。',action:'查看全部依据',result:true},
  warning: {title:'发热伴皮疹、关节不适',label:'重要提示',tone:'orange',icon:'alert',body:'最近体温 38.2 °C；监护人记录皮疹、活动时关节不适。关节炎尚未经临床确认。',action:'查看全部依据',result:true},
  urgent: {title:'呼吸困难或难以唤醒需急诊',label:'紧急提醒',tone:'red',icon:'alert',body:'若孩子出现呼吸困难、嗜睡且难以唤醒等表现，请立即寻求急救帮助。',action:'查看紧急提示',result:true},
  expired: {title:'本次提示已失效',label:'资料已更新',tone:'slate',icon:'history',body:'依据记录发生变化，当前需要重新评估。',action:'查看更新记录',result:false},
  failed: {title:'暂时无法更新预警',label:'连接异常',tone:'slate',icon:'offline',body:'本次读取未完成，请重新加载当前提示。',action:'重新加载',result:false},
  disabled: {title:'预警服务未启用',label:'服务状态',tone:'slate',icon:'shield',body:'可以继续记录资料，当前没有可用的评估结果。',action:'查看服务说明',result:false},
};

export const careGuidance = {
  pending:{decision:'尚无就医评估结果',instruction:'本次资料正在等待处理，请按已有医生安排就诊。',evidence:'3项报告待核对；症状为家庭记录。'},
  evaluating:{decision:'尚无就医评估结果',instruction:'评估完成前，继续遵循已有医生安排。',evidence:'正在核查资料完整性、适用前提与来源。'},
  incomplete:{decision:'数据不足，无法判断就医紧迫性',instruction:'补充已有报告及采样时间，不能据此认定无需就医。',evidence:'缺少甘油三酯、纤维蛋白原及 LDH 等资料。'},
  clear:{decision:'没有新增就医安排',instruction:'继续遵循原有随访计划；当前无有效预警不代表无需就医。',evidence:'当前事件列表为空；不据此排除疾病。'},
  attention:{decision:'需要联系医生核对变化',instruction:'整理历次报告，联系随访医生确认是否需要就诊及时间。',evidence:'铁蛋白 310 → 386 ng/mL · 09/20—09/27 · 待核对。'},
  warning:{decision:'需要联系医生评估就诊安排',instruction:'请及时联系随访医生，说明发热、皮疹和关节不适，并携带记录摘要。',evidence:'09/27 19:40 · 家庭观察 · 尚未经临床核对。'},
  urgent:{decision:'出现上述表现时，立即急诊就医',instruction:'请立即寻求急救帮助，不等待平台回复或补齐报告。',evidence:'通用急症提示；出现所列症状时立即行动。'},
  expired:{decision:'原就医提示已失效',instruction:'当前安排需要重新确认，不能继续使用历史提示判断。',evidence:'引用记录的版本发生变化。'},
  failed:{decision:'当前无法取得就医提示',instruction:'已有就诊安排继续保留；出现明显不适时联系医生。',evidence:'读取失败不代表风险降低。'},
  disabled:{decision:'平台尚未提供就医判断',instruction:'请按已有医生安排就诊，家庭记录可以继续填写。',evidence:'临床规则、家庭文案与联系渠道尚未启用。'},
};

export const transitionEvents = {
  pending: ['START'], evaluating: ['PUBLISH_CLEAR','PUBLISH_ATTENTION','PUBLISH_WARNING','PUBLISH_URGENT','NEEDS_DATA','FAIL'],
  incomplete: ['SUBMIT'], clear: ['SUBMIT','INVALIDATE'], attention: ['READ','SUBMIT','INVALIDATE'],
  warning: ['READ','SUBMIT','INVALIDATE'], urgent: ['READ','SUBMIT','INVALIDATE'],
  expired: ['SUBMIT'], failed: ['RETRY'], disabled: ['ENABLE'],
};
export const eventLabels = {START:'开始评估',PUBLISH_CLEAR:'收到无事件结果',PUBLISH_ATTENTION:'收到关注提示',PUBLISH_WARNING:'收到重要预警',PUBLISH_URGENT:'收到紧急预警',NEEDS_DATA:'收到缺项结果',FAIL:'读取失败',SUBMIT:'提交更新资料',INVALIDATE:'依据失效',READ:'查看提示',RETRY:'重新加载',ENABLE:'服务启用'};
export function makeAlert(status='warning') {
  if (!alertViews[status]) throw new Error('预警设计状态不存在');
  return {status,read:false,history:[{event:'OPEN_DESIGN',to:status}]};
}
export function transitionAlert(state,event) {
  if (!transitionEvents[state.status].includes(event)) throw new Error(`当前状态不允许事件：${state.status} / ${event}`);
  if (event === 'READ') return {...state,read:true,history:[...state.history,{event,to:state.status}]};
  const destinations={START:'evaluating',PUBLISH_CLEAR:'clear',PUBLISH_ATTENTION:'attention',PUBLISH_WARNING:'warning',PUBLISH_URGENT:'urgent',NEEDS_DATA:'incomplete',FAIL:'failed',SUBMIT:'pending',INVALIDATE:'expired',RETRY:'evaluating',ENABLE:'pending'};
  return {status:destinations[event],read:false,history:[...state.history,{event,to:destinations[event]}]};
}
