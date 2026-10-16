import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import protocol from '../../../backend-design/protocol.schema.json';
import protocolV2 from '../../../backend-design/v2-protocol.schema.json';
import dataset from '../../../data-design/dataset.schema.json';
import caseExtension from '../../../data-design/case-extension.schema.json';
import reportRecognition from '../../../backend/recognition/report.schema.json';
import therapyRecognition from '../../../backend/recognition/therapy.schema.json';
import alertNode from '../../../alert-design/node.schema.json';
import publication from '../../../backend-design/publication.schema.json';
import notification from '../../../backend-design/notification.schema.json';
import personalRecordTask from '../../../backend-design/personal-record-task.schema.json';
import materialReminderNotice from '../../../backend-design/material-reminder-notice.schema.json';
import archiveRelations from '../../../backend-design/archive-relations.schema.json';
import archiveSearch from '../../../backend-design/archive-search.schema.json';
import archiveDelta from '../../../backend-design/archive-delta.schema.json';
import observationPeriod from '../../../backend-design/observation-period.schema.json';
import archiveExtraction from '../../../alert-design/v2-recognition.schema.json';
import voiceInput from '../../../backend-design/voice-input.schema.json';
import familyRecord from '../../../backend/records/search.schema.json';
import fileValidation from '../../../backend-design/file-validation.schema.json';

const validator = new Ajv2020({ strict: true, strictTypes: false, strictRequired: false,
  allowUnionTypes: true, coerceTypes: false, useDefaults: false, removeAdditional: false });
addFormats(validator);
validator.addSchema(dataset, 'dataset.schema.json');
validator.addSchema(dataset, 'data-design/dataset.schema.json');
validator.addSchema(caseExtension, 'case-extension.schema.json');
validator.addSchema(caseExtension, 'data-design/case-extension.schema.json');
validator.addSchema(protocol, 'protocol.schema.json');
validator.addSchema(protocolV2, 'v2-protocol.schema.json');
validator.addSchema(reportRecognition, 'report-recognition.schema.json');
validator.addSchema(therapyRecognition, 'therapy-recognition.schema.json');
validator.addSchema(alertNode, 'alert-design/node.schema.json');
validator.addSchema(publication, 'publication.schema.json');
validator.addSchema(notification, 'notification.schema.json');
validator.addSchema(personalRecordTask, 'personal-record-task.schema.json');
validator.addSchema(materialReminderNotice, 'material-reminder-notice.schema.json');
validator.addSchema(archiveRelations, 'archive-relations.schema.json');
validator.addSchema(archiveSearch, 'archive-search.schema.json');
validator.addSchema(archiveDelta, 'archive-delta.schema.json');
validator.addSchema(observationPeriod, 'observation-period.schema.json');
validator.addSchema(archiveExtraction, 'alert-design/v2-recognition.schema.json');
validator.addSchema(voiceInput, 'voice-input.schema.json');
validator.addSchema(familyRecord, 'family-record.schema.json');
validator.addSchema(fileValidation, 'file-validation.schema.json');

export function checkPurposeSchemas(): string[] {
  const names = ['SupportLoginInput', 'SupportSessionView', 'PurposeCopyInput', 'PurposeCopyOptionsView', 'PurposeReviewInput', 'PurposeRevokeInput', 'PurposeCopyView', 'PurposeCopyEvidenceView', 'WriteReceipt', 'TaskView', 'RequestView',
    'DispositionApprovalInput', 'DispositionExecuteInput', 'DispositionRetainInput', 'DispositionRetryInput', 'DispositionView'];
  const refs = names.map(name => `v2-protocol.schema.json#/$defs/${name}`);
  refs.push('protocol.schema.json#/$defs/IntentView');
  for (const ref of refs) if (!validator.getSchema(ref)) throw new Error(`接口 Schema 尚未登记：${ref}`);
  return refs;
}

export function validateApiValue<T>(schema: string, value: unknown): T {
  const check = validator.getSchema(schema);
  if (!check) throw new Error(`接口 Schema 尚未登记：${schema}`);
  if (!check(value)) throw new Error('接口资料未通过 Schema 校验，本次内容未被采用。');
  return value as T;
}

export function compileApiSchemas(refs: string[]) {
  for (const ref of refs) if (!validator.getSchema(ref)) throw new Error(`接口 Schema 尚未登记：${ref}`);
  return refs;
}

export function checkAssessmentSchemas(): string[] {
  const names = ['AssessmentInput', 'AssessmentIntentInput', 'AssessmentRequestLookup', 'AssessmentReason',
    'AssessmentContextStates', 'AssessmentStoredInput', 'AssessmentOutput', 'AssessmentView', 'TaskView', 'RequestView', 'WriteReceipt',
    'AssessmentCreationQuery', 'ClinicalDirectoryQuery', 'ClinicalDirectoryRecord', 'ClinicalDirectoryView',
    'AssessmentConfigurationOption', 'AssessmentCreationOptions', 'AssessmentObservationTime', 'AssessmentClinicalSelection',
    'AssessmentSelectionView', 'AssessmentSelectionCandidate', 'AssessmentCreationPlan'];
  const refs = names.map(name => `v2-protocol.schema.json#/$defs/${name}`);
  refs.push('protocol.schema.json#/$defs/IntentView');
  for (const ref of refs) if (!validator.getSchema(ref)) throw new Error(`接口 Schema 尚未登记：${ref}`);
  return refs;
}

export function checkDynamicSchemas(): string[] {
  const names = ['DynamicInput', 'DynamicClinicalSelection', 'DynamicIntentInput', 'DynamicRequestLookup',
    'DynamicSignal', 'DynamicGroup', 'DynamicCombination', 'DynamicLifecycle', 'DynamicView', 'DynamicAutomaticTrigger', 'DynamicTriggerHistory', 'DynamicTriggerQuery',
    'DynamicCreationOptions', 'DynamicCreationPlan', 'TaskView', 'RequestView'];
  const refs = names.map(name => `v2-protocol.schema.json#/$defs/${name}`);
  for (const ref of refs) if (!validator.getSchema(ref)) throw new Error(`接口 Schema 尚未登记：${ref}`);
  return refs;
}

export function checkVerificationSchemas(): string[] {
  const names = ['VerificationRequestInput', 'VerificationInput', 'VerificationRevokeInput', 'VerificationIntentInput',
    'VerificationRequestLookup', 'VerificationFieldView', 'VerificationResourceView', 'VerificationOptionsView', 'VerificationCreationScopeView', 'RequestView', 'WriteReceipt'];
  const refs = names.map(name => `v2-protocol.schema.json#/$defs/${name}`);
  refs.push('protocol.schema.json#/$defs/IntentView');
  for (const ref of refs) if (!validator.getSchema(ref)) throw new Error(`接口 Schema 尚未登记：${ref}`);
  return refs;
}
