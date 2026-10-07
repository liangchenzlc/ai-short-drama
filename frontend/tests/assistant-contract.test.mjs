import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

function compile(source) {
  return ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
}
const source = readFileSync(new URL('../src/features/ai-assistant/assistant-draft.ts', import.meta.url), 'utf8');
const drafts = await import(`data:text/javascript;base64,${Buffer.from(compile(source)).toString('base64')}`);
function storage() {
  const values = new Map();
  return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}
const pending = () => ({ version: 1, key: 'original-request', draft: '分析当前作品', body: {
  content: '分析当前作品', model_config_id: '9007199254740993',
  context: { kind: 'canvas', id: 'canvas-source-key', revision: '4', include_document: false, selected: [{ kind: 'node', id: 'stable-node-uuid' }] },
  attachment_ids: ['301'], skills: [{ id: 'builtin:continuity', content_version: '1' }], video_audio: 'visual_only',
} });

test('assistant drafts retain explicit references and selection removal across refresh without secrets or URLs', () => {
  const local = storage();
  const draft = { version: 1, text: '@文字1 分析它', modelId: '9007199254740993', skills: [], contextKey: 'canvas:1', includeContext: false,
    references: [{ kind: 'node', id: 'stable-node-uuid', name: '文字1', revision: '4', url: 'temporary', api_key: 'test-secret' }], api_key: 'test-secret' };
  assert.equal(drafts.saveAssistantDraft('1', '20', draft, local), true);
  assert.deepEqual(drafts.readAssistantDraft('1', '20', local).references, [{ kind: 'node', id: 'stable-node-uuid', name: '文字1', revision: '4' }]);
  assert.equal(drafts.readAssistantDraft('1', '20', local).includeContext, false);
  assert.equal(drafts.readAssistantDraft('2', '20', local).text, '');
  assert.equal(drafts.readAssistantDraft('1', '21', local).text, '');
  assert.ok(!local.getItem(drafts.assistantDraftKey('1', '20')).includes('test-secret'));
  assert.ok(!local.getItem(drafts.assistantDraftKey('1', '20')).includes('temporary'));
});

test('uncertain messages retain frozen context and original key independently of new edits', () => {
  const local = storage(); const original = pending();
  assert.equal(drafts.saveAssistantPendingSend('1', '20', original, local), true);
  drafts.saveAssistantDraft('1', '20', { version: 1, text: '新的消息', skills: [] }, local);
  assert.deepEqual(drafts.readAssistantPendingSend('1', '20', local), { pending: original, error: '' });
  assert.equal(drafts.readAssistantPendingSend('2', '20', local).pending, null);
  assert.equal(drafts.saveAssistantPendingSend('1', '20', { ...original, key: 'new-key' }, local), false);
  assert.equal(drafts.saveAssistantPendingSend('1', '20', { ...original, body: { ...original.body, context: null } }, local), false);
  assert.equal(drafts.clearAssistantPendingSend('1', '20', 'new-key', local), false);
  assert.equal(drafts.clearAssistantPendingSend('1', '20', original.key, local), true);
  assert.equal(drafts.readAssistantDraft('1', '20', local).text, '新的消息');
});

test('ordinary chat and valid episode context can be restored', () => {
  for (const context of [null, { kind: 'episode', id: '9007199254740993', revision: '1', stage: 'assembly' }, { kind: 'episode', id: '20', revision: '2', stage: 'storyboard', storyboard_revision: '4', selected: [{ kind: 'shot', id: '21', revision: '7' }] }]) {
    const local = storage(); const request = pending(); request.body.context = context;
    assert.equal(drafts.saveAssistantPendingSend('1', '20', request, local), true);
    assert.deepEqual(drafts.readAssistantPendingSend('1', '20', local).pending, request);
  }
});

test('pure chat recovery rejects tool mode, task fields, bad identifiers and credentials', () => {
  for (const extra of [{ mode: 'generate' }, { task: { kind: 'image' } }, { expected_scope: {} }, { api_key: 'test-secret' }, { context: { kind: 'episode', id: 20, revision: '1' } }, { context: { kind: 'canvas', id: '1', revision: '1', include_document: 'false' } }]) {
    const local = storage(); const request = pending(); request.body = { ...request.body, ...extra };
    assert.equal(drafts.saveAssistantPendingSend('1', '20', request, local), false);
    assert.equal(local.values.size, 0);
  }
});

test('corrupt pending state and unavailable storage block fresh submission', () => {
  const local = storage(); local.setItem(drafts.assistantPendingSendKey('1', '20'), '{bad');
  assert.ok(drafts.readAssistantPendingSend('1', '20', local).error);
  assert.equal(drafts.saveAssistantPendingSend('1', '20', pending(), local), false);
  assert.equal(drafts.saveAssistantPendingSend('1', '21', pending(), { getItem: () => null, setItem: () => { throw new Error('quota'); } }), false);
});

test('assistant HTTP contracts use project scope, separate attachment paths and unchanged decimal IDs', async () => {
  const calls = [];
  globalThis.__assistantHttp = Object.fromEntries(['get', 'post', 'patch', 'delete'].map(method => [method, async (...args) => { calls.push({ method, args }); return { data: { items: [] } }; }]));
  const apiSource = readFileSync(new URL('../src/api/modules/assistant.ts', import.meta.url), 'utf8').replace("import { http } from '../http';", 'const http = globalThis.__assistantHttp;');
  const { assistantApi } = await import(`data:text/javascript;base64,${Buffer.from(compile(apiSource)).toString('base64')}`);
  await assistantApi.conversations('9007199254740993', 20, true, undefined, '名称');
  assert.equal(calls[0].args[0], '/assistant/conversations');
  assert.equal(calls[0].args[1].params.project_id, '9007199254740993');
  assert.equal(calls[0].args[1].params.episode_id, undefined);
  const original = pending(); await assistantApi.send('9007199254740995', original.body, original.key);
  assert.equal(calls[1].args[0], '/assistant/conversations/9007199254740995/messages');
  assert.deepEqual(calls[1].args[1], original.body);
  assert.equal(calls[1].args[2].headers['Idempotency-Key'], original.key);
  await assistantApi.referenceAttachment('20', 'asset', '9007199254740997', 'reference-key');
  assert.equal(calls[2].args[0], '/assistant/conversations/20/attachments/references');
  assert.equal(calls[2].args[1].source_id, '9007199254740997');
  delete globalThis.__assistantHttp;
});

test('common assistant has no canvas stores, routing or execution cards and keeps safe Markdown', () => {
  const dir = new URL('../src/features/ai-assistant/', import.meta.url);
  const code = readdirSync(dir).filter(name => name.endsWith('.tsx') || name.endsWith('.ts')).map(name => readFileSync(new URL(name, dir), 'utf8')).join('\n');
  assert.doesNotMatch(code, /from\s+['"](?:@\/|[^'"]*canvas\/|zustand|react-router)/);
  assert.doesNotMatch(code, /rehypeRaw|dangerouslySetInnerHTML|onRunProposal|\.review\(|\.continue\(/);
  assert.match(code, /ReactMarkdown remarkPlugins=\{\[remarkGfm\]\}/);
  assert.match(code, /\/assistant\/conversations/);
  assert.match(code, /作品上下文已截取/);
});
