import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/assets/asset-editing-session.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { AssetEditingSession, assetRecoveryKey } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const asset = () => ({ id: '9007199254740993', row_version: '1', name: '林晚', kind: 'character', description: '黑色外套', prompt: '旧共享提示词', tags: [], scene_time: '', label: '', state: 'confirmed', media_id: '8', image: { media_id: '8', url: 'image' }, link_id: '77', position: 0 });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const recovery = () => { let record = null; return { read: () => record, save: value => { record = structuredClone(value); }, clear: () => { record = null; }, get value() { return record; } }; };

test('serial saves carry fresh string versions and late acknowledgements preserve new keystrokes', async () => {
  const waiting = deferred(); const writes = []; let server = asset();
  const storage = recovery();
  const session = new AssetEditingSession(server, async (_, body) => {
    writes.push(body); if (writes.length === 1) await waiting.promise;
    server = { ...server, ...body, row_version: String(BigInt(server.row_version) + 1n) };
    return server;
  }, storage, 60000);
  session.edit({ description: '第一次输入' });
  const save = session.flush();
  session.edit({ description: '等待期间继续输入' });
  assert.equal(session.getSnapshot().asset.description, '等待期间继续输入');
  waiting.resolve();
  assert.equal((await save).description, '等待期间继续输入');
  assert.equal(writes.length, 2);
  assert.deepEqual(writes.map(item => item.row_version), ['1', '2']);
  assert.equal(writes[1].prompt, '旧共享提示词');
  assert.equal(session.getSnapshot().asset.row_version, '3');
  assert.equal(storage.value, null);
  session.dispose();
});

test('409 pauses the queue, retains all shared fields, and requires explicit reconciliation', async () => {
  let calls = 0; const storage = recovery(); let conflict = true;
  const session = new AssetEditingSession(asset(), async (_, body) => {
    calls += 1;
    if (conflict) throw { status: 409, code: 'asset_version_conflict' };
    return { ...asset(), ...body, row_version: '4' };
  }, storage, 60000);
  session.edit({ name: '本人草稿', kind: 'prop' });
  assert.equal(await session.flush(), null);
  session.edit({ description: '冲突期间的输入' });
  assert.equal(await session.flush(), null);
  assert.equal(await session.retry(), null);
  assert.equal(calls, 1);
  assert.equal(storage.value.fields.description, '冲突期间的输入');
  assert.equal(session.getSnapshot().asset.name, '本人草稿');
  conflict = false;
  session.reconcile({ ...asset(), name: '其他成员', row_version: '3' }, true);
  await session.flush();
  assert.equal(calls, 2);
  assert.equal(session.getSnapshot().asset.name, '本人草稿');
  assert.equal(session.getSnapshot().asset.row_version, '4');
  session.dispose();
});

test('shared confirmation pauses without repeated requests and applies only the approved editing batch', async () => {
  const writes = []; let server = asset();
  const session = new AssetEditingSession(server, async (_, body) => {
    writes.push(body);
    if (!body.confirm_shared) throw { status: 409, code: 'shared_asset_confirmation_required' };
    server = { ...server, ...body, row_version: String(BigInt(server.row_version) + 1n) }; return server;
  }, recovery(), 60000);
  session.edit({ description: '修改共享作品' });
  assert.equal(await session.flush(), null);
  assert.equal(session.getSnapshot().status, 'shared');
  assert.equal(await session.flush(), null);
  assert.equal(writes.length, 1);
  session.edit({ description: '确认前继续输入' });
  await session.approveShared();
  assert.equal(writes[1].confirm_shared, true);
  assert.equal(writes[1].description, '确认前继续输入');
  session.edit({ description: '下一个编辑批次' });
  await session.flush();
  assert.equal(writes[2].confirm_shared, false);
  assert.equal(session.getSnapshot().status, 'shared');
  session.dispose();
});

