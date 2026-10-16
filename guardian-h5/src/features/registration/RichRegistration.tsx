import { ChevronRight } from '../../components/ThreeDIcon';

/** Presentation only: identity and navigation are supplied by the guarded page. */
export function RegistrationPatient({ displayId, onClick }: { displayId?: string; onClick: () => void }) {
  return <button type="button" className="registration-patient" onClick={onClick}>
    <span className="registration-avatar" aria-hidden="true">{displayId ? Array.from(displayId)[0] : '档'}</span>
    <span className="registration-patient-copy"><strong>{displayId ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span>
    <span className="badge">家庭记录</span><ChevronRight size={16} />
  </button>;
}

export function RegistrationSteps({ current }: { current: 1 | 2 | 3 }) {
  return <ol className="registration-steps" aria-label="资料登记进度">
    {['添加照片', '核对原图', '确认内容'].map((label, index) => <li key={label} aria-current={index + 1 === current ? 'step' : undefined}>
      <span>{index + 1}</span>{label}
    </li>)}
  </ol>;
}
