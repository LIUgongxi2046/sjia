import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import schema from '../../../backend/files/original-read.schema.json' with { type: 'json' };
import recordSchema from '../../../backend/records/search.schema.json' with { type: 'json' };
import type { RecordFieldView } from './records';

export interface OriginalSourceView {
  patient_id: string; record_id: string; record_version_id: string; record_version: number; source_organization: string | null;
  originals: {
    file_id: string; object_version: string; checksum: string; status: 'quarantined' | 'available' | 'failed' | 'disposed';
    media_type: string; ordinal: number; created_at: string; pages: { page_id: string; ordinal: number }[];
    documents: { document_id: string; document_version_id: string; document_type: string; source_organization: string | null; documented_time: RecordFieldView['medical_time'] }[];
    use_authorizations: { authorization_id: string; purpose: string; environment: string; evidence_ref: string; valid_until: string | null }[];
    purpose_copies: { copy_id: string; version: string; purpose: string; status: 'pending_review' | 'approved' | 'rejected' | 'revoked' | 'disposed';
      review_id: string | null; reviewed_at: string | null; revoked_at: string | null; identity_result: string | null; clinical_result: string | null }[];
  }[];
}

const validator = new Ajv2020({ strict: true, allowUnionTypes: true, coerceTypes: false, useDefaults: false, removeAdditional: false });
addFormats(validator);
validator.addSchema(recordSchema, 'record-search.schema.json');
const validate = validator.compile<OriginalSourceView>(schema);

export function validateOriginalSource(value: unknown): OriginalSourceView {
  if (!validate(value)) throw new Error('原件元数据未通过 Schema 校验，本次内容未被采用。');
  const result = value;
  if (new Set(result.originals.map(file => file.file_id)).size !== result.originals.length
    || new Set(result.originals.map(file => file.ordinal)).size !== result.originals.length) throw new Error('原件标识或顺序重复，本次内容未被采用。');
  for (const file of result.originals) {
    if (new Set(file.pages.map(page => page.page_id)).size !== file.pages.length
      || new Set(file.pages.map(page => page.ordinal)).size !== file.pages.length
      || new Set(file.documents.map(document => document.document_version_id)).size !== file.documents.length
      || new Set(file.purpose_copies.map(copy => copy.copy_id)).size !== file.purpose_copies.length) throw new Error('原件的页面、文书或副本引用重复。');
    for (const copy of file.purpose_copies) {
      if ((copy.review_id === null) !== (copy.reviewed_at === null)
        || (copy.review_id === null && (copy.identity_result !== null || copy.clinical_result !== null || copy.revoked_at !== null))) throw new Error('副本复核信息缺少准确保存依据。');
    }
  }
  return result;
}
