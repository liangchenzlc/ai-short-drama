import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

function compile(source) {
  return ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
}
const source = readFileSync(new URL('../../src/features/ai-assistant/assistant-draft.ts', import.meta.url), 'utf8');
const drafts = await import(`data:text/javascript;base64,${Buffer.from(compile(source)).toString('base64')}`);
const historyDraftSource = readFileSync(new URL('../../src/features/ai-assistant/assistant-history-draft.ts', import.meta.url), 'utf8');
const historyDrafts = await import(`data:text/javascript;base64,${Buffer.from(compile(historyDraftSource)).toString('base64')}`);
const undoSource = readFileSync(new URL('../../src/features/ai-assistant/assistant-undo-draft.ts', import.meta.url), 'utf8');
const undoDrafts = await import(`data:text/javascript;base64,${Buffer.from(compile(undoSource)).toString('base64')}`);
const memorySource = readFileSync(new URL('../../src/features/ai-assistant/assistant-memory-draft.ts', import.meta.url), 'utf8');
const memoryDrafts = await import(`data:text/javascript;base64,${Buffer.from(compile(memorySource)).toString('base64')}`);
const linkSource = readFileSync(new URL('../../src/features/ai-assistant/assistant-source-link.ts', import.meta.url), 'utf8');
const { assistantSourceLink } = await import(`data:text/javascript;base64,${Buffer.from(compile(linkSource)).toString('base64')}`);
function storage() {
  const values = new Map();
  return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}

test('uncertain batch undo retains its exact operation, preview and explicit selection across accounts', () => {
  const local = storage();
  const original = { version: 1, operationId: '9007199254740993', key: 'original-undo', pending: true, body: { preview_hash: 'a'.repeat(64), selected_keys: ['asset:9007199254740995'] } };
  assert.equal(undoDrafts.saveUndoDraft('1', '3', original, local), true);
  assert.deepEqual(undoDrafts.readUndoDraft('1', '3', local).draft, original);
  assert.equal(undoDrafts.readUndoDraft('2', '3', local).draft, null);
  for (const change of [{ key: 'replacement' }, { operationId: '4' }, { body: { ...original.body, preview_hash: 'b'.repeat(64) } }, { body: { ...original.body, selected_keys: ['shot:6'] } }]) {
    assert.equal(undoDrafts.saveUndoDraft('1', '3', { ...original, ...change }, local), false);
  }
  assert.equal(undoDrafts.clearUndoDraft('1', '3', local, 'replacement'), false);
  assert.equal(undoDrafts.clearUndoDraft('1', '3', local, original.key), true);
});

test('batch undo cannot overwrite damaged records or proceed without reliable local storage', () => {
  const local = storage(); local.values.set('short-drama:user:1:assistant-undo:v1:3', '{damaged');
  const draft = { version: 1, operationId: '5', key: 'new', pending: false, body: { preview_hash: 'a'.repeat(64), selected_keys: ['asset:6'] } };
  assert.ok(undoDrafts.readUndoDraft('1', '3', local).error);
  assert.equal(undoDrafts.saveUndoDraft('1', '3', draft, local), false);
  assert.equal(local.values.get('short-drama:user:1:assistant-undo:v1:3'), '{damaged');
  assert.equal(undoDrafts.clearUndoDraft('1', '3', local), true);
  assert.equal(undoDrafts.saveUndoDraft('1', '3', { ...draft, body: { ...draft.body, selected_keys: ['asset:6', 'asset:6'] } }, local), false);
  assert.equal(undoDrafts.saveUndoDraft('1', '3', draft, { ...local, setItem() { throw new Error('blocked'); } }), false);
});
const pending = () => ({ version: 1, key: 'original-request', draft: '分析当前作品', body: {
  content: '分析当前作品', model_config_id: '9007199254740993',
  context: { kind: 'canvas', id: 'canvas-source-key', revision: '4', include_document: false, selected: [{ kind: 'node', id: 'stable-node-uuid' }] },
  attachment_ids: ['301'], skills: [{ id: 'builtin:continuity', content_version: '1' }], video_audio: 'visual_only',
} });

