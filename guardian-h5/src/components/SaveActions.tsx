import { Button } from 'antd-mobile';
import { ContextLink } from './ContextLink';

export type SaveState =
  | { status: 'unavailable' }
  | { status: 'ready'; onSubmit: () => void }
  | { status: 'submitting' }
  | { status: 'uncertain'; onCheck: () => void }
  | { status: 'checking' }
  | { status: 'failed'; onRetry: () => void }
  | { status: 'conflict'; onReview: () => void }
  | { status: 'saved'; recordPath: string; confirmedAt: string };

export type DraftState =
  | { status: 'unsaved' }
  | { status: 'saving' }
  | { status: 'failed' }
  | { status: 'saved'; confirmedAt: string };

export function FormActions({ label = '核对并提交', state = { status: 'unavailable' }, draft = { status: 'unsaved' } }: { label?: string; state?: SaveState; draft?: DraftState }) {
  const messages: Record<SaveState['status'], string> = {
    unavailable: '尚未保存', ready: '确认患儿、原值、单位和时间后保存。', submitting: '正在保存，请稍候。',
    uncertain: '尚未确认保存结果，请查询本次提交。', checking: '正在查询本次提交，请稍候。',
    failed: '本次未能保存，当前页面保留输入。', conflict: '记录已有新版本，请核对变化后继续。', saved: '记录已保存。',
  };
  const draftMessage = draft.status === 'saved' ? `草稿保存于 ${draft.confirmedAt}` : draft.status === 'saving' ? '正在保存草稿。' : draft.status === 'failed' ? '草稿尚未保存，离开或刷新可能丢失输入。' : '';
  return <section className="form-actions" aria-label="保存与提交" aria-busy={state.status === 'submitting' || state.status === 'checking'}>
    {state.status === 'saved' ? <ContextLink className="primary-link" to={state.recordPath}>查看已保存记录</ContextLink> :
      <Button block color="primary" disabled={['unavailable', 'submitting', 'checking'].includes(state.status)} onClick={() => {
        if (state.status === 'ready') state.onSubmit();
        if (state.status === 'uncertain') state.onCheck();
        if (state.status === 'failed') state.onRetry();
        if (state.status === 'conflict') state.onReview();
      }}>{state.status === 'uncertain' || state.status === 'checking' ? '查询保存结果' : state.status === 'failed' ? '重新保存' : state.status === 'conflict' ? '核对最新版本' : state.status === 'submitting' ? '正在保存' : label}</Button>}
    <p role="status">{messages[state.status]}{state.status === 'saved' ? `确认时间：${state.confirmedAt}` : ''}</p>
    {draftMessage && <p role="status">{draftMessage}</p>}
  </section>;
}
