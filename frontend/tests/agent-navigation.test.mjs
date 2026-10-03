import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/agents/agent-navigation.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { creationMode, withEpisodeView, changeCreationMode, changeConversation, isAgentRunActive, agentRunLabel, stageConversation, selectStageConversation, withStageConversation } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('each workflow remembers its own conversation and preserves decimal string identifiers', () => {
  const source = selectStageConversation('?mode=agent', 'source', '9007199254740993');
  const assetsUrl = withStageConversation('/assets', source.toString(), 'source', 'assets');
  const assetsSearch = new URL(assetsUrl, 'https://fixture.invalid').search;
  assert.equal(stageConversation(assetsSearch, 'assets'), undefined);
  const assets = selectStageConversation(assetsSearch, 'assets', '302');
  const back = new URL(withStageConversation('/source', assets.toString(), 'assets', 'source'), 'https://fixture.invalid').search;
  assert.equal(stageConversation(back, 'source'), '9007199254740993');
  assert.equal(stageConversation(back, 'assets'), '302');
  assert.equal(stageConversation('?mode=agent&conversation=301', 'source'), '301');
  assert.equal(stageConversation('?conversation=301&conversation_stage=source', 'storyboard'), undefined);
});

test('old episode links remain prompt mode and an explicit agent link survives stage navigation', () => {
  assert.equal(creationMode(''), 'prompt');
  assert.equal(creationMode('?conversation=301'), 'prompt');
  assert.equal(creationMode('?mode=unknown'), 'prompt');
  const link = withEpisodeView('/projects/10/episodes/20/assets', '?mode=agent&conversation=301&scope=shot');
  assert.equal(link, '/projects/10/episodes/20/assets?mode=agent&conversation=301&scope=shot');
  assert.equal(creationMode(new URL(link, 'https://fixture.invalid').search), 'agent');
});

test('switching to prompt preserves the private conversation selection for a return to agent', () => {
  const prompt = changeCreationMode('?mode=agent&conversation=301', 'prompt');
  assert.equal(creationMode(prompt.toString()), 'prompt');
  assert.equal(prompt.get('conversation'), '301');
  const agent = changeCreationMode(prompt.toString(), 'agent');
  assert.equal(agent.get('conversation'), '301');
  assert.equal(agent.get('mode'), 'agent');
});

test('selecting and clearing a conversation keep the episode view without creating another conversation', () => {
  const selected = changeConversation('?mode=prompt&filter=未完成', '9007199254740993');
  assert.equal(selected.get('conversation'), '9007199254740993');
  assert.equal(selected.get('filter'), '未完成');
  const cleared = changeConversation(selected.toString());
  assert.equal(cleared.has('conversation'), false);
  assert.equal(cleared.get('mode'), 'agent');
});

test('backend waiting states keep the task active while terminal states permit conversation archive', () => {
  for (const status of ['queued', 'running', 'waiting_generation', 'waiting_review']) assert.equal(isAgentRunActive(status), true);
  for (const status of ['succeeded', 'failed', 'cancelled', null]) assert.equal(isAgentRunActive(status), false);
  assert.equal(agentRunLabel('waiting_generation'), '等待生成结果');
  assert.equal(agentRunLabel('waiting_review'), '等待确认');
});
