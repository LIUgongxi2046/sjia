import { useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { DatasetInput, DatasetView, EvaluationIntentInput, EvaluationRunInput, LabelInput, PartitionInput } from '../../../../frontend-design/v2-types';

export type DatasetOperation = EvaluationIntentInput['operation_id'] | 'dataset_run_create';
export type DatasetPayload = EvaluationIntentInput['payload'] | EvaluationRunInput;
export const datasetOperationNames: Record<DatasetOperation, string> = {
  dataset_create: '保存用途清单', dataset_partition: '登记患儿集合划分',
  dataset_label: '确认独立标签', dataset_freeze: '冻结评价集合',
  dataset_run_create: '创建固定版本评价运行',
};

export function DatasetEditor({ operation, dataset, pending, onSubmit }: {
  operation: DatasetOperation; dataset: DatasetView | null; pending: boolean;
  onSubmit: (payload: DatasetPayload) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(() => ({
    protocol_ref: dataset?.protocol_ref ?? '', source_manifest_ref: dataset?.source_manifest_ref ?? '',
    purpose_copy_ids: dataset?.purpose_copy_ids.join('\n') ?? '', label_version_ref: dataset?.label_version_ref ?? '',
    partition: operation === 'dataset_run_create' && !dataset?.partitions.some(item => item.partition === 'development')
      ? 'validation' : 'development', label_state: 'unknown', patient_id: '', partition_id: '',
  }));
  const composing = useRef(false);
  function field(name: string, label: string, required = true, multiline = false) {
    return <label className="field"><span>{label}</span>{multiline
      ? <textarea required={required} value={values[name] ?? ''} onChange={event => setValues(previous => ({ ...previous, [name]: event.target.value }))} />
      : <input required={required} value={values[name] ?? ''} onChange={event => setValues(previous => ({ ...previous, [name]: event.target.value }))} />}</label>;
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (pending || composing.current) return;
    if (operation !== 'dataset_create' && !dataset) throw new Error('当前操作缺少准确评价集合。');
    if (operation === 'dataset_create') {
      const input: DatasetInput = { dataset_id: dataset?.dataset_id ?? null, expected_version: dataset?.version ?? null,
        protocol_ref: values.protocol_ref, source_manifest_ref: values.source_manifest_ref,
        purpose_copy_ids: values.purpose_copy_ids.split(/\s+/u).filter(Boolean), label_version_ref: values.label_version_ref || null };
      onSubmit(input);
    } else if (operation === 'dataset_partition') {
      const partition = values.partition;
      if (partition !== 'development' && partition !== 'validation') throw new Error('患儿集合划分无效。');
      const input: PartitionInput = { expected_version: dataset!.version, patient_id: values.patient_id,
        partition, authorization_id: values.authorization_id, evidence_ref: values.evidence_ref };
      onSubmit(input);
    } else if (operation === 'dataset_label') {
      const partition = dataset!.partitions.find(item => item.partition_id === values.partition_id);
      if (!partition) throw new Error('独立标签需要准确患儿分组。');
      const state = values.label_state;
      if (state !== 'positive' && state !== 'negative' && state !== 'unknown') throw new Error('独立标签状态无效。');
      const input: LabelInput = { expected_version: dataset!.version, patient_id: partition.patient_id,
        partition_id: partition.partition_id, observation_period_id: values.observation_period_id,
        label_state: state, reference_answer_ref: values.reference_answer_ref || null,
        reference_recognition_at: values.reference_recognition_at || null,
        limitations: (values.limitations ?? '').split('\n').filter(Boolean) };
      onSubmit(input);
    } else if (operation === 'dataset_run_create') {
      if (dataset!.status !== 'frozen' || !dataset!.label_version_ref) throw new Error('评价运行需要冻结资料及准确独立答案版本。');
      const partition = values.partition;
      if (partition !== 'development' && partition !== 'validation') throw new Error('评价运行集合划分无效。');
      onSubmit({ expected_version: dataset!.version, partition,
        config_ids: (values.config_ids ?? '').split(/\s+/u).filter(Boolean), code_version: values.code_version,
        label_version_ref: dataset!.label_version_ref, mode: 'replay' });
    } else onSubmit({ expected_version: dataset!.version, checksum: dataset!.checksum, evidence_ref: values.evidence_ref });
  }
  return <form onSubmit={submit} onCompositionStart={() => { composing.current = true; }}
    onCompositionEnd={() => { composing.current = false; }}>
    <fieldset disabled={pending}>
      {operation === 'dataset_create' && <>
        {field('protocol_ref', '固定评价方案引用')}{field('source_manifest_ref', '真实来源清单引用')}
        {field('purpose_copy_ids', '获准用途副本标识，每行一份', true, true)}{field('label_version_ref', '独立答案版本引用', false)}
      </>}
      {operation === 'dataset_partition' && <>
        {field('patient_id', '患儿标识')}
        <label className="field"><span>患儿集合划分</span><select value={values.partition} onChange={event => setValues(previous => ({ ...previous, partition: event.target.value }))}>
          <option value="development">开发集合</option><option value="validation">独立验证集合</option>
        </select></label>
        {field('authorization_id', '患儿用途授权标识')}{field('evidence_ref', '集合划分依据引用')}
      </>}
      {operation === 'dataset_label' && <>
        <label className="field"><span>准确患儿分组</span><select required value={values.partition_id} onChange={event => setValues(previous => ({ ...previous, partition_id: event.target.value }))}>
          <option value="">请选择已登记分组</option>{dataset!.partitions.map(item => <option key={item.partition_id} value={item.partition_id}>
            {item.patient_id} · {item.partition === 'development' ? '开发集合' : '独立验证集合'}
          </option>)}
        </select></label>
        {field('observation_period_id', '观察区间版本标识')}
        <label className="field"><span>独立标签</span><select value={values.label_state} onChange={event => setValues(previous => ({ ...previous, label_state: event.target.value }))}>
          <option value="unknown">未知</option><option value="positive">阳性</option><option value="negative">阴性</option>
        </select></label>
        {field('reference_answer_ref', '独立参考答案引用', values.label_state !== 'unknown')}
        {field('reference_recognition_at', '首次识别时间，包含准确时区', values.label_state !== 'unknown')}
        {field('limitations', '无法评估范围，每行一项', false, true)}
      </>}
      {operation === 'dataset_freeze' && <>
        <p>冻结当前版本 {dataset!.version}。冻结后修改资料需要保存新的版本。</p>
        {field('evidence_ref', '冻结确认依据引用')}
      </>}
      {operation === 'dataset_run_create' && <>
        <p>固定资料版本 {dataset!.version}；独立答案版本 {dataset!.label_version_ref}。</p>
        <label className="field"><span>评价使用的患儿集合</span><select value={values.partition} onChange={event => setValues(previous => ({ ...previous, partition: event.target.value }))}>
          <option value="development" disabled={!dataset!.partitions.some(item => item.partition === 'development')}>开发集合</option>
          <option value="validation" disabled={!dataset!.partitions.some(item => item.partition === 'validation')}>独立验证集合</option>
        </select></label>
        {field('config_ids', '已批准规则版本标识，每行一项', true, true)}
        {field('code_version', '本次采用的已验证程序摘要')}
        <p>历史回放保留准确输入和完整分母，不发送家庭通知。</p>
      </>}
      <button className="button" type="submit" disabled={pending}>{pending ? '正在保存并核验' : datasetOperationNames[operation]}</button>
    </fieldset>
  </form>;
}
