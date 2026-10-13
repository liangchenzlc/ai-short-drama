import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const source = readFileSync(new URL('../src/features/projects/creation-draft-session.ts', import.meta.url), 'utf8');
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
  data.session.edit({ input_text: 'saved despite timeout' }); assert.equal(await data.session.flush(), null);
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
