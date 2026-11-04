import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

async function load(path, replace = value => value) {
  const source = replace(readFileSync(new URL(path, import.meta.url), 'utf8'));
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
}
const item = { id: '9007199254740993', content_version: '9007199254740994', title: '对白', content: '对白简短', state: 'active' };

test('a late memory response advances its exact version and preserves subsequent typing', async () => {
  const { memoryEditor, receiveMemory, memoryDirty } = await load('../../src/features/assistant/settings/memory-manager-state.ts');
  const edit = memoryEditor(item);
  const submission = { patch: { ...edit.value, title: '新的标题', content_version: item.content_version }, revision: 1 };
  const newer = { ...edit, value: { title: '新的标题', content: '响应前继续输入' }, revision: 2 };
  const result = receiveMemory(newer, { ...item, ...submission.patch, content_version: '9007199254740995' }, submission);
  assert.equal(result.saved.content_version, '9007199254740995');
  assert.deepEqual(result.value, newer.value);
  assert.equal(memoryDirty(result), true);
});

test('confirmed memory fields settle a draft and reject blank input without discarding it', async () => {
  const { memoryEditor, receiveMemory, memoryDirty, memoryValidation } = await load('../../src/features/assistant/settings/memory-manager-state.ts');
  const edit = { ...memoryEditor(item), value: { title: '  ', content: '保留草稿' }, revision: 1 };
  assert.equal(memoryValidation(edit.value), '请填写记忆标题和内容。');
  assert.equal(edit.value.content, '保留草稿');
  const submitted = { patch: { title: '对白', content: '新的对白偏好', content_version: item.content_version }, revision: 1 };
  assert.equal(memoryDirty(receiveMemory(edit, { ...item, ...submitted.patch }, submitted)), false);
});

test('memory deletion sends a versioned body and returns the forgotten item', async () => {
  const calls = [];
  globalThis.__memoryHttp = { async delete(path, config) { calls.push([path, config]); return { data: { ...item, state: 'forgotten' } }; } };
  try {
    const httpUrl = `data:text/javascript;base64,${Buffer.from('export const http = globalThis.__memoryHttp;').toString('base64')}`;
    const { memoryApi } = await load('../../src/api/assistant/settings/memory.ts', value => value.replace('@host/api/http', httpUrl));
    assert.equal((await memoryApi.remove(item.id, item.content_version)).state, 'forgotten');
    assert.deepEqual(calls, [[`/assistant/memory/${item.id}`, { data: { content_version: item.content_version } }]]);
  } finally { delete globalThis.__memoryHttp; }
});