test('503 retains recovery and stops automated writes until retry', async () => {
  let fail = true; let calls = 0; const storage = recovery();
  const session = new AssetEditingSession(asset(), async (_, body) => {
    calls += 1; if (fail) throw { status: 503, message: '暂停服务' };
    return { ...asset(), ...body, row_version: '2' };
  }, storage, 60000);
  session.edit({ description: '离线草稿' });
  assert.equal(await session.flush(), null);
  assert.equal(session.getSnapshot().status, 'error');
  assert.equal(storage.value.fields.description, '离线草稿');
  assert.equal(await session.flush(), null);
  assert.equal(calls, 1);
  fail = false; await session.retry();
  assert.equal(calls, 2); assert.equal(storage.value, null);
  session.dispose();
});

test('recovered drafts from an older server version cannot post before explicit review', async () => {
  let calls = 0; const storage = recovery();
  storage.save({ base: asset(), fields: { ...asset(), description: '本机保留' } });
  const session = new AssetEditingSession({ ...asset(), description: '服务端新稿', row_version: '2' }, async () => { calls += 1; return asset(); }, storage);
  assert.equal(session.getSnapshot().status, 'conflict');
  assert.equal(session.getSnapshot().asset.description, '本机保留');
  assert.equal(await session.flush(), null); assert.equal(calls, 0);
  assert.notEqual(assetRecoveryKey('1', '2', '3', '4'), assetRecoveryKey('5', '2', '3', '4'));
  session.dispose();
});

test('malformed recovery stays untouched and blocks saves until it is explicitly cleared', async () => {
  for (const record of [{}, { base: asset(), fields: { ...asset(), tags: null } }, { base: { ...asset(), row_version: 1 }, fields: asset() }]) {
    let stored = record; let calls = 0; let writes = 0;
    const storage = { read: () => stored, save: value => { writes += 1; stored = value; }, clear: () => { stored = null; } };
    let session;
    assert.doesNotThrow(() => { session = new AssetEditingSession(asset(), async (_, body) => { calls += 1; return { ...asset(), ...body, row_version: '2' }; }, storage, 60000); });
    assert.equal(session.getSnapshot().recoveryBlocked, true);
    assert.equal(session.hasUnsettled(), true);
    session.edit({ description: '保留在内存中的新输入' });
    assert.equal(await session.retry(), null);
    assert.equal(await session.flush(), null);
    assert.equal(stored, record); assert.equal(writes, 0); assert.equal(calls, 0);
    assert.equal(session.discardRecovery(), true);
    assert.equal(session.getSnapshot().recoveryBlocked, false);
    await session.flush();
    assert.equal(calls, 1); assert.equal(stored, null);
    session.dispose();
  }
});

test('disposed sessions leave a replacement session recovery untouched when a save resolves or fails late', async () => {
  for (const succeeds of [true, false]) {
    const waiting = deferred(); const storage = recovery();
    const oldSession = new AssetEditingSession(asset(), async (_, body) => {
      await waiting.promise;
      if (!succeeds) throw { status: 503, message: 'late failure' };
      return { ...asset(), ...body, row_version: '2' };
    }, storage, 60000);
    oldSession.edit({ description: 'old session input' });
    const saving = oldSession.flush();
    oldSession.dispose();
    const newSession = new AssetEditingSession(asset(), async () => asset(), storage, 60000);
    try {
      newSession.edit({ description: 'new session input' });
      const before = structuredClone(storage.value);
      waiting.resolve();
      assert.equal(await saving, null);
      assert.deepEqual(storage.value, before);
      assert.equal(newSession.getSnapshot().asset.description, 'new session input');
      assert.equal(oldSession.getSnapshot().status, 'saving');
    } finally { newSession.dispose(); }
  }
});

test('shared asset recovery keys are identical across episodes and isolate different accounts', () => {
  assert.equal(assetRecoveryKey('1', '2', '3', '4'), assetRecoveryKey('1', '2', '5', '4'));
  assert.notEqual(assetRecoveryKey('1', '2', '3', '4'), assetRecoveryKey('9', '2', '3', '4'));
});