test('memory drafts preserve the original uncertain body and remain account scoped', () => {
  const local = storage();
  const draft = { version: 1, item: null, key: 'original-memory', pending: true, values: {
    scope_type: 'user', kind: 'preference', memory_key: 'dialogue', title: '对白', content: '对白要短', aliases: [], tags: [], pinned: false,
  } };
  assert.equal(memoryDrafts.saveMemoryDraft('1', draft, local), true);
  assert.deepEqual(memoryDrafts.readMemoryDraft('1', local).draft, draft);
  assert.equal(memoryDrafts.readMemoryDraft('2', local).draft, null);
  assert.equal(memoryDrafts.saveMemoryDraft('1', { ...draft, key: 'replacement' }, local), false);
  assert.equal(memoryDrafts.saveMemoryDraft('1', { ...draft, values: { ...draft.values, content: '换了正文' } }, local), false);
  assert.equal(memoryDrafts.saveMemoryDraft('1', { ...draft, pending: false }, local), true);
  assert.equal(memoryDrafts.clearMemoryDraft('1', local), true);
});

test('a damaged memory draft is retained until it is explicitly cleared', () => {
  const local = storage();
  local.setItem('assistant-memory-draft:v1:1', '{broken');
  assert.ok(memoryDrafts.readMemoryDraft('1', local).error);
  assert.equal(memoryDrafts.saveMemoryDraft('1', { version: 1 }, local), false);
  assert.equal(local.getItem('assistant-memory-draft:v1:1'), '{broken');
  assert.equal(memoryDrafts.clearMemoryDraft('1', local), true);
});

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

test('explicitly clearing task methods survives refresh and stays account scoped', () => {
  const local = storage();
  const draft = { version: 1, text: '继续但不再使用手动方法', taskId: '9007199254740993', skills: [], skillsDirty: true };
  assert.equal(drafts.saveAssistantDraft('1', '20', draft, local), true);
  const restored = drafts.readAssistantDraft('1', '20', local);
  assert.deepEqual(restored.skills, []);
  assert.equal(restored.skillsDirty, true);
  assert.equal(restored.taskId, draft.taskId);
  assert.notEqual(drafts.readAssistantDraft('2', '20', local).skillsDirty, true);
  const request = { version: 1, key: 'clear-methods', draft: draft.text, body: { content: draft.text, task_id: draft.taskId, skills: [] } };
  assert.equal(drafts.saveAssistantPendingSend('1', '20', request, local), true);
  assert.deepEqual(drafts.readAssistantPendingSend('1', '20', local).pending, request);
});

test('pending sends preserve real builtin Skill identities and exact immutable versions', () => {
  for (const id of ['novel.v1', 'script.v1', 'extract.v1', 'storyboard.v1', 'asset_patch.v1', 'shot_patch.v1', 'image.v1', 'video.v1', 'builtin:continuity', '9007199254740993']) {
    const local = storage(); const request = pending();
    request.body.skills = [{ id, content_version: '9007199254740995' }];
    assert.equal(drafts.saveAssistantPendingSend('1', '20', request, local), true, id);
    assert.deepEqual(drafts.readAssistantPendingSend('1', '20', local).pending, request);
    assert.equal(drafts.saveAssistantPendingSend('1', '20', { ...request, body: { ...request.body, skills: [{ id, content_version: '9007199254740996' }] } }, local), false);
  }
  for (const id of ['../script.v1', 'script.v0', 'script.v1?token=secret', 'x'.repeat(81), '']) {
    const local = storage(); const request = pending(); request.body.skills = [{ id, content_version: '1' }];
    assert.equal(drafts.saveAssistantPendingSend('1', '20', request, local), false, id);
    assert.equal(local.values.size, 0);
  }
});

