import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const path = new URL('../../src/features/assistant/questions/assistant-question-state.ts', import.meta.url);
async function implementation() {
  assert.ok(existsSync(path), 'persistent question state must be implemented');
  const code = ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
}
const question = { operation_id: '201', question_version: '9007199254740993', status: 'pending', can_answer: true, request: { title: '确认方向', question: '请选择', allow_skip: true, fields: [{ key: 'direction', type: 'single_select', label: '方向', required: true, options: [{ value: 'warm', label: '温情', recommended: true }, { value: 'dark', label: '悬疑', recommended: false }] }] } };
test('question drafts and uncertain answer identity survive refresh without answering another operation', async () => {
  const { readQuestionDraft, saveQuestionDraft, questionAnswer, emptyQuestionDraft } = await implementation();
  const entries = new Map(); const storage = { getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value) };
  const draft = { ...emptyQuestionDraft(), values: { direction: 'warm' }, otherValues: { mood: '雨夜🙂' }, otherSelected: ['mood'], index: 1, pending: { key: 'original-answer', body: { question_version: question.question_version, outcome: 'answered', values: { direction: 'warm' }, additional_text: '' } } };
  saveQuestionDraft('1', '3', question.operation_id, draft, storage);
  assert.deepEqual(readQuestionDraft('1', '3', '201', storage), draft);
  assert.deepEqual(readQuestionDraft('2', '3', '201', storage), emptyQuestionDraft());
  assert.deepEqual(readQuestionDraft('1', '3', '202', storage), emptyQuestionDraft());
  assert.deepEqual(questionAnswer(question, draft, 'skipped'), { question_version: question.question_version, outcome: 'skipped', values: {}, additional_text: '' });
});
test('multiple questions combine single and multi selections with per-question other text', async () => {
  const { questionError, questionAnswer, emptyQuestionDraft } = await implementation();
  const request = { ...question, request: { ...question.request, fields: [...question.request.fields, { key: 'mood', type: 'multi_select', label: '氛围', required: true, options: [{ value: 'rain', label: '雨夜', recommended: true }, { value: 'fog', label: '雾气', recommended: false }] }] } };
  const draft = { ...emptyQuestionDraft(), index: 1, values: { mood: ['rain'] }, otherSelected: ['direction', 'mood'], otherValues: { direction: ' 开放结尾 ', mood: ' 铜钥匙🙂 ' } };
  assert.equal(questionError(request, draft), '');
  assert.deepEqual(questionAnswer(request, draft, 'answered'), { question_version: '9007199254740993', outcome: 'answered', values: { direction: '开放结尾', mood: ['rain', '铜钥匙🙂'] }, additional_text: '' });
  assert.match(questionError(request, { ...draft, otherValues: { direction: ' ' } }), /其他/);
  assert.match(questionError(request, emptyQuestionDraft()), /方向/);
});
