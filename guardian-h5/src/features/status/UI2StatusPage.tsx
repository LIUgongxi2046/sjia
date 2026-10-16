import { useNavigate } from 'react-router';
import { ChevronRight, FileText, House, LockKeyhole, UserRound, WifiOff } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { useAccess } from '../../state/access';

const states = {
  access: { id: 'UI2-22', name: '访问受限', title: '暂时无法访问此资料', text: '当前关联或操作权限需要重新核验。',
    note: '拒绝访问后不显示患儿内容。', Icon: LockKeyhole },
  service: { id: 'UI2-23', name: '服务暂不可用', title: '暂时无法取得内容', text: '服务恢复后重新读取，不能将本次失败视为没有资料。',
    note: '读取失败不表示没有资料。', Icon: WifiOff },
  missing: { id: 'UI2-24', name: '页面不存在', title: '这个页面暂时无法找到', text: '可以从首页或记录列表继续访问现有资料。',
    note: '提供准确的返回入口。', Icon: FileText },
};

export function UI2StatusPage({ kind }: { kind: keyof typeof states }) {
  const navigate = useNavigate(), access = useAccess();
  const signedIn = !!access.principalId && access.accountStatus === 'active' && !access.loading && !access.error;
  const state = states[kind], { Icon } = state;
  const target = kind === 'access' ? signedIn ? '/associations' : '/login' : kind === 'missing' && signedIn ? '/records' : '/home';
  const action = kind === 'access' ? signedIn ? '查看关联' : '登录并核验身份' : kind === 'missing' && signedIn ? '返回记录' : '返回首页';
  return <UI2FamilyFrame title={state.name} screenId={state.id} note={state.note} backTo="/home">
    <section className="rich-status-detail"><Icon size={42} /><h2>{state.title}</h2><p>{state.text}</p>
      <button className="button" onClick={() => navigate(target)}>{action}</button>
    </section>
    <section className="rich-status-links"><h3>还可以继续</h3><div className="card">
      <button className="list-row" onClick={() => navigate('/home')}><span className="row-icon"><House size={30} /></span>
        <span className="row-text"><strong>返回首页</strong><small>查看当前可访问的患儿资料</small></span><ChevronRight size={19} /></button>
      <button className="list-row" onClick={() => navigate(signedIn ? '/account' : '/login')}><span className="row-icon"><UserRound size={30} /></span>
        <span className="row-text"><strong>{signedIn ? '账号与关联' : '登录与身份'}</strong><small>检查当前身份和访问范围</small></span><ChevronRight size={19} /></button>
    </div></section>
  </UI2FamilyFrame>;
}
