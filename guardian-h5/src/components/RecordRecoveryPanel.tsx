import { useState } from 'react';
import type { useRecordSubmission } from '../state/useRecordSubmission';

/** Explicitly resume server-held requests after reload; never store health data in browser storage. */
export function RecordRecoveryPanel({ submission, onRecovered }: {
  submission: ReturnType<typeof useRecordSubmission>; onRecovered: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!submission.checking && !submission.recoveryError && !submission.recoveries.length) return null;
  return <section className="status-panel" aria-label="原提交恢复">
    <p>{submission.checking ? '正在核对之前的保存结果…' : submission.recoveryError
      ?? '发现之前尚未确认结果的保存。请恢复原提交，避免重复登记；将使用当时确认的完整内容。'}</p>
    {submission.recoveryError && <button type="button" className="button secondary" onClick={submission.recheck}>重新核对保存结果</button>}
    {submission.recoveries.map(saved => <button type="button" className="button secondary" key={saved.intent_id}
      disabled={busy} onClick={() => {
        setError(null); setBusy(true);
        void submission.recover(saved).then(result => { if (result) onRecovered(); })
          .catch(failure => setError(failure instanceof Error ? failure.message : '原提交仍未确认，请保留本次请求。'))
          .finally(() => setBusy(false));
      }}>{saved.status === 'committed' ? '查看已确认保存的记录' : `核对并重试 ${new Date(saved.prepared_at).toLocaleString()} 的原提交`}</button>)}
    {error && <p role="alert">{error}</p>}
  </section>;
}
