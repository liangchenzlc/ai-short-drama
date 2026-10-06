import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const compile = path => ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const url = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const attemptUrl = url(compile('../src/features/generations/attempt.ts'));
const attempt = await import(attemptUrl);
const recoveryUrl = url(compile('../src/features/projects/creation-recovery.ts').replace('../generations/attempt', attemptUrl));
const recovery = await import(recoveryUrl);
const render = await import(url(compile('../src/features/projects/render-recovery.ts').replace('./creation-recovery', recoveryUrl)));
const assembly = await import(url(compile('../src/features/projects/assembly-recovery.ts').replace('./creation-recovery', recoveryUrl)));
const store = () => { const map = new Map(); return { getItem: k => map.get(k) ?? null, setItem: (k, v) => map.set(k, v), removeItem: k => map.delete(k) }; };
const body = { row_version: '9007199254740993', source_hash: 'a'.repeat(64), acknowledge_stale_source: false };

test('recovery uses the authenticated in-memory account when session storage is stale or unavailable', () => {
  const previous = globalThis.window;
  globalThis.window = { sessionStorage: { getItem: () => '11', setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); } } };
  try {
    attempt.setAttemptAccount('22');
    assert.equal(recovery.creationScope('writing:10:20'), 'creation-recovery:user:22:writing:10:20');
    attempt.setAttemptAccount('33');
    assert.equal(recovery.creationScope('writing:10:20'), 'creation-recovery:user:33:writing:10:20');
    attempt.setAttemptAccount(null);
    assert.equal(recovery.creationScope('writing:10:20'), 'creation-recovery:user:anonymous:writing:10:20');
  } finally { globalThis.window = previous; }
});

test('lost render receipt keeps complete original body and key across reload', () => {
  const storage = store();
  const accepted = render.beginRenderAttempt('user:1:episode:2', { kind: 'export', body }, storage);
  const restored = render.readRenderAttempt('user:1:episode:2', storage);
  assert.deepEqual(restored, accepted);
  assert.equal(render.beginRenderAttempt('user:1:episode:2', { kind: 'export', body }, storage).key, accepted.key);
  assert.throws(() => render.beginRenderAttempt('user:1:episode:2', { kind: 'export', body: { ...body, row_version: '2' } }, storage), /尚未核对/);
  assert.throws(() => render.beginRenderAttempt('user:1:episode:2', { kind: 'preview', body }, storage), /尚未核对/);
  render.finishRenderAttempt('user:1:episode:2', accepted, storage);
  assert.notEqual(render.beginRenderAttempt('user:1:episode:2', { kind: 'export', body }, storage).key, accepted.key);
});
test('retry and accounts are isolated; an older receipt cannot clear a new attempt', () => {
  const storage = store();
  const first = render.beginRenderAttempt('user:1:episode:2', { kind: 'retry', body: {}, job_id: '123' }, storage);
  assert.equal(render.readRenderAttempt('user:2:episode:2', storage), null);
  render.finishRenderAttempt('user:1:episode:2', first, storage);
  const next = render.beginRenderAttempt('user:1:episode:2', { kind: 'retry', body: {}, job_id: '123' }, storage);
  render.finishRenderAttempt('user:1:episode:2', first, storage);
  assert.equal(render.readRenderAttempt('user:1:episode:2', storage).key, next.key);
});
test('corrupt or unavailable persistence blocks submission instead of replacing the key', () => {
  const storage = store(); storage.setItem('scope', '{bad');
  assert.throws(() => render.beginRenderAttempt('scope', { kind: 'export', body }, storage), /无法读取/);
  assert.throws(() => render.beginRenderAttempt('scope', { kind: 'export', body }, null), /无法保存/);
});

test('render recovery exposes the complete damaged record without replacing it', () => {
  const storage = store();
  for (const raw of ['  {damaged\n中文记录', '', JSON.stringify({ format: 2, kind: 'export', key: 'original-key', body })]) {
    storage.setItem('render', raw);
    assert.deepEqual(render.readRenderRecoveryRecord('render', storage), { attempt: null, damaged: raw });
    assert.throws(() => render.beginRenderAttempt('render', { kind: 'export', body }, storage), /无法读取/);
    assert.equal(storage.getItem('render'), raw);
  }
});

test('explicit discard removes only the unchanged damaged render slot', () => {
  const storage = store(); const raw = '{damaged-record';
  storage.setItem('render', raw); storage.setItem('draft', 'saved draft');
  const other = render.beginRenderAttempt('other-render', { kind: 'preview', body }, storage);
  render.discardDamagedRenderAttempt('render', raw, storage);
  assert.equal(storage.getItem('render'), null);
  assert.equal(storage.getItem('draft'), 'saved draft');
  assert.deepEqual(render.readRenderAttempt('other-render', storage), other);
  assert.equal(render.beginRenderAttempt('render', { kind: 'export', body }, storage).kind, 'export');
});

