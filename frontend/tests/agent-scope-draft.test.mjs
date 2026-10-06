import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

async function moduleAt(name) {
  const source = readFileSync(new URL(`../src/features/agents/${name}.ts`, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
}
const { episodeConversationScope, conversationScopeKey, conversationMatchesScope, scopeConversation, selectScopeConversation } = await moduleAt('agent-scope');
const { readAgentDraft, saveAgentDraft, agentDraftKey } = await moduleAt('agent-draft');
const pendingModule = await moduleAt('agent-draft');

test('episode, asset and shot scopes keep stable decimal identities and separate task groups', () => {
  const episode = episodeConversationScope('source', '9007199254740993');
  assert.deepEqual(episode, { stage: 'source', subject_type: 'episode', subject_id: '9007199254740993', task_type: 'writing' });
  const asset = episodeConversationScope('assets', '20', { type: 'asset', id: '9007199254740995', label: '角色 · 林晚' });
  const other = episodeConversationScope('assets', '20', { type: 'asset', id: '9007199254740997', label: '同名角色' });
  assert.notEqual(conversationScopeKey(asset), conversationScopeKey(other));
  assert.equal(episodeConversationScope('assets', '20').task_type, 'extraction');
  assert.equal(episodeConversationScope('storyboard', '20').task_type, 'planning');
  assert.equal(episodeConversationScope('storyboard', '20', { type: 'shot', id: '41', label: '分镜 01' }).task_type, 'creation');
  assert.equal(conversationMatchesScope({ ...asset, scope_version: 0 }, asset), false);
  assert.equal(conversationMatchesScope({ ...other, scope_version: 1 }, asset), false);
  assert.equal(conversationMatchesScope({ ...asset, scope_version: 1 }, asset), true);
});

test('object URLs never inherit a stage conversation and retain each object selection separately', () => {
  const first = episodeConversationScope('assets', '20', { type: 'asset', id: '501', label: '' });
  const second = episodeConversationScope('assets', '20', { type: 'asset', id: '502', label: '' });
  assert.equal(scopeConversation('?conversation=301&conversation_assets=301', first), undefined);
  const firstSearch = selectScopeConversation('?mode=agent', first, '301');
  const secondSearch = selectScopeConversation(firstSearch.toString(), second, '302');
  assert.equal(scopeConversation(secondSearch.toString(), first), '301');
  assert.equal(scopeConversation(secondSearch.toString(), second), '302');
});

test('drafts isolate accounts and conversations and persist only text, model ID and skill references', () => {
  const map = new Map(); const storage = { getItem: key => map.get(key) ?? null, setItem: (key, value) => map.set(key, value) };
  saveAgentDraft('1', '301', { version: 1, text: 'A 的草稿', modelId: '9007199254740993', skills: [{ id: 'builtin:continuity', content_version: '1', instructions: '不应存储', url: 'signed-url' }], api_key: 'not-stored', url: 'signed-url' }, storage);
  assert.equal(readAgentDraft('1', '301', storage).text, 'A 的草稿');
  assert.equal(readAgentDraft('2', '301', storage).text, '');
  assert.equal(readAgentDraft('1', '302', storage).text, '');
  const saved = JSON.parse(map.get(agentDraftKey('1', '301')));
  assert.deepEqual(saved.skills, [{ id: 'builtin:continuity', content_version: '1' }]);
  assert.equal(saved.api_key, undefined); assert.equal(saved.url, undefined);
  storage.setItem(agentDraftKey('1', '301'), '{broken');
  assert.deepEqual(readAgentDraft('1', '301', storage), { version: 1, text: '', skills: [] });
  assert.equal(saveAgentDraft('1', '301', { version: 1, text: '仍在内存', skills: [] }, { setItem: () => { throw new Error('quota'); } }), false);
});

const pendingSend = () => ({ version: 1, key: 'original-request-key', draft: '生成雨夜站台候选', body: {
  content: '生成雨夜站台候选', model_config_id: '9007199254740993',
  expected_scope: { stage: 'source', subject_type: 'episode', subject_id: '20', task_type: 'writing' },
  attachment_ids: ['7001'], video_audio: 'visual_only', skills: [{ id: 'builtin:continuity', content_version: '1' }],
} });
const pendingStorage = () => {
  const values = new Map();
  return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
};

test('pending sends preserve the exact request independently of later drafts and account scopes', () => {
  const { saveAgentPendingSend, readAgentPendingSend, clearAgentPendingSend } = pendingModule;
  const storage = pendingStorage(); const pending = pendingSend();
  assert.equal(saveAgentPendingSend('1', '301', pending, storage), true);
  assert.equal(saveAgentPendingSend('1', '301', pending, storage), true);
  saveAgentDraft('1', '301', { version: 1, text: '后来编辑的新草稿', modelId: '72', skills: [] }, storage);
  assert.deepEqual(readAgentPendingSend('1', '301', storage), { pending, error: '' });
  assert.equal(readAgentDraft('1', '301', storage).text, '后来编辑的新草稿');
  assert.equal(readAgentPendingSend('2', '301', storage).pending, null);
  assert.equal(readAgentPendingSend('1', '302', storage).pending, null);
  assert.equal(clearAgentPendingSend('1', '301', pending.key, storage), true);
  assert.equal(readAgentPendingSend('1', '301', storage).pending, null);
  assert.equal(readAgentDraft('1', '301', storage).text, '后来编辑的新草稿');
});

test('an unresolved pending send cannot be replaced or cleared by a different request', () => {
  const { saveAgentPendingSend, readAgentPendingSend, clearAgentPendingSend } = pendingModule;
  const storage = pendingStorage(); const pending = pendingSend();
  assert.equal(saveAgentPendingSend('1', '301', pending, storage), true);
  assert.equal(saveAgentPendingSend('1', '301', { ...pending, key: 'new-request-key' }, storage), false);
  assert.equal(saveAgentPendingSend('1', '301', { ...pending, body: { ...pending.body, attachment_ids: [] } }, storage), false);
  assert.equal(clearAgentPendingSend('1', '301', 'different-request', storage), false);
  assert.deepEqual(readAgentPendingSend('1', '301', storage).pending, pending);
});

test('damaged pending records fail closed and never store credentials or temporary URLs', () => {
  const { saveAgentPendingSend, readAgentPendingSend, agentPendingSendKey } = pendingModule;
  const storage = pendingStorage(); const pending = pendingSend();
  assert.equal(saveAgentPendingSend('1', '301', { ...pending, body: { ...pending.body, api_key: 'secret', url: 'signed-url' } }, storage), false);
  assert.equal(storage.values.size, 0);
  const slot = agentPendingSendKey('1', '301');
  storage.setItem(slot, '{broken');
  const restored = readAgentPendingSend('1', '301', storage);
  assert.equal(restored.pending, null); assert.ok(restored.error);
  assert.equal(saveAgentPendingSend('1', '301', pending, storage), false);
  assert.equal(saveAgentPendingSend('1', '302', pending, { getItem: () => null, setItem: () => { throw new Error('quota'); } }), false);
});

test('pending requests retain all sixteen supported attachments without changing decimal IDs', () => {
  const { saveAgentPendingSend, readAgentPendingSend } = pendingModule;
  const storage = pendingStorage(); const pending = pendingSend();
  pending.body.attachment_ids = Array.from({ length: 16 }, (_, i) => String(9007199254740993n + BigInt(i)));
  assert.equal(saveAgentPendingSend('1', '301', pending, storage), true);
  assert.deepEqual(readAgentPendingSend('1', '301', storage).pending.body.attachment_ids, pending.body.attachment_ids);
});
