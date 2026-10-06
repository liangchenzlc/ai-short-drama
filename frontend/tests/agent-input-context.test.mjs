import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/agents/agent-input-context.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { attachmentInputIssue, attachmentFileIssue } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const attachment = (kind, metadata = {}) => ({ kind, metadata });
const model = (image = false, audio = false) => ({ input_capabilities: { text: true, image, audio, video: image ? 'sampled_frames' : 'unsupported' } });

test('unknown or previously text-only models do not prevent sending visual or audio references', () => {
  assert.equal(attachmentInputIssue([attachment('text')], model()), '');
  assert.equal(attachmentInputIssue([attachment('image')], model()), '');
  assert.equal(attachmentInputIssue([attachment('image')], model(true)), '');
  assert.equal(attachmentInputIssue([attachment('audio')], model(true)), '');
  assert.equal(attachmentInputIssue([attachment('audio')], model(true, true)), '');
  assert.equal(attachmentInputIssue([attachment('image')]), '');
});

test('video references retain the explicit audio choice and are checked by the actual runtime', () => {
  const video = attachment('video', { has_audio: true });
  assert.equal(attachmentInputIssue([video], model(true)), '');
  assert.equal(attachmentInputIssue([video], model(true), 'visual_only'), '');
  assert.equal(attachmentInputIssue([video], model(), 'visual_only'), '');
  assert.equal(attachmentInputIssue([video], model(true, true)), '');
  assert.equal(attachmentInputIssue([attachment('video')], model(true)), '');
});

test('attachment count and upload bounds stop oversized submissions while accepting the exact limit', () => {
  assert.equal(attachmentInputIssue(Array.from({ length: 16 }, () => attachment('text')), model()), '');
  assert.match(attachmentInputIssue(Array.from({ length: 17 }, () => attachment('text')), model()), /最多添加 16/);
  assert.match(attachmentFileIssue({ name: '空文件.txt', size: 0 }, 'text'), /文件为空/);
  for (const [kind, mib] of [['text', 1], ['image', 20], ['audio', 20], ['video', 50]]) {
    assert.equal(attachmentFileIssue({ name: '边界文件', size: mib * 1024 * 1024 }, kind), '');
    assert.match(attachmentFileIssue({ name: '超限文件', size: mib * 1024 * 1024 + 1 }, kind), /上传限制/);
  }
});
