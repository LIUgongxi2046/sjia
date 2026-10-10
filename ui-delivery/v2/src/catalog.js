export const screens = [
  ['home','首页','家庭首页','FR-01 FR-02 FR-06 FR-07 FR-12','/home','当前提示优先，拍照记录紧随其后。'],
  ['observations','体温与症状','资料登记','FR-02','/register/observations','语音、体温与观察状态在同一任务中确认。'],
  ['reports','拍照添加报告','资料登记','FR-02 FR-13','/register/reports','原件、识别和本人确认分别呈现。'],
  ['therapies','治疗资料','资料登记','FR-02','/register/therapies','原药名、实际使用状态和时间分别确认。'],
  ['records','记录','家庭档案','FR-03 FR-08 FR-11','/records','时间轴、档案、摘要和草稿各有明确入口。'],
  ['detail','记录详情','家庭档案','FR-02 FR-03 FR-08','/records/:recordId','原值、医学时间、来源与核对状态共同展示。'],
  ['analyte','历次检验','家庭档案','FR-03 FR-19','/records/analytes/:analyteId','只有可比较的独立采样显示变化。'],
  ['correction','更正记录','家庭档案','FR-08','/records/:recordId/correct','更正原因与版本检查是必要操作。'],
  ['history','更正历史','家庭档案','FR-08','/records/:recordId/history','保留原始版本及更正关系。'],
  ['version','历史版本','家庭档案','FR-08 FR-09','/records/versions/:versionId','历史内容只读，继续执行当前权限。'],
  ['alerts','预警记录','预警与就医','FR-06 FR-07 FR-12','/alerts','明确当前有效性与查看状态。'],
  ['alert-detail','提示详情','预警与就医','FR-06 FR-07 FR-12','/alerts/:alertId','批准文字、依据、时效与联系入口。'],
  ['summaries','准备就医摘要','预警与就医','FR-11 FR-12','/summaries','按照患儿与时间范围选择当前可见资料。'],
  ['summary-detail','就医摘要','预警与就医','FR-11 FR-12','/summaries/:summaryId','原始事实和来源适合手机当面阅读。'],
  ['account','我的','账号与权限','FR-01 FR-09 FR-22','/account','账号、患儿与提醒管理。'],
  ['data-use','资料使用说明','账号与权限','FR-09 FR-21','/account/data-use','用途、保存、删除与外部处理各自说明。'],
  ['login','登录','账号与权限','FR-01 FR-09','/login','明确身份方式尚需配置，不能伪造登录成功。'],
  ['associations','患儿管理','账号与权限','FR-01','/associations','按本人有效关系管理患儿。'],
  ['association-new','申请关联','账号与权限','FR-01 FR-09','/associations/new','证明和关联范围由批准流程确定。'],
  ['association-detail','关联详情','账号与权限','FR-01 FR-09','/associations/:relationId','本人权限、核验与撤销结果分别呈现。'],
  ['drafts','我的草稿','家庭档案','FR-02 FR-08 FR-09','/drafts','只恢复本人服务端已确认保存的草稿。'],
  ['access','访问受限','系统状态','FR-01 FR-09','/access-denied','拒绝访问后不显示患儿内容。'],
  ['service','服务暂不可用','系统状态','FR-01 FR-02 FR-03 FR-07 FR-11','/service-unavailable','读取失败不表示没有资料。'],
  ['missing','页面不存在','系统状态','FR-09','*','提供准确的返回入口。'],
  ['archive','长期档案','资料整理 2.0','FR-14 FR-15 FR-17','/archives','医院、就诊、文书与确认文本共同查找。'],
  ['batch','批次进度','资料整理 2.0','FR-14','/archives/batches/:batchId','逐页保存进度，失败和完成内容分别呈现。'],
  ['confirm','逐项确认','资料整理 2.0','FR-13 FR-15','/archives/confirm','逐项医学时间、语境与出处不能被共同时间覆盖。'],
  ['grouping','文书与就诊归组','资料整理 2.0','FR-15','/archives/grouping','一份文书可以包含多次就诊。'],
  ['duplicates','重复资料核对','资料整理 2.0','FR-16','/archives/duplicates','同一文件、同次采样和独立检查分别判断。'],
  ['delta','这次新增了什么','资料整理 2.0','FR-18','/archives/changes','明确比较基准，逐条变化可以查看来源。'],
  ['period','观察时段','资料整理 2.0','FR-19','/records/period','实际采样间隔与资料时效明确表达。'],
  ['reminders','资料补充提醒','资料整理 2.0','FR-22','/account/reminders','本人自主启用，可暂停、延后和关闭。'],
  ['original','原始资料','资料整理 2.0','FR-13 FR-21','/archives/source','显示来源版本、用途与文书页面。'],
  ['verify','专业资料核对','受控支持','FR-10 FR-13 FR-16','/support/verification','按已分配患儿、字段及版本核对。'],
  ['assessment','评估依据','受控支持','FR-04 FR-05 FR-06 FR-19','/support/assessment','静态和动态独立，未知保持未知。'],
  ['publication','内容与启用','受控支持','FR-12 FR-06','/support/publication','保存、批准、验证与启用是不同动作。'],
  ['dataset','资料与评价集合','受控支持','FR-20','/support/datasets','文件、患儿、事件和观察人时分别统计。'],
  ['privacy','用途与副本','受控支持','FR-09 FR-21','/support/data-use','必要副本处理及人工复核在外发前完成。'],
  ['delivery','通知记录','受控支持','FR-07 FR-22','/support/deliveries','已受理、已送达和已查看分别记录。'],
].map((row,index)=>({key:row[0],name:row[1],group:row[2],requirements:row[3],route:row[4],note:row[5],id:`UI2-${String(index+1).padStart(2,'0')}`}));

export const reviewStates = [
  ['default','页面默认',''],
  ['loading','加载','正在读取当前资料'],
  ['empty','空内容','尚无符合条件的资料'],
  ['error','读取失败','暂时无法取得资料'],
  ['offline','网络中断','网络连接已中断'],
  ['permission','权限失效','当前访问关系需要核验'],
  ['partial','资料不足','部分内容需要补充'],
  ['stale','版本失效','依据已有更新'],
  ['pending','提交待确认','保存结果待确认'],
  ['success','完成反馈','记录已保存'],
  ['attention','关注提示','关注提示的内容区域'],
  ['warning','重要提示','重要提示的内容区域'],
  ['urgent','紧急提示','紧急提示的内容区域'],
];

export const groups = [...new Set(screens.map(screen=>screen.group))];
export const formScreens = ['observations','reports','therapies','correction','association-new','confirm','grouping','duplicates','reminders','period','summaries','verify','publication','privacy'];
export function statesFor(key) {
  if (['access','service','missing'].includes(key)) return ['default'];
  const states = ['default','loading','empty','error','offline','permission','partial','stale'];
  if (formScreens.includes(key)) states.push('pending','success');
  if (['home','alerts','alert-detail'].includes(key)) states.push('attention','warning','urgent');
  return states;
}
