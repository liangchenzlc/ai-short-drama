import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

function compile(source) {
  return ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
}
const source = readFileSync(new URL('../../src/features/ai-assistant/prompt-tokens.ts', import.meta.url), 'utf8');
const tokens = await import(`data:text/javascript;base64,${Buffer.from(compile(source)).toString('base64')}`);

const words = ['/剧本创作', '@截图参考'];

test('highlight parts keep the raw text and only mark known tokens', () => {
  assert.deepEqual(tokens.splitHighlightedText('写第一集 /剧本创作 开场', words), [
    { text: '写第一集 ', token: false },
    { text: '/剧本创作', token: true },
    { text: ' 开场', token: false },
  ]);
  assert.deepEqual(tokens.splitHighlightedText('写第一集', words), [{ text: '写第一集', token: false }]);
  assert.deepEqual(tokens.splitHighlightedText('', words), []);
  assert.deepEqual(tokens.splitHighlightedText('前 /剧本创作 @截图参考 后', words), [
    { text: '前 ', token: false }, { text: '/剧本创作', token: true }, { text: ' ', token: false },
    { text: '@截图参考', token: true }, { text: ' 后', token: false },
  ]);
});

test('backspace at a token boundary removes the whole token and one separator', () => {
  const value = '写第一集 /剧本创作 开场';
  const end = value.indexOf(' 开场');
  assert.deepEqual(tokens.tokenDeletionRange(value, words, end, 'backward'), { start: 5, end: end + 1 });
  assert.deepEqual(tokens.tokenDeletionRange(value, words, end - 2, 'backward'), { start: 5, end: end + 1 });
  assert.equal(tokens.tokenDeletionRange(value, words, 3, 'backward'), null);
  assert.deepEqual(tokens.tokenDeletionRange('/剧本创作 开场', words, 5, 'backward'), { start: 0, end: 6 });
});

test('delete at a token boundary removes the token and keeps surrounding spacing', () => {
  const value = '写第一集 /剧本创作 开场';
  assert.deepEqual(tokens.tokenDeletionRange(value, words, 5, 'forward'), { start: 5, end: 11 });
  assert.deepEqual(tokens.tokenDeletionRange(value, words, 7, 'forward'), { start: 5, end: 11 });
  assert.equal(tokens.tokenDeletionRange(value, words, 12, 'forward'), null);
  assert.deepEqual(tokens.tokenDeletionRange('/剧本创作开场', words, 0, 'forward'), { start: 0, end: 5 });
});
