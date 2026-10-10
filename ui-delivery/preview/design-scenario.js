// 本文件仅描述高保真画板内容，不连接业务服务。
export const scenario = {
  kind:'DESIGN_EXAMPLE',caption:'设计示例',
  child:{name:'安安',age:'2岁4个月',gender:'男童',relation:'母亲',identifier:'设计档案 A01',diagnosis:'sJIA 状态待临床资料确认'},
  guardian:{name:'安安妈妈',status:'已登录',role:'主要监护人'},
  temperature:[{time:'09/26 08:00',value:'36.9'},{time:'09/26 19:30',value:'37.4'},{time:'09/27 08:10',value:'37.8'},{time:'09/27 19:40',value:'38.2'}],
  labs:[{name:'铁蛋白',value:'386',unit:'ng/mL',method:'化学发光法'},{name:'血小板计数',value:'178',unit:'×10⁹/L',method:'仪器计数法'},{name:'天门冬氨酸氨基转移酶',value:'34',unit:'U/L',method:'速率法'}],
  missingLabs:['甘油三酯','纤维蛋白原','LDH','血常规及分类（除血小板）','其他肝功能项目','D-二聚体','CRP'],
  report:{identifier:'设计报告 R01',sampledAt:'09/27 10:20',resultAt:'09/27 12:05',recordedAt:'09/27 16:30',source:'监护人报告转录',verification:'待专业核对'},
  symptoms:[
    {id:'fever',name:'发热',value:'38.2 °C',status:'监护人记录',time:'09/27 19:40',detail:'腋下测量；本次记录发热，发生时间按监护人填写保留。',location:'全身感受 · 头部为示意',observed:'记录发热'},
    {id:'rash',name:'皮疹',value:'观察到皮疹',status:'监护人自报',time:'09/27 19:20',detail:'本次观察到皮疹，范围与持续时间由监护人补充。',location:'发生部位未填写 · 前臂为示意',observed:'观察到'},
    {id:'arthritis',name:'关节炎',value:'关节不适待核对',status:'关节炎尚未确认',time:'09/27 18:30',detail:'监护人记录活动时关节不适。关节炎及活动性由临床资料确认。',location:'发生部位未填写 · 膝部为示意',observed:'活动不适'},
  ],
  records:[
    {id:'temperature',type:'体温症状',title:'体温与症状记录',value:'38.2',unit:'°C',time:'09/27 19:40',source:'监护人记录',status:'本人填写',description:'腋下测量 · 皮疹 · 关节不适'},
    {id:'report',type:'检验报告',title:'检验报告',value:'3',unit:'项结果',time:'09/27 10:20',source:'报告转录',status:'待专业核对',description:'铁蛋白、血小板计数、AST'},
    {id:'therapy',type:'治疗资料',title:'治疗资料记录',value:'已结束',unit:'',time:'09/27 08:00',source:'监护人记录',status:'本人确认',description:'布洛芬混悬液 · 查看使用时间'},
    {id:'previous',type:'体温症状',title:'体温记录',value:'37.4',unit:'°C',time:'09/26 19:30',source:'监护人记录',status:'本人填写',description:'腋下测量 · 未观察到皮疹'},
  ],
  labHistory:[{date:'09/20',value:'310'},{date:'09/23',value:'342'},{date:'09/25',value:'368'},{date:'09/27',value:'386'}],
};
