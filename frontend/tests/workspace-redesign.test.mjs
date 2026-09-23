import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

async function load(path) {
  const source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
}
const { decodeNovelFile } = await load('features/projects/novel-import.ts');
const { taskPrompts, taskOrigin } = await load('features/generations/task-content.ts');
test('TXT import handles UTF-8 BOM, GBK and line endings, rejects binary and oversize', () => {
  assert.equal(decodeNovelFile(new TextEncoder().encode('\uFEFF雨夜\r\n归来').buffer), '雨夜\n归来');
  assert.equal(decodeNovelFile(new Uint8Array([0xd6, 0xd0, 0xce, 0xc4]).buffer), '中文');
  assert.throws(() => decodeNovelFile(new Uint8Array([0]).buffer));
  assert.throws(() => decodeNovelFile(new ArrayBuffer(1048577)));
});
test('task input presentation includes prompts and never serializes unrelated parameters', () => {
  assert.deepEqual(taskPrompts({ input: { prompt: 'saved prompt', reference_media_ids: ['1'] }, parameters: { secret: 'omit' } }), [{ label: '生成提示词', content: 'saved prompt' }]);
  assert.deepEqual(taskPrompts({ input: { messages: [{ role: 'system', content: 'rules' }, { role: 'user', content: 'story' }, null] } }), [{ label: '系统提示词', content: 'rules' }, { label: '用户提示词', content: 'story' }]);
  assert.equal(taskOrigin({ display_context: { project: '雨夜', episode: '第 2 集', subject: '分镜 03 生图', scope: '通用任务' } }), '雨夜 / 第 2 集 / 分镜 03 生图');
});
