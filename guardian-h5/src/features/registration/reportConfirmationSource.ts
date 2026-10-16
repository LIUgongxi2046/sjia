import type { ReportItemInput } from './reportFieldValue';

/** Acknowledgment describes only the source evidence attached to each item. */
export function reportConfirmationSource(items: ReportItemInput[], photos: Array<{ file_id: string }>) {
  const imageItems = items.filter(item => !!item.photo_id);
  const manualItems = items.length - imageItems.length;
  const incompleteImageSource = imageItems.some(item => !item.source_text?.trim() || !photos.some(photo => photo.file_id === item.photo_id));
  if (incompleteImageSource) return {
    incompleteImageSource, label: '原图来源不完整，暂不能确认',
    note: '识别项目缺少本次关联原件或来源原文。补齐来源后才能逐项核对；本人确认不代表专业核对。',
  };
  if (!imageItems.length) return {
    incompleteImageSource, label: '已逐项核对手动填写内容（项目未关联原图）',
    note: '本次为手动登记。请按实际可用资料逐项核对；没有资料支持的内容保持未知，不声称已经核对原图。本人确认不代表专业核对。',
  };
  return {
    incompleteImageSource,
    label: manualItems ? '已核对原图项目，并逐项核对手动填写内容' : '已对照原图逐项核对',
    note: manualItems ? '手动项目未关联原图，请按实际可用资料核对，未知内容保持未知。本人确认不代表专业核对。' : '逐项核对原值、单位、时间与来源；本人确认不代表专业核对。',
  };
}
