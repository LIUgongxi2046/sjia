import { useNavigate } from 'react-router';
import { ArrowRight } from 'lucide-react';
import { CircleHelp, LockKeyhole, WifiOff } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';

const states = {
  access: { id: 'UI2-22', name: '访问受限', title: '暂时无法访问', text: '当前关联或操作权限需要重新核验。',
    action: '查看患儿关联', path: '/associations', note: '拒绝访问后不显示患儿内容。', Icon: LockKeyhole },
  service: { id: 'UI2-23', name: '服务暂不可用', title: '暂时无法取得内容', text: '服务恢复后重新读取，不能将本次失败视为没有资料。',
    action: '返回首页', path: '/home', note: '读取失败不表示没有资料。', Icon: WifiOff },
  missing: { id: 'UI2-24', name: '页面不存在', title: '这个页面暂不可用', text: '链接可能不完整，可以返回当前可用入口。',
    action: '返回首页', path: '/home', note: '提供准确的返回入口。', Icon: CircleHelp },
};

export function UI2StatusPage({ kind }: { kind: keyof typeof states }) {
  const navigate = useNavigate();
  const state = states[kind];
  const { Icon } = state;
  return <UI2FamilyFrame title={state.name} screenId={state.id} note={state.note} backTo="/home">
    <section className="card"><div className="empty"><span className="empty-icon"><Icon size={54} /></span>
      <h3>{state.title}</h3><p>{state.text}</p><button className="text-action" onClick={() => navigate(state.path)}>{state.action}<ArrowRight size={16} /></button>
    </div></section>
  </UI2FamilyFrame>;
}