test('multi-source selection drafts survive page changes without body or credentials', () => {
  const local = storage();
  const option = { project_id: '9007199254740993', source_kind: 'canvas', source_id: 'canvas-b', kind: 'node', id: 'same-key', revision: '7', content_revision: '4', checksum: 'a'.repeat(64), name: '跨页节点', project_name: '另一作品', source_name: '画布二', content: '正文不能进入引用草稿', api_key: 'secret' };
  const draft = { version: 1, text: '继续讨论', skills: [], contextKey: 'previous-page', subjectDraft: { taskId: '9007199254740995', expectedInputRevision: '3', retainKeys: ['subject:' + '1'.repeat(64)], added: [option] } };
  assert.equal(drafts.saveAssistantDraft('1', '20', draft, local), true);
  const restored = drafts.readAssistantDraft('1', '20', local).subjectDraft;
  assert.equal(restored.added[0].project_id, option.project_id);
  assert.equal(restored.added[0].content_revision, '4');
  assert.equal(restored.expectedInputRevision, '3');
  assert.ok(!local.getItem(drafts.assistantDraftKey('1', '20')).includes('正文不能'));
  assert.ok(!local.getItem(drafts.assistantDraftKey('1', '20')).includes('secret'));
});

test('uncertain multi-source requests preserve the original selection revision and reject expanded scope', () => {
  const subject = { project_id: '9007199254740993', source_kind: 'project', source_id: '9007199254740993', kind: 'image_slot', id: '9007199254740997', revision: '4' };
  const request = { version: 1, key: 'multi-source', draft: '用这些参考', body: { content: '用这些参考', task_id: '9007199254740999', subject_selection: { expected_input_revision: '3', retain_keys: ['subject:' + 'a'.repeat(64)], subjects: [subject] } } };
  const local = storage();
  assert.equal(drafts.saveAssistantPendingSend('1', '20', request, local), true);
  assert.deepEqual(drafts.readAssistantPendingSend('1', '20', local).pending, request);
  assert.equal(drafts.saveAssistantPendingSend('1', '20', { ...request, body: { ...request.body, subject_selection: { ...request.body.subject_selection, expected_input_revision: '4' } } }, local), false);
  for (const changed of [{ ...subject, project_id: '2' }, { ...subject, kind: 'node' }, { ...subject, content: '伪造正文' }, { ...subject, revision: 4 }]) {
    assert.equal(drafts.saveAssistantPendingSend('1', 'other', { ...request, body: { ...request.body, subject_selection: { ...request.body.subject_selection, subjects: [changed] } } }, local), false);
  }
  assert.equal(drafts.saveAssistantPendingSend('1', 'other', { ...request, body: { ...request.body, context: null } }, local), false);
});

test('attachment removal drafts and uncertain requests preserve their task and exact input revision', () => {
  const local = storage();
  const draft = { version: 1, text: '后续移除附件', skills: [], taskId: '9007199254740993', settingsDirty: true,
    executionTarget: { mode: 'infinite_canvas', project_id: '9007199254740995', canvas_id: 'canvas-b' },
    memoryProjectIds: ['9007199254740997'], attachmentDraft: { taskId: '9007199254740993', expectedInputRevision: '4', retainIds: ['9007199254740999'], text_content: '不能存正文' } };
  assert.equal(drafts.saveAssistantDraft('1', '20', draft, local), true);
  const restored = drafts.readAssistantDraft('1', '20', local);
  assert.deepEqual(restored.attachmentDraft, { taskId: draft.taskId, expectedInputRevision: '4', retainIds: ['9007199254740999'] });
  assert.equal(restored.settingsDirty, true);
  assert.deepEqual(restored.executionTarget, draft.executionTarget);
  assert.deepEqual(restored.memoryProjectIds, draft.memoryProjectIds);
  assert.ok(!local.getItem(drafts.assistantDraftKey('1', '20')).includes('不能存正文'));
  const request = { version: 1, key: 'remove-attachment', draft: draft.text, body: { content: draft.text, task_id: draft.taskId,
    attachment_selection: { expected_input_revision: '4', retain_ids: [] } } };
  assert.equal(drafts.saveAssistantPendingSend('1', '20', request, local), true);
  assert.deepEqual(drafts.readAssistantPendingSend('1', '20', local).pending, request);
  assert.equal(drafts.saveAssistantPendingSend('1', '20', { ...request, body: { ...request.body, attachment_selection: { expected_input_revision: '5', retain_ids: [] } } }, local), false);
  for (const selection of [null, { retain_ids: [] }, { expected_input_revision: '0', retain_ids: ['1', '1'] }, { expected_input_revision: '0', retain_ids: ['node:1'] }, { expected_input_revision: '0', retain_ids: [], text_content: '伪造正文' }]) {
    assert.equal(drafts.saveAssistantPendingSend('1', 'other', { ...request, body: { ...request.body, attachment_selection: selection } }, local), false);
  }
});

