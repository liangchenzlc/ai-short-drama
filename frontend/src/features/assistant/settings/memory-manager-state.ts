import type { MemoryEdit, MemoryFields, MemoryItem, MemorySubmission } from '@host/types/assistant/settings/memory';

export const memoryFields = (item: MemoryFields): MemoryFields => ({ title: item.title, content: item.content });
export const sameMemory = (left: MemoryFields, right: MemoryFields) => left.title === right.title && left.content === right.content;
export const memoryDirty = (edit: MemoryEdit) => !sameMemory(edit.saved, edit.value);
export const memoryUpdatedLabel = (value: string) => new Date(value).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
export function memoryValidation(value: MemoryFields) {
  if (!value.title.trim() || !value.content.trim()) return '请填写记忆标题和内容。';
  return value.title.length > 200 || value.content.length > 12000 ? '标题最多 200 字，内容最多 12000 字。' : '';
}
export function memoryEditor(item: MemoryItem): MemoryEdit {
  return { saved: item, value: memoryFields(item), revision: 0, blocked: false, issue: '', conflict: null, submission: null };
}
export function receiveMemory(edit: MemoryEdit, item: MemoryItem, submission: MemorySubmission): MemoryEdit {
  // 请求发出后输入仍可继续；响应只推进版本，不覆盖后来的草稿。
  return { ...edit, saved: item, value: edit.revision === submission.revision ? memoryFields(item) : edit.value,
    blocked: false, issue: '', conflict: null, submission: null };
}
