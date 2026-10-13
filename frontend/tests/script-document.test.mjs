import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../src/features/projects/script-document.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { documentText, textDocument, isEditorDocument, sameDocument, scriptScenes, scriptWordCount } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('only Quill final newline is removed, preserving Chinese, whitespace and blank lines', () => {
  for (const content of ['', '中文', '  中文\n\n', '\n第一场\n台词\n']) assert.equal(documentText(textDocument(content)), content);
  assert.equal(documentText({ ops: [{ insert: '第一场', attributes: { bold: true } }, { insert: '\n\n\n', attributes: { blockquote: true } }] }), '第一场\n\n');
});

test('document validation accepts limited text formats and rejects embeds or executable attributes', () => {
  assert.equal(isEditorDocument({ ops: [{ insert: '场次\n', attributes: { header: 3, bold: true, italic: true, blockquote: false } }] }), true);
  for (const ops of [[{ insert: { image: 'image' } }], [{ insert: '文本\n', attributes: { link: 'javascript:alert(1)' } }], [{ retain: 1 }], [{ insert: '文本' }], [{ insert: '文本\n', attributes: { header: 4 } }], [{ insert: '文本\n', attributes: { bold: 'true' } }]]) assert.equal(isEditorDocument({ ops }), false);
});

test('equivalent split operations and unformatted legacy content do not create false dirty state', () => {
  assert.equal(sameDocument(null, { ops: [{ insert: '正' }, { insert: '文\n' }] }, '正文'), true);
  assert.equal(sameDocument({ ops: [{ insert: '正文\n', attributes: { bold: false } }] }, null, '正文'), true);
  assert.equal(sameDocument({ ops: [{ insert: '正文\n', attributes: { bold: true } }] }, null, '正文'), false);
});

test('scene locating is derived from existing lines without rewriting full script', () => {
  const content = '序言\n第一场 雨夜\n主角：你好\n2-1 内 走廊\nEXT. 夜街\n结束';
  assert.deepEqual(scriptScenes(content).map(scene => [scene.label, content.slice(scene.index).split('\n')[0]]), [
    ['第一场 雨夜', '第一场 雨夜'], ['2-1 内 走廊', '2-1 内 走廊'], ['EXT. 夜街', 'EXT. 夜街'],
  ]);
  assert.equal(scriptWordCount('中文 \n🙂'), 3);
});
