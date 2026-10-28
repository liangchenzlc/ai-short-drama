import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../../src/features/creation/shared/sessions/resource-editing-session.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { ResourceEditingSession } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const clone = value => structuredClone(value);
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
function fixture(recovery = null) {
  let remote = { version: '1', text: 'saved', reviewed: false }, stored = clone(recovery);
  const writes = [], backups = [];
  const options = {
    read: async () => clone(remote),
    save: async (base, document) => {
      writes.push({ base: clone(base), document: clone(document) });
      remote = { ...document, version: String(BigInt(base.version) + 1n) }; return clone(remote);
    },
    version: value => value.version,
    document: ({ text, reviewed }) => ({ text, reviewed }),
    apply: (base, document) => ({ ...base, ...document }),
    recovery: { read: () => clone(stored), save: value => { stored = clone(value); }, clear: () => { stored = null; }, export: () => stored ? JSON.stringify(stored) : null, previous: () => backups.length ? JSON.stringify(backups.at(-1)) : null, backup: raw => { backups.push(JSON.parse(raw)); } },
    pauseMs: 10_000,
  };
  return { options, writes, backups, create: () => new ResourceEditingSession(options), get remote() { return remote; }, set remote(value) { remote = value; }, get stored() { return stored; } };
}

test('save queue survives editor unsubscribe and drains newer input without changing review', async () => {
  const f = fixture(), session = f.create(); await session.load();
  const gate = deferred(), save = f.options.save; let first = true;
  f.options.save = async (...args) => { if (first) { first = false; await gate.promise; } return save(...args); };
  const unsubscribe = session.subscribe(() => {});
  session.edit({ text: 'first', reviewed: false }); const saving = session.flush();
  await tick();
  session.edit({ text: 'newer', reviewed: false }); unsubscribe(); gate.resolve();
  assert.equal(await saving, true);
  assert.deepEqual(f.writes.map(write => write.base.version), ['1', '2']);
  assert.equal(f.remote.text, 'newer'); assert.equal(f.remote.reviewed, false); assert.equal(f.stored, null);
  session.dispose();
});

test('same-version recovery silently resumes saving and a stale read cannot roll it back', async () => {
  const f = fixture({ format: 1, base_version: '1', document: { text: 'recovered', reviewed: false } });
  const session = f.create(); await session.load();
  assert.equal(session.getSnapshot().value.text, 'recovered'); assert.equal(session.getSnapshot().recovered, null);
  assert.equal(await session.flush(), true);
  session.initialize({ version: '1', text: 'stale list', reviewed: false });
  assert.equal(session.getSnapshot().value.text, 'recovered'); assert.equal(session.getSnapshot().value.version, '2');
  assert.equal(session.accept({ version: '1', text: 'stale business receipt', reviewed: false }), false);
  session.dispose();
});

test('a failed refresh cannot pause the independent save queue', async () => {
  const f = fixture(), session = f.create(); await session.load();
  f.options.read = async () => { throw new Error('poll offline'); };
  await assert.rejects(session.load(true), /poll offline/);
  session.edit({ text: 'still editable', reviewed: false });
  assert.equal(await session.flush(), true); assert.equal(f.remote.text, 'still editable'); session.dispose();
});

test('an explicit fresh read cannot join an older pending poll and stale arrivals cannot roll it back', async () => {
  const f = fixture(), session = f.create(); await session.load(); const old = deferred(), fresh = deferred(); let reads = 0;
  f.options.read = () => ++reads === 1 ? old.promise : fresh.promise;
  const polling = session.load(); const refreshing = session.load(true, true);
  assert.equal(reads, 2);
  fresh.resolve({ version: '2', text: 'new saved source', reviewed: false }); await refreshing;
  old.resolve({ version: '1', text: 'saved', reviewed: false }); await polling;
  assert.equal(session.getSnapshot().value.version, '2'); assert.equal(session.getSnapshot().value.text, 'new saved source');
  session.dispose();
});

test('older reads cannot replace newer render metadata even when the saved document version is unchanged', async () => {
  const f = fixture(), session = f.create(); await session.load(); const old = deferred(), fresh = deferred(); let reads = 0;
  f.options.read = () => ++reads === 1 ? old.promise : fresh.promise;
  const polling = session.load(); const refreshing = session.load(true, true);
  fresh.resolve({ ...f.remote, render_progress: 100 }); await refreshing;
  old.resolve({ ...f.remote, render_progress: 30 }); await polling;
  assert.equal(session.getSnapshot().value.render_progress, 100); session.dispose();
});

