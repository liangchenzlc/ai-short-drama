import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/agents/agent-navigation.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { withEpisodeView, isAgentRunActive, agentRunLabel } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('project assistant conversation survives stage navigation with decimal string identifiers', () => {
  assert.equal(withEpisodeView('/projects/10/episodes/20/assets', '?assistant=open&conversation=9007199254740993'), '/projects/10/episodes/20/assets?assistant=open&conversation=9007199254740993');
  assert.equal(withEpisodeView('/source', ''), '/source');
});

test('backend waiting states keep the task active while terminal states permit conversation archive', () => {
  for (const status of ['queued', 'running', 'waiting_generation', 'waiting_review']) assert.equal(isAgentRunActive(status), true);
  for (const status of ['succeeded', 'failed', 'cancelled', null]) assert.equal(isAgentRunActive(status), false);
  assert.equal(agentRunLabel('waiting_generation'), '等待生成结果');
  assert.equal(agentRunLabel('waiting_review'), '等待确认');
});