test('source links locate the actual owner, image group, slot and canvas node without number coercion', () => {
  const subject = { project_id: '9007199254740993', source_kind: 'project', source_id: '9007199254740993', kind: 'asset', id: '9007199254740995' };
  assert.equal(assistantSourceLink(subject), '/projects/9007199254740993/assets?asset=9007199254740995');
  assert.equal(assistantSourceLink({ ...subject, kind: 'shot' }), '/projects/9007199254740993/storyboard?shot=9007199254740995');
  assert.equal(assistantSourceLink({ ...subject, kind: 'image_slot', id: '99', source_location: { project_id: subject.project_id, kind: 'shot', id: '9007199254740997', image_group_id: '9007199254740999', image_slot_id: '99' } }), '/projects/9007199254740993/storyboard?shot=9007199254740997&source_image_group=9007199254740999&source_image_slot=99');
  assert.equal(assistantSourceLink({ ...subject, kind: 'node', id: 'node #1', source_id: 'canvas/b' }), '/canvas-app/canvas/canvas%2Fb?node=node%20%231');
  assert.equal(assistantSourceLink({ ...subject, kind: 'image_group' }), null);
  assert.equal(assistantSourceLink({ ...subject, kind: 'sound_line', id: 'line_a-9' }), '/projects/9007199254740993/assembly?source_line=line_a-9');
});

test('saved sound line requests keep string identities and reject injected bodies', () => {
  const local = storage(); const request = pending();
  const reference = { project_id: '9007199254740993', source_kind: 'project', source_id: '9007199254740993', kind: 'sound_line', id: 'line_a-9', revision: '9007199254740995' };
  request.body = { content: '为已选台词生成配音', model_config_id: '71', subject_selection: { expected_input_revision: '0', retain_keys: [], subjects: [reference] } };
  assert.equal(drafts.saveAssistantPendingSend('1', 'other', request, local), true);
  assert.deepEqual(drafts.readAssistantPendingSend('1', 'other', local).pending.body.subject_selection.subjects, [reference]);
  for (const patch of [{ id: '../line' }, { text: '伪造正文' }, { voice: '伪造音色' }]) {
    assert.equal(drafts.saveAssistantPendingSend('1', 'other', { ...request, body: { ...request.body, subject_selection: { ...request.body.subject_selection, subjects: [{ ...reference, ...patch }] } } }, local), false);
  }
});

test('control and deletion recovery preserve the original payload, account and request identity', () => {
  const local = storage();
  for (const request of [
    { version: 1, kind: 'control', targetId: '9007199254740993', key: 'resume-original', body: { kind: 'resume', expected_control_seq: '0', content: '按修正继续' } },
    { version: 1, kind: 'delete', targetId: '9007199254740993', key: 'delete-original', body: { row_version: '7', clear_derived_memory: true } },
  ]) {
    assert.equal(drafts.saveAssistantPendingAction('1', '20', request, local), true);
    assert.deepEqual(drafts.readAssistantPendingAction('1', '20', request.kind, local), { pending: request, error: '' });
    assert.equal(drafts.readAssistantPendingAction('2', '20', request.kind, local).pending, null);
    assert.equal(drafts.readAssistantPendingAction('1', '21', request.kind, local).pending, null);
    assert.equal(drafts.saveAssistantPendingAction('1', '20', { ...request, body: { ...request.body, content: '新请求' } }, local), false);
    assert.equal(drafts.clearAssistantPendingAction('1', '20', request.kind, 'wrong-key', local), false);
    assert.equal(drafts.clearAssistantPendingAction('1', '20', request.kind, request.key, local), true);
  }
});

