import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../../src/features/creation/writing/editor/script-document.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { scriptScenes, scriptWordCount } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('scene locating is derived from existing lines without rewriting full script', () => {
  const content = '序言\n第一场 雨夜\n主角：你好\n2-1 内 走廊\nEXT. 夜街\n结束';
  assert.deepEqual(scriptScenes(content).map(scene => [scene.label, content.slice(scene.index).split('\n')[0]]), [
    ['第一场 雨夜', '第一场 雨夜'], ['2-1 内 走廊', '2-1 内 走廊'], ['EXT. 夜街', 'EXT. 夜街'],
  ]);
  assert.equal(scriptWordCount('中文 \n🙂'), 3);
});
