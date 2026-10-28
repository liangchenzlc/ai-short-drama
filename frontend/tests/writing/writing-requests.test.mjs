import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

async function load(file) {
  const source = readFileSync(new URL(`../../src/features/projects/script-desk/${file}.ts`, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
}
const { writingRewriteRequest, codePointSelection } = await load('writing-requests');
const { ScriptHistory } = await load('editor-history');
const { scriptExportName, scriptCharacterCount, selectionSummary } = await load('script-selection');
const state = { novel: '小说', outline: '', script: '😀开头，同一句话；同一句话👨‍👩‍👧', scriptId: '9007199254740999', contentVersion: '9007199254740997' };

test('export names remain portable and range summaries preserve complete Unicode characters', () => {
  assert.equal(scriptExportName('雨夜:来信/第一稿. '), '雨夜来信第一稿-剧本.txt');
  assert.equal(scriptExportName(' /:*? '), '作品-剧本.txt');
  assert.equal(scriptCharacterCount(' 😀 台词\n\t'), 3);
  const text = '😀'.repeat(100);
  assert.equal(selectionSummary(text), '😀'.repeat(44) + '…' + '😀'.repeat(44));
});

test('emoji and repeated selections use exact half-open code-point offsets', () => {
  const start = state.script.lastIndexOf('同一句话');
  const selection = { source: state.script, start, end: start + 4, text: '同一句话' };
  const request = writingRewriteRequest('10', state, selection, '缩短这句', '');
  assert.equal(request.source.start, Array.from(state.script.slice(0, start)).length);
  assert.equal(request.source.end - request.source.start, 4);
  assert.equal(request.source.script_id, state.scriptId);
  assert.throws(() => writingRewriteRequest('10', { ...state, script: '另一个版本' }, selection, '修改', ''));
  assert.throws(() => codePointSelection({ source: '😀', start: 0, end: 1, text: '\ud83d' }));
});

test('typing coalesces while AI replacements and IME commits remain undo transactions', () => {
  const history = new ScriptHistory('原文');
  assert.equal(history.canUndo, false);
  assert.equal(history.canRedo, false);
  history.record('原文a', 3, 3, true, 1000);
  history.record('原文ab', 4, 4, true, 1200);
  history.record('AI 修改后', 6, 6);
  assert.equal(history.move(-1).text, '原文ab');
  assert.equal(history.canRedo, true);
  assert.equal(history.move(-1).text, '原文');
  assert.equal(history.move(1).text, '原文ab');
  history.record('中文输入完成', 6, 6);
  assert.equal(history.canRedo, false);
  assert.equal(history.move(1), null);
  assert.equal(history.move(-1).text, '原文ab');
});
