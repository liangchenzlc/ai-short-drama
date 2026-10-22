import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
const source = readFileSync(new URL('../../src/features/projects/creation-draft-session.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { CreationDraftSession } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const value = (input_text = '') => ({ model_config_id: '7', input_text, image_settings: { count: 1, aspect: '16:9', resolution: '2K' }, video_settings: null, reference_media_ids: [] });
const saved = (input, version = '1') => ({ ...value(input), id: '100', row_version: version, draft_version: version, object_id: '9007199254740999' });
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
function setup(remote = saved('old')) {
  let server = remote, stored = null;
  const writes = [];
  const api = { get: async () => structuredClone(server),
    create: async input => { writes.push(input); return server = { ...saved(input.input_text), ...input }; },
    update: async (version, input) => { writes.push({ version, ...input }); return server = { ...saved(input.input_text, String(BigInt(version) + 1n)), ...input }; } };
  const persistence = { read: () => structuredClone(stored), save: input => { stored = structuredClone(input); }, clear: () => { stored = null; } };
  const session = new CreationDraftSession(api, persistence, value());
  return { api, session, persistence, writes, get stored() { return stored; }, set stored(input) { stored = input; }, get server() { return server; }, set server(input) { server = input; } };
}
test('private draft serializes writes and retains input typed during a response', async () => {
  const data = setup(); await data.session.load();
  const gate = deferred(); let first = true;
  const update = data.api.update;
  data.api.update = async (version, input) => { if (first) { first = false; await gate.promise; } return update(version, input); };
  data.session.edit({ input_text: 'first' }); const saving = data.session.flush();
  data.session.edit({ input_text: 'newer' }); gate.resolve();
  const result = await saving;
  assert.equal(result.input_text, 'newer'); assert.deepEqual(data.writes.map(item => item.version), ['1', '2']);
  assert.equal(data.session.getSnapshot().value.input_text, 'newer'); assert.equal(data.stored, null); data.session.dispose();
});
test('409 keeps private input and prevents all automatic writes until explicit reload', async () => {
  const data = setup(); await data.session.load(); let calls = 0;
  data.api.update = async () => { calls++; throw { status: 409 }; };
  data.session.edit({ input_text: 'local' }); assert.equal(await data.session.flush(), null);
  data.session.edit({ input_text: 'still local' }); assert.equal(await data.session.flush(), null);
  assert.equal(await data.session.retry(), null); assert.equal(calls, 1); assert.equal(data.session.getSnapshot().status, 'conflict');
  assert.equal(data.stored.value.input_text, 'still local'); data.session.dispose();
});
test('uncertain create is reconciled by scope before retry and never creates twice', async () => {
  const data = setup(null); await data.session.load(); let creates = 0;
  data.api.create = async input => { creates++; data.server = { ...saved(input.input_text), ...input }; throw new Error('network timeout'); };
  data.session.edit({ input_text: 'saved despite timeout' }); assert.equal((await data.session.flush()).input_text, 'saved despite timeout');
  const result = await data.session.retry(); assert.equal(result.input_text, 'saved despite timeout'); assert.equal(creates, 1);
  assert.equal(data.session.getSnapshot().status, 'saved'); data.session.dispose();
});
test('restored input pauses when another window changed the server version', async () => {
  const data = setup(saved('remote', '8')); data.stored = { row_version: '1', value: value('recovery') };
  await data.session.load(); assert.equal(data.session.getSnapshot().value.input_text, 'recovery');
  assert.equal(await data.session.flush(), null); assert.equal(data.writes.length, 0);
  await data.session.load(true); assert.equal(data.session.getSnapshot().value.input_text, 'remote'); data.session.dispose();
});
test('in-flight response cannot update a disposed account or object session', async () => {
  const data = setup(); await data.session.load(); const gate = deferred();
  data.api.update = () => gate.promise;
  data.session.edit({ input_text: 'private' }); const saving = data.session.flush(); data.session.dispose();
  gate.resolve(saved('old actor receipt', '2')); assert.equal(await saving, null);
  assert.equal(data.session.getSnapshot().value.input_text, 'private');
});
test('format and model-only changes save and references deduplicate permanent IDs', async () => {
  const data = setup(); await data.session.load();
  data.session.edit({ model_config_id: '9007199254740999', reference_media_ids: ['17', '17', '18'] });
  await data.session.flush(); assert.equal(data.writes[0].model_config_id, '9007199254740999');
  assert.deepEqual(data.writes[0].reference_media_ids, ['17', '18']); data.session.dispose();
});

test('explicit server reload locks editing until its delayed response arrives', async () => {
  const data = setup(); await data.session.load();
  data.session.edit({ input_text: 'kept until confirmation' });
  const gate = deferred(); data.api.get = () => gate.promise;
  const reload = data.session.load(true);
  assert.equal(data.session.getSnapshot().loaded, false);
  data.session.edit({ input_text: 'input during reload' });
  assert.equal(data.session.getSnapshot().value.input_text, 'kept until confirmation');
  assert.equal(await data.session.flush(), null);
  gate.resolve(saved('server after confirmation', '2')); await reload;
  assert.equal(data.session.getSnapshot().loaded, true);
  assert.equal(data.session.getSnapshot().value.input_text, 'server after confirmation');
  assert.equal(data.stored, null); data.session.dispose();
});

for (const lateResult of ['response', 'failure']) test(`explicit server reload ignores an older retry ${lateResult}`, async () => {
  const data = setup(); await data.session.load();
  data.api.update = async () => { throw new Error('save timeout'); };
  data.session.edit({ input_text: 'local input before server reload' });
  assert.equal(await data.session.flush(), null);
  const gate = deferred(); data.api.get = () => gate.promise;
  const retry = data.session.retry();
  data.api.get = async () => saved('confirmed server input', '2');
  await data.session.load(true);
  if (lateResult === 'response') gate.resolve(saved('older server input', '1'));
  else gate.reject(new Error('late lookup failed'));
  assert.equal(await retry, null);
  assert.equal(data.session.getSnapshot().value.input_text, 'confirmed server input');
  assert.equal(data.session.getSnapshot().saved.row_version, '2');
  assert.equal(data.session.getSnapshot().status, 'saved');
  assert.equal(data.session.getSnapshot().message, '');
  assert.equal(data.stored, null); data.session.dispose();
});

test('disposed private draft ignores a delayed retry failure', async () => {
  const data = setup(); await data.session.load();
  const gate = deferred(); data.api.get = () => gate.promise;
  const retry = data.session.retry();
  data.session.dispose(); gate.reject(new Error('late lookup failed'));
  assert.equal(await retry, null);
  assert.equal(data.session.getSnapshot().status, 'saved');
  assert.equal(data.session.getSnapshot().message, '');
});

test('untouched parameters without a server record are idle and do not create recovery records', async () => {
  const data = setup(null); await data.session.load();
  assert.equal(data.session.getSnapshot().status, 'idle');
  assert.equal(data.session.hasUnsettled(), false); assert.equal(data.stored, null); assert.equal(data.writes.length, 0);
  data.session.dispose();
});

test('same-base parameter recovery resumes its serial save queue automatically', async () => {
  const data = setup(); data.stored = { row_version: '1', value: value('recovered input') };
  await data.session.load();
  assert.equal(data.session.getSnapshot().status, 'unsaved');
  assert.equal(data.session.getSnapshot().value.input_text, 'recovered input');
  assert.equal((await data.session.flush()).input_text, 'recovered input'); assert.equal(data.stored, null);
  data.session.dispose();
});

test('account suspension stops coalesced private writes until the original account resumes and reconciles', async () => {
  const data = setup(), gate = deferred(); await data.session.load();
  const update = data.api.update;
  data.api.update = async (...args) => { await gate.promise; return update(...args); };
  data.session.edit({ input_text: 'first' }); const saving = data.session.flush();
  data.session.edit({ input_text: 'newer' }); data.session.suspend(); gate.resolve();
  assert.equal(await saving, null); assert.equal(data.writes.length, 1);
  assert.equal(await data.session.flush(), null); assert.equal(data.session.getSnapshot().value.input_text, 'newer');
  data.session.resume(); await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(data.server.input_text, 'newer'); assert.equal(data.writes.length, 2); data.session.dispose();
});

test('recovered pending parameters CAS the latest input even when it equals the old server baseline', async () => {
  const data = setup(saved('base'));
  data.stored = { row_version: '1', value: value('base'), pending: { version: '1', value: value('older in-flight input') } };
  await data.session.load();
  assert.equal(data.session.getSnapshot().status, 'unsaved'); assert.notEqual(data.stored, null);
  await data.session.flush();
  assert.equal(data.writes.length, 1); assert.equal(data.writes[0].version, '1'); assert.equal(data.writes[0].input_text, 'base');
  data.session.dispose();
});

for (const winner of ['selected', 'older']) test(`explicit private reload keeps its selected input when the ${winner} update wins`, async () => {
  const data = setup(saved('base')), gate = deferred(), backups = [];
  data.persistence.backup = input => backups.push(structuredClone(input));
  const commit = (version, input) => {
    if (version !== data.server.row_version) throw { status: 409 };
    return data.server = { ...saved(input.input_text, String(BigInt(version) + 1n)), ...input };
  };
  let calls = 0;
  data.api.update = async (version, input) => {
    data.writes.push({ version, ...input });
    if (++calls === 1) {
      await gate.promise;
      if (winner === 'selected') throw new Error('receipt timeout while request remains in transit');
    }
    return commit(version, input);
  };
  await data.session.load();
  data.session.edit({ input_text: 'older in-flight input' }); const older = data.session.flush();
  await data.session.load(true);
  assert.equal(data.session.getSnapshot().value.input_text, 'base');
  assert.equal(data.session.getSnapshot().status, 'unsaved');
  assert.equal(data.stored.pending.value.input_text, 'older in-flight input');
  assert.equal(backups[0].value.input_text, 'older in-flight input');
  const selected = data.session.flush(); gate.resolve();
  assert.equal(await older, null);
  const result = await selected;
  assert.deepEqual(data.writes.map(input => [input.version, input.input_text]), [['1', 'older in-flight input'], ['1', 'base']]);
  if (winner === 'selected') {
    assert.equal(result.input_text, 'base');
    assert.equal(data.session.getSnapshot().status, 'saved');
    assert.equal(data.stored, null);
    assert.throws(() => commit('1', value('older in-flight input')), error => error.status === 409);
    assert.equal(data.server.input_text, 'base');
  } else {
    assert.equal(result, null);
    assert.equal(data.server.input_text, 'older in-flight input');
    assert.equal(data.session.getSnapshot().value.input_text, 'base');
    assert.equal(data.session.getSnapshot().status, 'conflict');
    assert.equal(data.stored.value.input_text, 'base');
    assert.equal(data.stored.pending.version, '1');
  }
  data.session.dispose();
});

test('private reload after an absent scoped read reconciles a late create without creating twice', async () => {
  const data = setup(null), gate = deferred(), backups = [];
  data.persistence.backup = input => backups.push(structuredClone(input));
  let creates = 0;
  data.api.create = async input => {
    creates++; await gate.promise;
    assert.equal(data.server, null);
    return data.server = { ...saved(input.input_text), ...input };
  };
  await data.session.load();
  data.session.edit({ input_text: 'older create' }); const older = data.session.flush();
  data.session.edit({ input_text: 'selected after absent read' });
  await data.session.load(true);
  assert.equal(data.session.getSnapshot().status, 'unsaved');
  assert.equal(data.stored.pending.version, null);
  const selected = data.session.flush(); gate.resolve();
  assert.equal(await older, null);
  assert.equal((await selected).input_text, 'selected after absent read');
  assert.equal(creates, 1); assert.equal(data.writes.length, 1); assert.equal(data.writes[0].version, '1');
  assert.equal(data.server.input_text, 'selected after absent read');
  assert.equal(data.stored, null); assert.equal(backups[0].pending.value.input_text, 'older create');
  data.session.dispose();
});

test('private reload preserves recovered pending create until the same scope is reconciled', async () => {
  const data = setup(null), backups = [];
  data.persistence.backup = input => backups.push(structuredClone(input));
  data.stored = { row_version: null, value: value('selected input'), pending: { version: null, value: value('older create') } };
  await data.session.load(); await data.session.load(true);
  assert.equal(data.session.getSnapshot().status, 'unsaved');
  data.server = saved('older create');
  assert.equal((await data.session.flush()).input_text, 'selected input');
  assert.equal(data.writes.length, 1); assert.equal(data.writes[0].version, '1');
  assert.equal(backups[0].pending.value.input_text, 'older create');
  data.session.dispose();
});

test('failed private reload backup keeps input and its uncertain request intact', async () => {
  const data = setup(); await data.session.load();
  data.api.update = async () => { throw new Error('request timeout'); };
  data.session.edit({ input_text: 'protected input' }); assert.equal(await data.session.flush(), null);
  const recovery = structuredClone(data.stored);
  data.persistence.backup = () => { throw new Error('backup storage full'); };
  await data.session.load(true);
  assert.equal(data.session.getSnapshot().status, 'error');
  assert.equal(data.session.getSnapshot().value.input_text, 'protected input');
  assert.deepEqual(data.stored, recovery);
  data.session.dispose();
});

test('private reload after an initial read failure retains the persisted request fence', async () => {
  const data = setup(saved('base'));
  data.stored = { row_version: '1', value: value('local'), pending: { version: '1', value: value('older input') } };
  const read = data.api.get; data.api.get = async () => { throw new Error('offline'); };
  await data.session.load(); data.api.get = read;
  await data.session.load(true);
  assert.equal(data.session.getSnapshot().status, 'unsaved');
  assert.equal(data.stored.pending.value.input_text, 'older input');
  assert.equal((await data.session.flush()).input_text, 'base');
  assert.equal(data.writes[0].version, '1');
  data.session.dispose();
});

test('private draft hook backs up a damaged original record before explicit server replacement', async () => {
  const hookSource = readFileSync(new URL('../../src/features/projects/useCreationDraft.ts', import.meta.url), 'utf8');
  const hookCompiled = ts.transpileModule(hookSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const storage = new Map(), scope = '7:generation-draft:10:30:storyboard_image';
  storage.set(scope, '{damaged-original-record');
  const exports = {};
  const providers = {
    react: { useEffect() {}, useMemo: factory => factory(), useRef: current => ({ current }), useSyncExternalStore: (_subscribe, snapshot) => snapshot() },
    '../../api/modules/creation-drafts': { creationDraftsApi: () => ({ get: async () => saved('server input') }) },
    '../auth/AuthSession': { useAuth: () => ({ user: { id: '7' }, enabled: true }) },
    './creation-draft-session': { CreationDraftSession },
    './creation-recovery': {
      creationScope: (key, actor) => `${actor}:${key}`,
      readRecovery: () => { throw new Error('damaged recovery'); },
      readRecoveryText: key => storage.get(key) ?? null,
      saveRecovery: (key, input) => storage.set(key, structuredClone(input)),
      clearRecovery: key => storage.delete(key),
    },
    './standard-session-registry': { retainStandardSession: (_actor, _key, factory) => factory(), releaseStandardSession() {} },
  };
  vm.runInNewContext(hookCompiled, { exports, require: key => { assert.ok(providers[key], key); return providers[key]; } });
  const hook = exports.useCreationDraft({ projectId: '10', objectId: '30', purpose: 'storyboard_image' });
  await hook.session.load(); assert.equal(hook.session.getSnapshot().status, 'error');
  await hook.reload();
  assert.equal(storage.get(`${scope}:previous`).recovery, '{damaged-original-record');
  assert.equal(storage.get(`${scope}:previous`).current.pending, null);
  assert.equal(storage.has(scope), false);
  assert.equal(hook.session.getSnapshot().value.input_text, 'server input');
  hook.session.dispose();
});