test('valid unknown render requests cannot use the damaged-record discard path', () => {
  const storage = store();
  const original = render.beginRenderAttempt('render', { kind: 'export', body }, storage);
  const raw = storage.getItem('render');
  assert.deepEqual(render.readRenderRecoveryRecord('render', storage), { attempt: original, damaged: null });
  assert.throws(() => render.discardDamagedRenderAttempt('render', raw, storage), /仍可恢复/);
  assert.deepEqual(render.readRenderAttempt('render', storage), original);
  assert.equal(render.beginRenderAttempt('render', { kind: 'export', body }, storage).key, original.key);
});

test('discard does not delete a render record changed while confirmation is open', () => {
  const storage = store();
  storage.setItem('render', '{new-damage');
  assert.throws(() => render.discardDamagedRenderAttempt('render', '{old-damage', storage), /已变化/);
  assert.equal(storage.getItem('render'), '{new-damage');
  storage.removeItem('render');
  const original = render.beginRenderAttempt('render', { kind: 'retry', body: {}, job_id: '9007199254740993' }, storage);
  assert.throws(() => render.discardDamagedRenderAttempt('render', '{old-damage', storage), /仍可恢复/);
  assert.deepEqual(render.readRenderAttempt('render', storage), original);
});

test('failed storage reads or removals keep damaged render recovery blocked', () => {
  const storage = store(); storage.setItem('render', '{damaged-record');
  const unavailable = { ...storage, getItem() { throw new Error('blocked'); } };
  assert.throws(() => render.readRenderRecoveryRecord('render', unavailable), /无法读取/);
  const removalFails = { ...storage, removeItem() { throw new Error('blocked'); } };
  assert.throws(() => render.discardDamagedRenderAttempt('render', '{damaged-record', removalFails), /无法清除/);
  assert.equal(storage.getItem('render'), '{damaged-record');
});

test('only explicit pre-creation business rejections release a render attempt', () => {
  assert.equal(render.definiteRenderRejection('assembly_not_ready', 422), true);
  assert.equal(render.definiteRenderRejection('sound_review_required', 409), true);
  assert.equal(render.definiteRenderRejection('assembly_not_ready', 503), false);
  assert.equal(render.definiteRenderRejection('assembly_not_ready'), false);
  assert.equal(render.definiteRenderRejection('assembly_idempotency_conflict', 409), false);
  assert.equal(render.definiteRenderRejection('not_found', 404), false);
  assert.equal(render.definiteRenderRejection('authentication_required', 401), false);
});
test('draft recovery preserves a string baseline and exact unsaved document', () => {
  const storage = store(); const draft = { format: 1, base_version: '9007199254740993', document: { novel: '小说草稿', script: '剧本草稿' } };
  recovery.saveRecovery('draft:user:1:episode:2', draft, storage);
  assert.deepEqual(recovery.readRecovery('draft:user:1:episode:2', recovery.isStoredDraft, storage), draft);
  assert.equal(recovery.readRecovery('draft:user:1:episode:3', recovery.isStoredDraft, storage), null);
  assert.equal(recovery.isStoredDraft({ ...draft, base_version: 1 }), false);
});
test('assembly draft stores stable media and edits, refreshes URLs and blocks missing sources', () => {
  const clip = { id: 'clip-1', shot_id: '10', media_id: '100', included: true, muted: true, position: 1, shot_position: 1, trim_in_ms: 400, trim_out_ms: 1800, duration_ms: 3000, script: '镜头', url: '/signed?secret=old', poster: '/poster?secret=old', filmstrip: { url: '/strip?secret=old' }, is_stale: false, archived: false, issue: null };
  const original = { assembly: { id: '1', row_version: '9007199254740993', resolution: '720p' }, clips: [clip] };
  const draft = assembly.assemblyDraft(original);
  assert.equal(JSON.stringify(draft).includes('secret='), false);
  assert.equal(assembly.validAssemblyDraft(draft), true);
  const restored = assembly.restoreAssemblyDraft({ ...original, clips: [{ ...clip, url: '/fresh', trim_in_ms: 0 }] }, draft);
  assert.equal(restored.clips[0].trim_in_ms, 400); assert.equal(restored.clips[0].muted, true); assert.equal(restored.clips[0].url, '/fresh');
  const missing = assembly.restoreAssemblyDraft({ ...original, clips: [], sources: [] }, draft);
  assert.equal(missing.clips[0].issue, 'missing'); assert.equal(missing.clips[0].url, null);
});
