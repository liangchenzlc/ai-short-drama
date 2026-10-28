import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const compile = path => ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const url = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const registry = await import(url(compile('../../src/features/creation/shared/sessions/registry.ts')));
const assetDraftsUrl = url(compile('../../src/features/creation/assets/sessions/local-asset-draft-model.ts'));
const { blankAssetDraft } = await import(assetDraftsUrl);
const { LocalAssetSession } = await import(url(compile('../../src/features/creation/assets/sessions/local-asset-session.ts').replace("'@host/features/creation/assets/sessions/local-asset-draft-model'", JSON.stringify(assetDraftsUrl))));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const storage = () => { const values = new Map(); return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }; };

test('releasing a page preserves its dirty queue and repeated shared asset IDs reuse one controller', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  registry.setStandardSessionAccount('registry-1');
  let made = 0, saves = 0, disposed = 0;
  const controller = { getSnapshot: () => ({ status: 'unsaved', dirty: true }), subscribe: () => () => {},
    hasUnsettled: () => true, flush: async () => { saves++; }, dispose: () => { disposed++; } };
  const first = registry.retainStandardSession('registry-1', 'asset:17', () => { made++; return controller; });
  registry.releaseStandardSession('registry-1', 'asset:17');
  t.mock.timers.tick(30_001);
  const second = registry.retainStandardSession('registry-1', 'asset:17', () => { made++; return controller; });
  assert.equal(first, second); assert.equal(made, 1); assert.equal(disposed, 0); assert.equal(saves, 1);
  registry.setStandardSessionAccount('registry-2'); assert.equal(disposed, 1);
});

test('account changes suspend private queues and hide their save details while retaining memory-only input', () => {
  registry.setStandardSessionAccount('account-a');
  let suspended = 0, resumed = 0;
  const controller = { getSnapshot: () => ({ status: 'error', message: 'private text' }), subscribe: () => () => {},
    hasUnsettled: () => true, hasMemoryOnlyChanges: () => true, dispose() {}, suspend() { suspended++; }, resume() { resumed++; } };
  registry.retainStandardSession('account-a', 'private:1', () => controller);
  assert.equal(registry.hasStandardUnpersistedChanges(), true);
  registry.setStandardSessionAccount('account-b');
  assert.equal(suspended, 1); assert.equal(registry.getStandardSaveSnapshot().length, 0);
  assert.equal(registry.hasStandardUnpersistedChanges(), false);
  registry.setStandardSessionAccount('account-a');
  assert.equal(resumed, 1); assert.equal(registry.getStandardSaveSnapshot()[0].message, 'private text');
});

test('retaining an existing session keeps the external-store snapshot stable until its details change', async () => {
  registry.setStandardSessionAccount('render-retry');
  const controller = { getSnapshot: () => ({ status: 'saved' }), subscribe: () => () => {}, dispose() {} };
  const details = { label: '剧本', projectId: '10', episodeId: '20' };
  registry.retainStandardSession('render-retry', 'writing:10:20', () => controller, details);
  await Promise.resolve();
  const before = registry.getStandardSaveSnapshot();
  let notifications = 0;
  const unsubscribe = registry.subscribeStandardSaveState(() => { notifications++; });
  registry.retainStandardSession('render-retry', 'writing:10:20', () => controller, { ...details });
  await Promise.resolve();
  assert.equal(registry.getStandardSaveSnapshot(), before);
  assert.equal(notifications, 0);
  registry.retainStandardSession('render-retry', 'writing:10:20', () => controller, { label: '剧本正文' });
  await Promise.resolve();
  assert.equal(notifications, 1);
  assert.equal(registry.getStandardSaveSnapshot()[0].label, '剧本正文');
  unsubscribe();
});

test('damaged recovery requires review and identical controller notifications preserve the snapshot', async () => {
  registry.setStandardSessionAccount('damaged-review');
  let notify;
  const controller = { getSnapshot: () => ({ status: 'error', recoveryBlocked: true }), subscribe: listener => { notify = listener; return () => {}; }, dispose() {} };
  registry.retainStandardSession('damaged-review', 'writing:10:20', () => controller);
  await Promise.resolve();
  const before = registry.getStandardSaveSnapshot();
  assert.equal(before[0].status, 'conflict');
  notify(); await Promise.resolve();
  assert.equal(registry.getStandardSaveSnapshot(), before);
});

test('a detached dirty queue is reclaimed after it finishes saving', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  registry.setStandardSessionAccount('eventual-cleanup');
  let dirty = true, disposed = false, notify;
  const controller = { getSnapshot: () => ({ status: dirty ? 'unsaved' : 'saved' }), subscribe: listener => { notify = listener; return () => {}; },
    hasUnsettled: () => dirty, flush: async () => {}, dispose() { disposed = true; } };
  registry.retainStandardSession('eventual-cleanup', 'writing:10:20', () => controller);
  registry.releaseStandardSession('eventual-cleanup', 'writing:10:20');
  t.mock.timers.tick(30_001); assert.equal(disposed, false);
  dirty = false; notify();
  t.mock.timers.tick(29_999); assert.equal(disposed, false);
  t.mock.timers.tick(2); assert.equal(disposed, true);
  assert.equal(registry.getStandardSaveSnapshot().length, 0);
});

