import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

function compile(source) {
  return ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
}
const source = readFileSync(new URL('../../src/features/ai-assistant/assistant-skill-token.ts', import.meta.url), 'utf8');
const tokens = await import(`data:text/javascript;base64,${Buffer.from(compile(source)).toString('base64')}`);

const novel = { id: 'skill-1', name: '剧本创作', content_version: '2' };
const unnamed = { id: 'skill-2', content_version: '1' };

test('selected skills keep an explicit /名称 token in the prompt', () => {
  assert.equal(tokens.skillToken(novel), '/剧本创作');
  assert.equal(tokens.skillToken(unnamed), '/skill-2');
  assert.equal(tokens.appendSkillToken('', '/剧本创作'), '/剧本创作 ');
  assert.equal(tokens.appendSkillToken('写第一集', '/剧本创作'), '写第一集 /剧本创作 ');
  assert.equal(tokens.appendSkillToken('写第一集\n', '/剧本创作'), '写第一集\n/剧本创作 ');
});

test('removing a skill only drops its own token and the adjacent separator', () => {
  assert.equal(tokens.removeSkillToken('写第一集 /剧本创作 开场', '/剧本创作'), '写第一集 开场');
  assert.equal(tokens.removeSkillToken('/剧本创作 开场', '/剧本创作'), '开场');
  assert.equal(tokens.removeSkillToken('写第一集', '/剧本创作'), '写第一集');
});

test('restored drafts regain their tokens and stray text never keeps a skill loaded', () => {
  assert.equal(tokens.ensureSkillTokens('写第一集', [novel]), '写第一集 /剧本创作 ');
  assert.equal(tokens.ensureSkillTokens('写第一集 /剧本创作 ', [novel]), '写第一集 /剧本创作 ');
  assert.deepEqual(tokens.retainTokenizedSkills('写第一集 /剧本创作 ', [novel, unnamed]), [novel]);
  assert.deepEqual(tokens.retainTokenizedSkills('写第一集', [novel]), []);
});