test('pending asset recovery CAS saves latest baseline input before an older request can overwrite it', async () => {
  const storage = recovery(); const original = asset(); const writes = [];
  storage.save({ base: original, fields: original, pending: { ...original, description: 'older in-flight fields' } });
  const session = new AssetEditingSession(original, async (_, body) => {
    writes.push(body); return { ...original, ...body, row_version: '2' };
  }, storage, 60000);
  assert.equal(session.getSnapshot().dirty, true); assert.notEqual(storage.value.pending, null);
  await session.flush();
  assert.equal(writes.length, 1); assert.equal(writes[0].row_version, '1'); assert.equal(writes[0].description, original.description);
  assert.equal(storage.value, null); session.dispose();
});

test('lost asset response is read back before continuing newer serial input', async () => {
  let server = asset(); const writes = []; const storage = recovery(); let lose = true;
  const session = new AssetEditingSession(server, async (_, body) => {
    writes.push(body); server = { ...server, ...body, row_version: String(BigInt(server.row_version) + 1n) };
    if (lose) { lose = false; throw new Error('lost receipt'); } return server;
  }, storage, 60000, async () => server);
  session.edit({ description: 'new shared fields' }); await session.flush();
  assert.equal(session.getSnapshot().status, 'saved'); assert.equal(writes.length, 1); assert.equal(storage.value, null); session.dispose();
});

test('another episode seed refreshes a clean shared session and never replaces dirty input or regresses versions', async () => {
  const session = new AssetEditingSession(asset(), async () => asset(), recovery(), 60000);
  session.observe({ ...asset(), row_version: '2', description: 'shared server version' });
  assert.equal(session.getSnapshot().asset.description, 'shared server version');
  assert.equal(session.getSnapshot().dirty, false);
  session.observe(asset()); assert.equal(session.getSnapshot().saved.row_version, '2');
  session.edit({ description: 'local input' });
  session.observe({ ...asset(), row_version: '3', description: 'collaborator change' });
  assert.equal(session.getSnapshot().status, 'conflict'); assert.equal(session.getSnapshot().asset.description, 'local input');
  session.dispose();
});

for (const olderWins of [false, true]) {
  test(`explicit asset server replacement fences the unknown request when ${olderWins ? 'older' : 'replacement'} commits first`, async () => {
    const storage = recovery(); let server = asset(), older = null;
    const commit = async body => {
      if (body.row_version !== server.row_version) throw { status: 409, code: 'asset_version_conflict' };
      server = { ...server, ...body, row_version: String(BigInt(server.row_version) + 1n) }; return server;
    };
    const session = new AssetEditingSession(server, async (_, body) => {
      if (!older) { older = structuredClone(body); throw { status: 503 }; } return commit(body);
    }, storage, 60000, async () => server);
    try {
      session.edit({ description: 'older request' }); await session.flush();
      session.reconcile(server, false);
      assert.equal(session.getSnapshot().asset.description, asset().description);
      assert.equal(session.getSnapshot().dirty, true); assert.equal(storage.value.pending.description, 'older request');
      if (olderWins) {
        await commit(older); assert.equal(await session.flush(), null);
        assert.equal(session.getSnapshot().status, 'conflict'); assert.equal(session.getSnapshot().asset.description, asset().description);
        assert.notEqual(storage.value, null);
      } else {
        await session.flush(); await assert.rejects(commit(older), error => error.status === 409);
        assert.equal(server.description, asset().description); assert.equal(server.row_version, '2');
        assert.equal(storage.value, null);
      }
    } finally { session.dispose(); }
  });
}

test('explicit asset server replacement clears an unknown request after the server version advances', async () => {
  const storage = recovery(); const server = asset();
  const session = new AssetEditingSession(server, async () => { throw { status: 503 }; }, storage, 60000, async () => server);
  try {
    session.edit({ description: 'older request' }); await session.flush();
    session.reconcile({ ...server, row_version: '2', description: 'already accepted' }, false);
    assert.equal(session.getSnapshot().asset.description, 'already accepted');
    assert.equal(session.getSnapshot().dirty, false); assert.equal(storage.value, null);
  } finally { session.dispose(); }
});