test('changed-version recovery stays conflicting after another refresh and never rebases itself', async () => {
  const f = fixture({ format: 1, base_version: '0', document: { text: 'local branch', reviewed: false } });
  const first = f.create(); await first.load(); first.edit({ text: 'continued branch', reviewed: false });
  assert.equal(first.getSnapshot().status, 'conflict'); assert.equal(f.stored.base_version, '0'); first.dispose();
  const second = f.create(); await second.load(); assert.equal(await second.flush(), false);
  assert.equal(second.getSnapshot().status, 'conflict'); assert.equal(second.getSnapshot().value.text, 'continued branch');
  assert.equal(f.writes.length, 0); second.dispose();
});

test('409 pauses this resource while keeping later input and never retries over the remote', async () => {
  const f = fixture(), session = f.create(); await session.load(); let attempts = 0;
  f.options.save = async () => { attempts++; throw { status: 409 }; };
  session.edit({ text: 'conflict', reviewed: false }); assert.equal(await session.flush(), false);
  session.edit({ text: 'more local', reviewed: false }); assert.equal(await session.retry(), false);
  assert.equal(attempts, 1); assert.equal(f.stored.document.text, 'more local'); session.dispose();
});

test('a lost save receipt is reconciled by reading before retry instead of repeating the write', async () => {
  const f = fixture(), session = f.create(); await session.load();
  const save = f.options.save;
  f.options.save = async (...args) => { await save(...args); throw new Error('response lost'); };
  session.edit({ text: 'accepted', reviewed: false }); assert.equal(await session.flush(), false);
  assert.ok(f.stored.pending); assert.equal(await session.retry(), true);
  assert.equal(f.writes.length, 1); assert.equal(session.getSnapshot().value.version, '2'); assert.equal(f.stored, null);
  session.dispose();
});

test('refresh cannot clear an uncertain write when the latest input matches the old server value', async () => {
  for (const oldWriteFirst of [false, true]) {
    const recovery = { format: 1, base_version: '1', document: { text: 'saved', reviewed: false }, pending: { base_version: '1', document: { text: 'older in-flight edit', reviewed: false } } };
    const f = fixture(recovery), session = f.create();
    const save = f.options.save;
    f.options.save = async (base, document) => {
      if (oldWriteFirst) f.remote = { version: '2', ...recovery.pending.document };
      if (base.version !== f.remote.version) throw { status: 409 };
      return save(base, document);
    };
    await session.load();
    assert.equal(session.getSnapshot().dirty, true);
    assert.ok(f.stored.pending);
    assert.equal(await session.flush(), !oldWriteFirst);
    assert.equal(session.getSnapshot().value.text, 'saved');
    if (oldWriteFirst) {
      assert.equal(session.getSnapshot().status, 'conflict');
      assert.equal(f.stored.document.text, 'saved');
    } else {
      assert.equal(f.writes.length, 1);
      assert.equal(f.remote.text, 'saved'); assert.equal(f.remote.version, '2');
    }
    session.dispose();
  }
});

test('reverting input during an uncertain save remains pending until a guarded save or conflict', async () => {
  const f = fixture(), session = f.create(); await session.load(); const gate = deferred();
  f.options.save = async () => { await gate.promise; throw new Error('response unknown'); };
  session.edit({ text: 'in-flight', reviewed: false }); const saving = session.flush(); await tick();
  session.edit({ text: 'saved', reviewed: false });
  assert.equal(session.getSnapshot().dirty, true); gate.resolve(); assert.equal(await saving, false);
  const save = fixture().options.save;
  f.options.save = async (base, document) => ({ ...await save(base, document) });
  assert.equal(await session.retry(), true); assert.equal(session.getSnapshot().value.text, 'saved');
  assert.equal(session.getSnapshot().value.version, '2'); session.dispose();
});

test('choosing the current server value still guards against an earlier request committing later', async () => {
  const f = fixture({ format: 1, base_version: '1', document: { text: 'last input', reviewed: false }, pending: { base_version: '1', document: { text: 'earlier request', reviewed: false } } });
  const session = f.create(); await session.load(); await session.reload(false);
  assert.equal(session.getSnapshot().value.text, 'saved'); assert.equal(session.getSnapshot().dirty, true);
  assert.ok(f.stored.pending); assert.equal(await session.flush(), true);
  assert.equal(f.remote.text, 'saved'); assert.equal(f.remote.version, '2'); session.dispose();
});

