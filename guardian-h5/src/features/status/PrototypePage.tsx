import { Activity, Bell, CalendarDays, ClipboardList, FileClock, FileText as FileHeart, FileText, History, House, ClipboardList as ListOrdered, LockKeyhole, FileText as PencilLine, Pill, ShieldCheck, UserRound, UsersRound } from '../../components/ThreeDIcon';
import { PageFrame, LinkGroup } from '../../components/PageFrame';
import { Disclosure, Section } from '../../components/ClinicalUI';

export function PrototypePage() {
  return <PageFrame screenId="S00" title="原型页面目录" subtitle="iOS Native Style · 监护人手机端" backTo="/home">
    <p className="catalogue-note">全部页面使用当前未登录、未接入资料的状态。详情入口用于查看受限页面结构，不指向真实病例。</p>
    <h2 className="section-heading">开始与登记</h2>
    <LinkGroup label="开始与登记" links={[
      { to: '/home', title: '首页', description: 'S03 · 预警结果优先与资料指引', icon: House },
      { to: '/login', title: '登录', description: 'S01 · 身份服务状态与访问步骤', icon: LockKeyhole },
      { to: '/associations', title: '患儿关联', description: 'S02 · 本人关联与状态说明', icon: UsersRound },
      { to: '/associations/new', title: '关联申请', description: 'S02 · 申请流程与资料字段', icon: UsersRound },
      { to: '/associations/unavailable', title: '关联详情', description: 'S02 · 未取得身份的访问状态', icon: ShieldCheck },
      { to: '/register/observations', title: '体温与症状', description: 'S04 · 测量、观察和时间精度', icon: Activity },
      { to: '/register/reports', title: '拍照添加报告', description: 'S05 · 拍照与确认保存', icon: FileText },
      { to: '/register/therapies', title: '治疗资料', description: 'S06 · 原药名、状态与起止时间', icon: Pill },
    ]} />
    <h2 className="section-heading">记录与就医准备</h2>
    <LinkGroup label="记录与就医准备" links={[
      { to: '/records', title: '记录时间轴', description: 'S07 · 类别、来源、状态与日期筛选', icon: CalendarDays },
      { to: '/records/unavailable', title: '记录详情', description: 'S08 · 原始内容、来源与版本结构', icon: FileText },
      { to: '/records/analytes/unavailable', title: '历次检验', description: 'S09 · 原值与可比较条件', icon: ListOrdered },
      { to: '/records/unavailable/correct', title: '更正记录', description: 'S10 · 版本、原因与编辑范围', icon: PencilLine },
      { to: '/records/unavailable/history', title: '更正历史', description: 'S10 · 版本追溯结构', icon: History },
      { to: '/summaries', title: '就医摘要', description: 'S12 · 选择日期并生成摘要', icon: FileHeart },
      { to: '/summaries/unavailable', title: '摘要详情', description: 'S12 · 有效性、时间和资料结构', icon: ClipboardList },
    ]} />
    <h2 className="section-heading">提示与本人资料</h2>
    <LinkGroup label="提示与本人资料" links={[
      { to: '/alerts', title: '预警', description: 'S11 · 结果、依据与下一步指引', icon: Bell },
      { to: '/alerts/unavailable', title: '预警详情', description: 'S11 · 当前不可读取状态', icon: Bell },
      { to: '/account', title: '我的', description: 'S13 · 账号、关联和资料入口', icon: UserRound },
      { to: '/account/data-use', title: '资料使用说明', description: 'S13 · 访问、来源、草稿与保存说明', icon: ShieldCheck },
      { to: '/drafts', title: '我的草稿', description: 'S14 · 分类与恢复条件', icon: FileClock },
      { to: '/access-denied', title: '访问受限', description: 'S15 · 权限状态与返回入口', icon: LockKeyhole },
      { to: '/service-unavailable', title: '服务不可用', description: 'S15 · 服务状态与可用入口', icon: LockKeyhole },
      { to: '/unavailable-page', title: '页面不存在', description: 'S15 · 无效地址与返回首页', icon: FileText },
    ]} />
    <Section title="可操作的原型交互"><p className="section-copy">导航、返回、表单字段展开、空白检验项目增减、时间精度选择、记录筛选、详情内容切换和摘要日期校验可以操作。身份核验、病例录入、草稿保存、提交、更正及摘要生成等待真实服务。</p><Disclosure title="需要真实服务验证的状态"><p>加载、真实空记录、记录读取失败、保存结果待确认、草稿恢复、版本冲突、关联撤销与摘要失效，均需真实请求及获准资料验证。</p><p>当前没有模拟病例、计算结果、接口响应或临床提示。</p></Disclosure></Section>
  </PageFrame>;
}
