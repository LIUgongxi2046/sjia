import { fieldLabel, fieldValue, type RecordFieldView } from '../api/records';

/** Presentation only: retain fieldValue's exact text, with a smaller unit slot. */
export function RecordFieldValue({ field }: { field: RecordFieldView }) {
  const value = fieldValue(field);
  const unit = field.raw_unit ? ` ${field.raw_unit}` : '（单位待补充）';
  if (field.value_state === 'unknown' || field.raw_numeric === null || !value.endsWith(unit)) return <>{value}</>;
  return <><span className="rich-record-number">{value.slice(0, -unit.length)}</span><span className="rich-record-unit">{unit}</span></>;
}

export function RecordFieldPrimary({ field }: { field: RecordFieldView }) {
  return <div className="pair rich-field-primary"><span>{fieldLabel(field)}</span><strong>{fieldValue(field)}</strong></div>;
}
