import { useState } from 'react';
import type { FormEvent } from 'react';
import type { ConfigInput, ReleaseInput } from '../../../../frontend-design/types';
import { publicationOperations, publicationTarget } from '../../api/publication';
import type { ConfigDetail, ContactDetail, PublicationDirectory, PublicationOperation, PublicationPayload, ReleaseDetail } from '../../api/publication';
import { validateApiValue } from '../../api/schema';

const kinds: ReleaseInput['components'][number]['kind'][] = ['rule', 'window', 'verification', 'mapping', 'visibility', 'content', 'notification'];
const names = { rule: '规则版本', window: '窗口版本', verification: '核对策略', mapping: '映射版本', visibility: '可见字段', content: '家庭文字版本', notification: '通知策略' };

export function PublicationEditor({ operation, directories, config, contact, release, gateVersion, text, busy, onSubmit }: {
  operation: PublicationOperation; directories: Partial<Record<'config' | 'contact' | 'source', PublicationDirectory>>;
  config: ConfigDetail | null; contact: ContactDetail | null; release: ReleaseDetail | null; gateVersion: string;
  text: string; busy: boolean; onSubmit: (operation: PublicationOperation, target: string, payload: PublicationPayload) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [schemaIndex, setSchemaIndex] = useState('');
  const configs = directories.config?.items ?? [], contacts = directories.contact?.items ?? [], sources = directories.source?.items ?? [];
  const schemas = directories.config?.schemas ?? [];
  const original = config?.resource.config;
  const initialPayload = original ? JSON.stringify(original.schema_version === 'content-1' && text.trim()
    ? { ...original.payload, approved_text: text } : original.payload, null, 2) : '';
  const id = operation.startsWith('config_') ? config?.resource.id : operation.startsWith('contact_') ? contact?.id : release?.resource.release_id;
  const status = operation.startsWith('config_') ? config?.resource.status : operation.startsWith('contact_') ? contact?.status : release?.resource.status;
  const version = operation.startsWith('config_') ? config?.resource.version : operation.startsWith('contact_') ? contact?.version : release?.resource.version;
  const input = (name: string, label: string, value = '', type = 'text', required = true) =>
    <label className="field"><span>{label}</span><input name={name} type={type} defaultValue={value} required={required} disabled={busy} /></label>;
  const select = (name: string, label: string, rows: { id: string; title: string; version: string | null }[], optional = false) =>
    <label className="field"><span>{label}</span><select name={name} required={!optional} disabled={busy} defaultValue="">
      <option value="">{optional ? '未引用来源' : '选择准确版本'}</option>{rows.map(row => <option key={row.id} value={row.id}>{row.title}{row.version ? ` · 版本 ${row.version}` : ''} · {row.id}</option>)}
    </select></label>;
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy || event.nativeEvent instanceof KeyboardEvent && event.nativeEvent.isComposing) return;
    const data = new FormData(event.currentTarget);
    const value = (name: string) => { const item = data.get(name); if (typeof item !== 'string') throw new Error('操作字段尚未完整填写。'); return item; };
    setError(null);
    try {
      let payload: PublicationPayload, targetId = id;
      if (operation === 'config_create') {
        const schema = schemas[Number(schemaIndex)]; if (!schema || schemaIndex === '') throw new Error('需要选择已登记的配置格式。');
        payload = { kind: schema.kind as ConfigInput['kind'], config_key: value('config_key'), schema_version: schema.schema_version,
          payload: JSON.parse(value('payload')) as Record<string, unknown>, source_revision_id: value('source_revision_id') || null };
      } else if (operation === 'release_create') {
        const directory = directories.config; if (!directory) throw new Error('当前发布环境尚未读取。');
        payload = { environment: directory.environment, components: kinds.map(kind => ({ kind, config_id: value(kind) })),
          validation_config_id: value('validation_config_id'), contact_version_id: value('contact_version_id') };
      } else if (operation === 'contact_create') {
        payload = { contact_id: value('contact_id'), contact_name: value('contact_name'), purpose: value('purpose'), channel: value('channel'),
          channel_value: value('channel_value'), source_revision_id: value('source_revision_id'), approval_config_id: value('approval_config_id'), valid_until: value('valid_until') };
      } else if (operation === 'config_approve') {
        if (!config) throw new Error('准确配置尚未读取。');
        payload = { config_checksum: config.resource.checksum, environment: directories.config!.environment, approval_role: value('approval_role'),
          evidence_ref: `urn:sjia:source-revision:${value('evidence_source')}`, audience: JSON.parse(value('audience')) as string[], valid_until: value('valid_until') || null };
      } else if (operation === 'config_validate') {
        if (!config) throw new Error('准确配置尚未读取。');
        const source = sources.find(row => row.id === value('evidence_source')); if (!source?.checksum) throw new Error('真实验证报告摘要尚未读取。');
        payload = { config_checksum: config.resource.checksum, environment: directories.config!.environment, artifact_sha256: config.artifact_sha256,
          dataset_revision_id: value('dataset_revision_id'), evidence_ref: `urn:sjia:source-revision:${source.id}`, evidence_sha256: source.checksum };
      } else if (publicationOperations[operation][1] === 'GateAction') {
        payload = { expected_gate_version: gateVersion, reason: value('reason') };
      } else {
        if (!status || !version) throw new Error('当前对象状态与版本尚未读取。');
        if (operation === 'config_approval_revoke') targetId = value('approval_id');
        payload = { expected_state: operation === 'config_approval_revoke' ? 'active' : status,
          ...(operation === 'config_approval_revoke' ? {} : { expected_version: version }), reason: value('reason') };
      }
      validateApiValue(`protocol.schema.json#/$defs/${publicationOperations[operation][1]}`, payload);
      onSubmit(operation, publicationTarget(operation, targetId), payload);
    } catch (failure) { setError(failure instanceof Error ? failure.message : '当前操作正文校验失败。'); }
  }
  return <form className="form-card" onSubmit={submit}>
    {operation === 'config_create' && <>{input('config_key', '配置标识', original?.config_key)}
      <label className="field"><span>配置格式</span><select required disabled={busy} value={schemaIndex} onChange={event => setSchemaIndex(event.target.value)}>
        <option value="">选择已登记格式</option>{schemas.map((schema, index) => <option key={`${schema.kind}:${schema.schema_version}`} value={index}>{schema.kind} · {schema.schema_version}</option>)}
      </select></label><label className="field"><span>完整配置正文</span><textarea name="payload" required disabled={busy} defaultValue={initialPayload} placeholder="提供当前格式要求的完整 JSON 正文" /></label>
      {select('source_revision_id', '配置来源', sources, true)}</>}
    {operation === 'release_create' && <>{kinds.map(kind => <div key={kind}>{select(kind, names[kind], configs.filter(row => row.kind === kind))}</div>)}
      {select('validation_config_id', '独立验证版本', configs.filter(row => row.kind === 'validation'))}{select('contact_version_id', '联系信息版本', contacts)}</>}
    {operation === 'contact_create' && <>{input('contact_id', '联系主体标识', contact?.contact.contact_id)}
      {input('contact_name', '联系名称', contact?.contact.contact_name)}{input('purpose', '联系用途', contact?.contact.purpose)}
      {input('channel', '联系渠道', contact?.contact.channel)}{input('channel_value', '准确联系地址', contact?.contact.channel_value)}
      {select('source_revision_id', '联系来源', sources)}{select('approval_config_id', '准确内容批准版本', configs.filter(row => row.schema_version === 'contact-1'))}
      {input('valid_until', '有效截止时间', contact?.contact.valid_until, 'text')}</>}
    {operation === 'config_approve' && <>{input('approval_role', '批准岗位')}{select('evidence_source', '批准证明来源', sources)}
      {input('audience', '获准读者范围 JSON')}{input('valid_until', '有效截止时间，可留空', '', 'text', false)}
      <p className="muted">配置摘要：{config?.resource.checksum}</p></>}
    {operation === 'config_validate' && <>{select('evidence_source', '真实验证报告来源', sources)}{select('dataset_revision_id', '验证资料来源版本', sources)}
      <p className="muted">当前制品摘要：{config?.artifact_sha256}</p></>}
    {operation === 'config_approval_revoke' && select('approval_id', '当前批准记录', (config?.approvals ?? []).filter(row => !row.revoked_at)
      .map(row => ({ id: row.id, title: row.approved_at, version: null })))}
    {!operation.endsWith('_create') && !['config_approve', 'config_validate'].includes(operation) && <>
      <div className="pair"><span>{publicationOperations[operation][1] === 'GateAction' ? '当前环境版本' : '当前对象版本'}</span><strong>{publicationOperations[operation][1] === 'GateAction' ? gateVersion : version}</strong></div>
      {input('reason', '操作依据')}</>}
    {error && <p role="alert">{error}</p>}<button className="button" type="submit" disabled={busy || !operation.endsWith('_create') && !id}>
      {busy ? '正在确认提交结果' : publicationOperations[operation][3]}</button>
  </form>;
}