test('account suspension ignores late acknowledgements and resumes the original pending request safely', async () => {
  const f = fixture(), session = f.create(); await session.load(); const gate = deferred(), save = f.options.save;
  f.options.save = async (...args) => { const result = await save(...args); await gate.promise; return result; };
  session.edit({ text: 'submitted', reviewed: false }); const saving = session.flush();
  await tick();
  session.edit({ text: 'latest original account input', reviewed: false }); session.suspend(); gate.resolve();
  assert.equal(await saving, false); assert.equal(session.getSnapshot().value.text, 'latest original account input');
  f.options.save = save; session.resume(); await tick(); await session.flush();
  assert.equal(f.remote.text, 'latest original account input'); assert.equal(f.writes.length, 2);
  session.dispose();
});

test('corrupt recovery remains untouched while new in-memory editing is protected', async () => {
  const f = fixture(), session = f.create(); f.options.recovery.read = () => { throw new Error('invalid record'); };
  let overwrites = 0; f.options.recovery.save = () => { overwrites++; };
  await session.load(); session.edit({ text: 'new in-memory input', reviewed: false });
  assert.equal(await session.flush(), false); assert.equal(session.hasMemoryOnlyChanges(), true);
  assert.equal(session.getSnapshot().value.text, 'new in-memory input'); assert.equal(overwrites, 0); session.dispose();
});

test('storage failure remains memory-only until the server actually acknowledges saving', async () => {
  const f = fixture(), session = f.create(); await session.load();
  f.options.recovery.save = () => { throw new Error('quota'); };
  session.edit({ text: 'not durable yet', reviewed: false }); assert.equal(session.hasMemoryOnlyChanges(), true);
  assert.equal(session.getSnapshot().status, 'unsaved'); assert.equal(await session.flush(), true);
  assert.equal(f.remote.text, 'not durable yet'); assert.equal(session.hasMemoryOnlyChanges(), false); session.dispose();
});

test('explicit local-version choice reads the newest version before applying and keeps a backup', async () => {
  const f = fixture({ format: 1, base_version: '0', document: { text: 'chosen branch', reviewed: false } });
  const session = f.create(); await session.load(); f.remote = { version: '8', text: 'latest remote', reviewed: false };
  await session.reload(true); assert.equal(session.getSnapshot().value.text, 'chosen branch');
  assert.equal(await session.flush(), true); assert.equal(f.writes[0].base.version, '8');
  assert.ok(session.exportRecovered()); session.dispose();
});

test('server reload persists a backup before reading and preserves edits typed during that read', async () => {
  const f = fixture(), session = f.create(); await session.load();
  session.edit({ text: 'before reload', reviewed: false });
  const gate = deferred(); f.options.read = async () => { assert.equal(f.backups.length, 1); await gate.promise; return f.remote; };
  const reload = session.reload(false); await tick(); session.edit({ text: 'typed during reload', reviewed: false }); gate.resolve(); await reload;
  assert.equal(f.backups.at(-1).document.text, 'typed during reload');
  assert.equal(session.getSnapshot().value.text, 'saved'); assert.equal(f.stored, null); session.dispose();
});

test('backup failure or missing backup support cannot discard local edits', async () => {
  for (const backup of [undefined, () => { throw new Error('backup quota'); }]) {
    const f = fixture(), session = f.create(); await session.load(); session.edit({ text: 'must survive', reviewed: false });
    f.options.recovery.backup = backup;
    await assert.rejects(session.reload(false)); assert.equal(session.getSnapshot().value.text, 'must survive');
    assert.equal(f.stored.document.text, 'must survive'); session.dispose();
  }
});

test('a replaced local version remains downloadable after refreshing a clean resource', async () => {
  const f = fixture(), first = f.create(); await first.load(); first.edit({ text: 'previous local input', reviewed: false });
  await first.reload(false); assert.equal(f.stored, null); first.dispose();
  const refreshed = f.create(); await refreshed.load();
  assert.equal(refreshed.getSnapshot().status, 'saved'); assert.equal(refreshed.hasPrevious(), true);
  assert.equal(JSON.parse(refreshed.exportRecovered()).document.text, 'previous local input'); refreshed.dispose();
});

test('switching accounts during asynchronous request preparation cannot send a write', async () => {
  const f = fixture(), session = f.create(); await session.load(); const gate = deferred(), save = f.options.save;
  f.options.save = async (base, document, assertActive) => {
    await gate.promise; assertActive(); return save(base, document);
  };
  session.edit({ text: 'private original-account text', reviewed: false }); const saving = session.flush(); await tick();
  session.suspend(); const other = fixture().create(); other.resume(); await other.load(); gate.resolve();
  assert.equal(await saving, false); assert.equal(f.writes.length, 0);
  assert.equal(session.getSnapshot().value.text, 'private original-account text'); session.dispose(); other.dispose();
});
