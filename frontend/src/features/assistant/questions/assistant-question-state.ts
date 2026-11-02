import type { AssistantQuestionAnswer } from '@host/types/assistant/api';
import type { AssistantAnswerableQuestion, AssistantQuestionDraft } from '@host/types/assistant/questions/state';

export const emptyQuestionDraft = (): AssistantQuestionDraft => ({ values: {}, otherValues: {}, otherSelected: [], index: 0, pending: null });
const draftKey = (accountId: string, conversationId: string, operationId: string) =>
  `short-drama:user:${accountId}:assistant-question:v2:${conversationId}:${operationId}`;
export function readQuestionDraft(accountId: string, conversationId: string, operationId: string, storage: Pick<Storage, 'getItem'>): AssistantQuestionDraft {
  try {
    const value = JSON.parse(storage.getItem(draftKey(accountId, conversationId, operationId)) ?? 'null');
    if (value && typeof value.values === 'object' && value.values && typeof value.otherValues === 'object' && value.otherValues && Array.isArray(value.otherSelected) && Number.isInteger(value.index) && value.index >= 0 &&
        (value.pending === null || (typeof value.pending?.key === 'string' && typeof value.pending.body?.question_version === 'string')))
      return value;
  } catch { /* 无效草稿不作为已提交请求恢复。 */ }
  return emptyQuestionDraft();
}
export function saveQuestionDraft(accountId: string, conversationId: string, operationId: string, draft: AssistantQuestionDraft, storage: Pick<Storage, 'setItem'>) {
  try { storage.setItem(draftKey(accountId, conversationId, operationId), JSON.stringify(draft)); return true; }
  catch { return false; }
}
export function questionAnswer(question: AssistantAnswerableQuestion, draft: AssistantQuestionDraft, outcome: AssistantQuestionAnswer['outcome']): AssistantQuestionAnswer {
  const values: AssistantQuestionAnswer['values'] = {};
  if (outcome === 'answered') for (const field of question.request.fields) {
    const selected = draft.values[field.key];
    if (draft.otherSelected.includes(field.key)) {
      const other = (draft.otherValues[field.key] ?? '').trim();
      values[field.key] = field.type === 'multi_select' ? [...new Set([...(Array.isArray(selected) ? selected : []), other])] : other;
    } else if (selected != null) values[field.key] = selected;
  }
  return { question_version: question.question_version, outcome, values, additional_text: '' };
}
export function questionError(question: AssistantAnswerableQuestion, draft: AssistantQuestionDraft) {
  const blankOther = question.request.fields.find(field => draft.otherSelected.includes(field.key) && !draft.otherValues[field.key]?.trim());
  if (blankOther) return `请填写「${blankOther.label}」的其他回答`;
  const values = questionAnswer(question, draft, 'answered').values;
  const missing = question.request.fields.find(field => field.required &&
    (values[field.key] == null || values[field.key] === '' || (Array.isArray(values[field.key]) && !(values[field.key] as string[]).length)));
  return missing ? `请回答「${missing.label}」` : '';
}