test('detached queue cleanup also observes the completion of its last operation', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  registry.setStandardSessionAccount('operation-cleanup');
  const gate = deferred(); let unsettled = true, disposed = false;
  const completion = gate.promise.then(() => { unsettled = false; });
  const controller = { getSnapshot: () => ({ status: 'saved' }), subscribe: () => () => {},
    hasUnsettled: () => unsettled, flush: () => completion, dispose() { disposed = true; } };
  registry.retainStandardSession('operation-cleanup', 'writing:10:20', () => controller);
  registry.releaseStandardSession('operation-cleanup', 'writing:10:20');
  gate.resolve(); await completion; await Promise.resolve();
  t.mock.timers.tick(30_001); assert.equal(disposed, true);
});

test('equal-looking resources from another account receive that account actions', async () => {
  let first = 0, second = 0;
  const controller = callback => ({ getSnapshot: () => ({ status: 'saved' }), subscribe: () => () => {}, dispose() {}, suspend() {}, resume() {}, flush: callback });
  registry.setStandardSessionAccount('actions-a');
  registry.retainStandardSession('actions-a', 'writing:10:20', () => controller(async () => { first++; }));
  registry.retainStandardSession('actions-b', 'writing:10:20', () => controller(async () => { second++; }));
  registry.setStandardSessionAccount('actions-b');
  await registry.getStandardSaveSnapshot()[0].flush();
  assert.equal(first, 0); assert.equal(second, 1);
});

test('unnamed local input survives editor release and in-flight creation keeps its receipt and newer input', async t => {
  const previous = globalThis.localStorage; globalThis.localStorage = storage();
  t.after(() => { globalThis.localStorage = previous; });
  const gate = deferred(); let preserved;
  const session = new LocalAssetSession('local-one', {
    create: async body => { await gate.promise; return { ...body, id: '17', row_version: '1' }; },
    preserveCreated: (saved, current) => { preserved = { saved, current }; },
  });
  const placeholder = blankAssetDraft('all'); session.replace([placeholder]);
  session.edit(placeholder.id, { description: '先写人物，稍后命名' });
  assert.equal(session.getSnapshot().drafts[0].description, '先写人物，稍后命名');
  assert.equal(session.hasMemoryOnlyChanges(), false);
  const unsubscribe = session.subscribe(() => {}); unsubscribe();
  session.edit(placeholder.id, { name: '林晚', kind: 'character' });
  const saving = session.create(placeholder.id);
  session.edit(placeholder.id, { description: '创建请求期间的新输入' });
  gate.resolve(); assert.equal(await saving, true);
  assert.equal(session.getSnapshot().drafts.length, 0);
  assert.equal(session.getSnapshot().created[placeholder.id].asset.description, '创建请求期间的新输入');
  assert.equal(preserved.current.description, '创建请求期间的新输入'); session.dispose();
});

test('unknown local creation retries its original request and does not invent a second idempotency key', async t => {
  const previous = globalThis.localStorage; globalThis.localStorage = storage();
  t.after(() => { globalThis.localStorage = previous; });
  const requests = []; let fail = true;
  const session = new LocalAssetSession('local-two', { create: async (body, key) => {
    requests.push({ body, key }); if (fail) throw new Error('lost receipt'); return { ...body, id: '18', row_version: '1' };
  }, preserveCreated() {} });
  const draft = { ...blankAssetDraft('character'), name: '原名称' }; session.replace([draft]);
  assert.equal(await session.create(draft.id), false);
  session.edit(draft.id, { name: '请求后新名称' }); fail = false;
  assert.equal(await session.create(draft.id), true);
  assert.deepEqual(requests[0], requests[1]);
  assert.equal(session.getSnapshot().created[draft.id].asset.name, '请求后新名称'); session.dispose();
});

test('navigation flush cannot overwrite or delete a damaged local asset recovery record', async t => {
  const previous = globalThis.localStorage; globalThis.localStorage = storage();
  t.after(() => { globalThis.localStorage = previous; });
  localStorage.setItem('damaged-assets', '{original damaged recovery');
  const session = new LocalAssetSession('damaged-assets', { create: async () => { throw new Error('must not create'); }, preserveCreated() {} });
  assert.equal(session.getSnapshot().status, 'error');
  assert.equal(await session.flush(), false);
  session.replace([blankAssetDraft('all')]); await session.retry();
  assert.equal(localStorage.getItem('damaged-assets'), '{original damaged recovery');
  session.dispose();
});

test('a temporary local storage failure clears after a successful write and creation can continue', async t => {
  const previous = globalThis.localStorage; const backing = storage(); let fail = true; let creations = 0;
  globalThis.localStorage = { ...backing, setItem(key, value) { if (fail) throw new Error('temporary quota'); backing.setItem(key, value); } };
  t.after(() => { globalThis.localStorage = previous; });
  const session = new LocalAssetSession('temporary-assets', { create: async body => { creations++; return { ...body, id: '19', row_version: '1' }; }, preserveCreated() {} });
  const draft = blankAssetDraft('all'); session.replace([draft]); session.edit(draft.id, { description: 'retained in memory' });
  assert.equal(session.hasMemoryOnlyChanges(), true); assert.ok(session.getSnapshot().recoveryError);
  fail = false; session.edit(draft.id, { kind: 'character', name: 'restored storage' });
  assert.equal(session.hasMemoryOnlyChanges(), false); assert.equal(session.getSnapshot().recoveryError, '');
  assert.equal(await session.create(draft.id), true); assert.equal(creations, 1); session.dispose();
});