test('malformed control recovery is retained and prevents replacing the uncertain request', () => {
  const local = storage();
  local.setItem(drafts.assistantPendingActionKey('1', '20', 'control'), '{broken');
  assert.ok(drafts.readAssistantPendingAction('1', '20', 'control', local).error);
  const valid = { version: 1, kind: 'control', targetId: '30', key: 'control', body: { kind: 'stop' } };
  assert.equal(drafts.saveAssistantPendingAction('1', '20', valid, local), false);
  assert.equal(drafts.clearAssistantPendingAction('1', '20', 'control', valid.key, local), false);
  assert.equal(drafts.saveAssistantPendingAction('1', '21', { ...valid, body: { kind: 'resume' } }, local), false);
  assert.equal(drafts.saveAssistantPendingAction('1', '21', { ...valid, body: { kind: 'redirect', content: ' ' } }, local), false);
});

test('uncertain deletion retains exactly the selected memory identities and versions', () => {
  const local = storage();
  const request = { version: 1, kind: 'delete', targetId: '9007199254740993', key: 'delete-memory', body: {
    row_version: '3', clear_derived_memory: true, memory_selections: [{ id: '9007199254740995', content_version: '7' }],
  } };
  assert.equal(drafts.saveAssistantPendingAction('1', 'history', request, local), true);
  assert.deepEqual(drafts.readAssistantPendingAction('1', 'history', 'delete', local).pending, request);
  for (const change of [
    { clear_derived_memory: false },
    { memory_selections: [{ id: 9007199254740995, content_version: '7' }] },
    { memory_selections: [request.body.memory_selections[0], request.body.memory_selections[0]] },
    { memory_selections: [{ id: '4', content_version: '7', owner_user_id: '2' }] },
  ]) assert.equal(drafts.saveAssistantPendingAction('1', 'other', { ...request, body: { ...request.body, ...change } }, local), false);
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
  const apiSource = readFileSync(new URL('../../src/api/modules/assistant.ts', import.meta.url), 'utf8').replace("import { http } from '../http';", 'const http = globalThis.__assistantHttp;');
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

test('shared assistant keeps package boundaries, durable traces and safe Markdown', () => {
  const dir = new URL('../../src/features/ai-assistant/', import.meta.url);
  const code = readdirSync(dir).filter(name => name.endsWith('.tsx') || name.endsWith('.ts')).map(name => readFileSync(new URL(name, dir), 'utf8')).join('\n');
  assert.doesNotMatch(code, /from\s+['"](?:@\/|[^'"]*canvas\/|zustand|react-router)/);
  assert.doesNotMatch(code, /rehypeRaw|dangerouslySetInnerHTML|onRunProposal|\.review\(|\.continue\(/);
  assert.match(code, /ReactMarkdown remarkPlugins=\{\[remarkGfm\]\}/);
  assert.match(code, /\/assistant\/global\/conversations/);
  assert.match(code, /ThoughtChain/);
  assert.match(code, /作品上下文已截取/);
});

const projectionCode = readFileSync(new URL('../../src/features/ai-assistant/assistant-task-state.ts', import.meta.url), 'utf8');
const projection = await import(`data:text/javascript;base64,${Buffer.from(compile(projectionCode)).toString('base64')}`);

test('discussion revisions retain frozen original requests across refresh and stay account scoped', () => {
  const local = storage();
  const draft = { version: 1, messageId: '9007199254740993', key: 'original-revision', pending: false,
    body: { kind: 'edit', row_version: '9007199254740995', content: '修订原讨论' } };
  assert.equal(historyDrafts.saveDiscussionDraft('1', '3', draft, local), true);
  const pending = { ...draft, pending: true };
  assert.equal(historyDrafts.saveDiscussionDraft('1', '3', pending, local), true);
  assert.deepEqual(historyDrafts.readDiscussionDraft('1', '3', local).draft, pending);
  assert.equal(historyDrafts.readDiscussionDraft('2', '3', local).draft, null);
  assert.equal(historyDrafts.readDiscussionDraft('1', '4', local).draft, null);
  for (const change of [{ key: 'replacement' }, { messageId: '5' }, { body: { ...draft.body, content: '迟到改写' } }]) {
    assert.equal(historyDrafts.saveDiscussionDraft('1', '3', { ...pending, ...change }, local), false);
  }
  assert.equal(historyDrafts.clearDiscussionDraft('1', '3', local, 'wrong-request'), false);
  assert.equal(historyDrafts.saveDiscussionDraft('1', '3', { ...pending, pending: false }, local), true);
  assert.equal(historyDrafts.clearDiscussionDraft('1', '3', local, pending.key), true);
});

test('discussion revision corruption is preserved and cannot be silently replaced', () => {
  const local = storage();
  const draft = { version: 1, messageId: '5', key: 'revision', pending: false, body: { kind: 'regenerate', row_version: '1' } };
  assert.equal(historyDrafts.saveDiscussionDraft('1', '3', draft, local), true);
  const key = [...local.values.keys()][0]; local.setItem(key, '{broken');
  assert.ok(historyDrafts.readDiscussionDraft('1', '3', local).error);
  assert.equal(historyDrafts.saveDiscussionDraft('1', '3', draft, local), false);
  assert.equal(historyDrafts.clearDiscussionDraft('1', '3', local, draft.key), false);
  assert.equal(historyDrafts.clearDiscussionDraft('1', '3', local), true);
  assert.equal(historyDrafts.saveDiscussionDraft('1', '3', { ...draft, body: { ...draft.body, content: '隐式编辑' } }, local), false);
  assert.equal(historyDrafts.saveDiscussionDraft('1', '3', { ...draft, body: { ...draft.body, execution_target: 'forged' } }, local), false);
});

test('late message pages cannot replace revised text and preserve exact decimal versions', () => {
  const current = { id: '9007199254740993', seq: 1, role: 'user', content: '新修订', row_version: '9007199254740995' };
  let messages = projection.mergeAssistantMessages([current], [{ ...current, content: '旧文本', row_version: '9007199254740994' }]);
  assert.equal(messages[0].content, '新修订');
  messages = projection.mergeAssistantMessages(messages, [{ id: '5', seq: 2, role: 'assistant', content: '旧协议回复' }]);
  assert.equal(messages.length, 2);
  messages = projection.mergeAssistantMessages(messages, [{ ...current, row_version: '9007199254740996', content: '更新后的消息' }]);
  assert.equal(messages[0].content, '更新后的消息');
});
const op = (state_version = '1', outcome = 'running') => ({ id: '9007199254740999', task_id: '5', run_id: '7', call_id: '8', tool_name: 'describe_creation_target', tool_version: '1', plan_revision: '1', state_version, phase: outcome === 'running' ? 'tool_running' : 'terminal', outcome, result_refs: [], error: null, created_at: '2026-10-21', finished_at: null });
const snapshot = (cursor, operations = []) => ({ conversation_id: '3', snapshot_cursor: cursor, tasks: [], operations });
const step = (seq, event_type, trace_version, outcome) => ({ seq, event_type, run_id: '7', created_at: '2026-10-21', payload: { operation_id: op().id, call_id: '8', task_id: '5', tool_name: 'describe_creation_target', trace_version, phase: outcome === 'succeeded' ? 'terminal' : 'tool_running', outcome } });

test('undo receipts survive duplicate events, stale pages and terminal operation replay', () => {
  const receipt = { id: '9007199254741001', operation_id: op().id, selected_keys: ['asset:17'], result_refs: [{ kind: 'asset', id: '17', status: 'deleted' }], created_at: '2026-10-22' };
  const original = op('3', 'succeeded');
  let state = projection.mergeTaskSnapshot(projection.emptyTaskProjection('3'), snapshot(10, [original]));
  const event = { seq: 11, event_type: 'operation.undo.completed', payload: { operation_id: original.id, receipt } };
  state = projection.applyTaskEvent(state, event);
  state = projection.applyTaskEvent(state, event);
  state = projection.mergeTaskSnapshot(state, snapshot(12, [original]));
  state = projection.applyTaskEvent(state, step(13, 'step.finished', '3', 'succeeded'));
  assert.deepEqual(state.operations[original.id].undo_receipts, [receipt]);
  assert.deepEqual(state.operations[original.id].result_refs, original.result_refs);
  const invalid = projection.applyTaskEvent(state, { ...event, seq: 14, payload: { ...event.payload, receipt: { ...receipt, operation_id: 'unrelated' } } });
  assert.equal(invalid, state);
});

test('trace replay and out-of-order updates never duplicate or rewind a settled operation', () => {
  let state = projection.emptyTaskProjection('3');
  state = projection.mergeTaskSnapshot(state, snapshot(10, [op('2', 'succeeded')]));
  state = projection.applyTaskEvent(state, step(9, 'step.started', '1', 'running'));
  state = projection.applyTaskEvent(state, step(11, 'step.started', '1', 'running'));
  state = projection.mergeTaskSnapshot(state, snapshot(12, [op('1', 'running')]));
  assert.equal(Object.keys(state.operations).length, 1);
  assert.equal(state.operations[op().id].outcome, 'succeeded');
  assert.equal(state.operations[op().id].state_version, '2');
  assert.equal(projection.mergeTaskSnapshot(state, snapshot(8, [op('9', 'failed')])), state);
});

test('step events preserve frozen method participation and reject malformed method metadata', () => {
  const method = { activation_id: '9007199254740993', skill_id: 'script.v1', name: '对白方法', content_version: '9007199254740995', package_checksum: 'a'.repeat(64), source: 'manual', purpose: '用户指定', active: true, plan_revision: '1', created_at: '2026-10-21', issue: null };
  let state = projection.emptyTaskProjection('3');
  const started = step(1, 'step.started', '1', 'running');
  started.payload.skill_activations = [method, null, { ...method, content_version: 2 }, { ...method, source: 'other' }];
  state = projection.applyTaskEvent(state, started);
  assert.deepEqual(state.operations[op().id].skill_activations, [method]);
  state = projection.applyTaskEvent(state, step(2, 'step.waiting_media', '2', 'waiting_media'));
  assert.deepEqual(state.operations[op().id].skill_activations, [method]);
  state = projection.applyTaskEvent(state, step(3, 'step.finished', '3', 'succeeded'));
  assert.deepEqual(state.operations[op().id].skill_activations, [method]);
  const stale = step(4, 'step.started', '1', 'running'); stale.payload.skill_activations = [];
  state = projection.applyTaskEvent(state, stale);
  assert.deepEqual(state.operations[op().id].skill_activations, [method]);
});

test('deletion tombstone rejects delayed snapshots and other conversations cannot merge', () => {
  let state = projection.mergeTaskSnapshot(projection.emptyTaskProjection('3'), snapshot(1, [op()]));
  assert.equal(projection.mergeTaskSnapshot(state, { ...snapshot(2), conversation_id: '4' }), state);
  state = projection.applyTaskEvent(state, { seq: 2, event_type: 'conversation.deleted', payload: {} });
  assert.equal(state.deleted, true); assert.deepEqual(state.operations, {});
  assert.equal(projection.mergeTaskSnapshot(state, snapshot(3, [op()])), state);
});

test('older task pages merge their entities without lowering the live snapshot watermark', () => {
  const newer = { id: '9', row_version: '4', status: 'stopped' };
  const earlier = { id: '3', row_version: '1', status: 'succeeded' };
  let state = projection.mergeTaskSnapshot(projection.emptyTaskProjection('3'), { ...snapshot(20), tasks: [newer] });
  state = projection.mergeTaskSnapshot(state, { ...snapshot(18), tasks: [earlier, { ...newer, row_version: '2', status: 'running' }] }, { historical: true });
  assert.equal(state.snapshotCursor, 20);
  assert.equal(state.tasks['3'].status, 'succeeded');
  assert.equal(state.tasks['9'].status, 'stopped');
});

test('global task recovery retains three independent scopes and resume identity', () => {
  const local = storage(); const request = pending();
  request.body = { content: '补充一个条件', task_id: '9007199254740993',
    context: { kind: 'project', id: '11', project_id: '11', revision: '4', include_document: false, selected: [{ kind: 'asset', id: '16', revision: '7' }] },
    execution_target: { mode: 'infinite_canvas', project_id: '12', canvas_id: 'canvas-uuid' }, memory_project_ids: ['13'], media_limits: { images: 2, videos: 0, audio: null },
  };
  assert.equal(drafts.saveAssistantPendingSend('1', '3', request, local), true);
  assert.deepEqual(drafts.readAssistantPendingSend('1', '3', local).pending, request);
  request.body.new_task = true;
  assert.equal(drafts.saveAssistantPendingSend('1', '4', request, local), false);
});
