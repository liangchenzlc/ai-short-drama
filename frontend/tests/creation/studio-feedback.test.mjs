import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = ts.transpileModule(readFileSync(new URL('../../src/components/ui/studio-feedback-store.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { createFeedbackStore, feedbackType } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('repeated polling errors deduplicate while changed incidents and later retries can notify', () => {
  const store = createFeedbackStore();
  assert.equal(store.shouldNotify('upload:asset1', 'network', 100), true);
  assert.equal(store.shouldNotify('upload:asset1', 'network', 101), false);
  assert.equal(store.shouldNotify('upload:asset1', 'permission', 102), true);
  assert.equal(store.shouldNotify('upload:asset2', 'network', 103), true);
  assert.equal(store.shouldNotify('upload:asset2', 'network', 30_104), true);
});

test('closing a notice does not delete recovery actions; resolving one source preserves the other', () => {
  const store = createFeedbackStore();
  let changes = 0;
  const unsubscribe = store.subscribe(() => changes++);
  const recovery = () => 'original-request';
  store.upsert({ id: 'first', type: 'warning', summary: '受理待核对', content: recovery });
  store.upsert({ id: 'second', type: 'error', summary: '保存失败', content: 'draft-preserved' });
  store.shouldNotify('first', 'warning');
  assert.equal(store.getSnapshot()[0].content(), 'original-request');
  store.remove('first');
  assert.equal(store.getSnapshot()[0].id, 'second');
  assert.equal(changes, 3);
  unsubscribe();
});

test('feedback stores isolate independent roots and dialogs', () => {
  const first = createFeedbackStore(), second = createFeedbackStore();
  first.upsert({ id: 'private', type: 'error', summary: 'private input', content: '' });
  assert.equal(second.getSnapshot().length, 0);
  assert.equal(first.shouldNotify('same-key', 'error', 1), true);
  assert.equal(second.shouldNotify('same-key', 'error', 1), true);
});

test('legacy mixed channels distinguish uncertain requests, errors and accepted tasks', () => {
  assert.equal(feedbackType('原请求结果不明，请核对'), 'warning');
  assert.equal(feedbackType('上传失败，请重试'), 'error');
  assert.equal(feedbackType('图片任务 1 已提交。'), 'success');
  assert.equal(feedbackType('当前版本已变化，输入仍保留。'), 'warning');
});
