import type { useCorrectionSubmission } from '../state/useCorrectionSubmission';
export function CorrectionRecovery({ submission }: { submission: ReturnType<typeof useCorrectionSubmission> }) {
  if (!submission.hasIdentity && !submission.error) return null;
  return <section className="status-panel" role="status"><h3>{submission.phase === 'saved' ? '更正申请处理结果已核验' : submission.phase === 'pending' ? '正在核验原提交' : '原提交结果需要核验'}</h3>
    {submission.error && <p>{submission.error}</p>}
    <p>查询与继续提交沿用同一请求身份；结果未知时不能另建提交。</p>
    <button className="text-action" disabled={submission.phase === 'pending'} onClick={() => void submission.recover()}>查询原请求</button>
    {submission.phase === 'unknown' && !submission.request?.may_retry && <button className="text-action" onClick={() => void submission.recover('continue')}>继续原提交</button>}
    {submission.notFound && <button className="text-action" onClick={submission.resetUnregistered}>原请求未登记，返回编辑</button>}
    {submission.request?.may_retry && <button className="text-action" disabled={submission.phase === 'pending'} onClick={() => void submission.recover('retry')}>按新一代意图重试原请求</button>}
  </section>;
}
