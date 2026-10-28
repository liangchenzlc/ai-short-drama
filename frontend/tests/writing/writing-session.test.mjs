import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../../src/features/creation/writing/sessions/writing-session.ts', import.meta.url), 'utf8');
const compilerOptions = { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 };
const compiled = ts.transpileModule(source, { compilerOptions }).outputText;
const { WritingSession } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const initial = () => ({ project_id: '9', content_version: '1', active_writing_tasks: [], editing_script: null, confirmed_script_id: null });
const record = (id, content, state) => ({ id, content, updated_at: '2026-09-21T00:00:00Z', ...(state ? { state } : {}) });
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a; reject=b;}); return {promise,resolve,reject}; };
function transport() {
  let server = initial(); const writes = [];
  return { writes, get server() { return server; }, set server(v) {server=v;},
    async get() { return structuredClone(server); },
    async script(body) { writes.push(['script',body]); server={...server,content_version:String(BigInt(server.content_version)+1n),editing_script:record('12',body.content,body.content.trim() ? 'confirmed' : 'unconfirmed'),confirmed_script_id:body.content.trim() ? '12' : null}; return {content_version:server.content_version,script:server.editing_script,confirmed_script_id:server.confirmed_script_id}; },
  };
}

test('an imported draft survives refresh with its change source and clears it after acknowledgement', async () => {
  const api = transport(); let stored = null;
  const recovery = { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } };
  const first = new WritingSession(api, recovery); await first.load(); first.importScript('整篇导入'); first.dispose();
  assert.equal(stored.script_change_source, 'import');
  const restored = new WritingSession(api, recovery); await restored.load();
  await restored.flush();
  assert.equal(api.writes[0][1].change_source, 'import');
  restored.edit('script', '后续手工编辑'); await restored.flush();
  assert.equal(api.writes[1][1].change_source, undefined); restored.dispose();
});

test('a lost import response is reconciled without leaving the import marker on later edits', async () => {
  const api = transport(), save = api.script.bind(api); let once = true;
  api.script = async body => { const result = await save(body); if (once) { once = false; throw { status: 503 }; } return result; };
  const session = new WritingSession(api); await session.load(); session.importScript('整篇导入');
  assert.equal(await session.flush(), true);
  session.edit('script', '手工续写'); assert.equal(await session.flush(), true);
  assert.equal(api.writes[0][1].change_source, 'import'); assert.equal(api.writes[1][1].change_source, undefined);
  session.dispose();
});
test('same-base crash recovery resumes exact input and the save queue without confirmation', async () => {
  const api = transport(); let stored = null;
  const persistence = { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } };
  const original = new WritingSession(api, persistence); await original.load();
  original.edit('script', '崩溃前剧本'); original.dispose();
  const restored = new WritingSession(api, persistence); await restored.load();
  assert.equal(restored.getSnapshot().recoverable, false); assert.equal(api.writes.length, 0);
  assert.equal(restored.getSnapshot().script, '崩溃前剧本');
  await restored.flush(); assert.equal(stored, null); restored.dispose();
});
test('recovered draft cannot silently replace a newer server version', async () => {
  const api = transport(); const old = { base_version: '1', script: '本机稿', script_id: null };
  const persistence = { read: () => old, save() {}, clear() {} };
  api.server = { ...api.server, content_version: '2', editing_script: record('11', '其他窗口修改') };
  const session = new WritingSession(api, persistence); await session.load();
  assert.equal(session.getSnapshot().recoveryChanged, true);
  assert.equal(await session.restoreRecovered(), false); assert.equal(api.writes.length, 0);
  assert.equal(session.getSnapshot().script, '其他窗口修改');
  assert.equal(await session.restoreRecovered(true), true);
  assert.equal(session.getSnapshot().script, '本机稿'); assert.equal(session.getSnapshot().contentVersion, '2'); session.dispose();
});
test('corrupt recovery keeps server content readable and blocks edits until explicit discard', async () => {
  const api = transport(); api.server = { ...api.server, editing_script: record('11', '服务端正文') };
  let corrupt = true;
  const recovery = { read: () => { if (corrupt) throw new Error('corrupt'); return null; }, save() { throw new Error('must not overwrite'); }, clear: () => { corrupt = false; } };
  const session = new WritingSession(api, recovery);
  await session.load();
  assert.equal(session.getSnapshot().loaded, true);
  assert.equal(session.getSnapshot().recoveryBlocked, true);
  assert.equal(session.getSnapshot().script, '服务端正文');
  session.edit('script', '不能覆盖恢复记录'); assert.equal(await session.flush(), false);
  assert.equal(session.getSnapshot().script, '服务端正文'); assert.equal(corrupt, true);
  session.discardRecovered(); assert.equal(session.getSnapshot().recoveryBlocked, false);
  session.edit('script', '明确弃稿后编辑'); assert.equal(session.getSnapshot().script, '明确弃稿后编辑');
  session.dispose();
});
test('recovery is locked while fresh version is being checked', async () => {
  const api = transport(); const wait = deferred();
  const draft = { base_version: '0', script: '完整恢复稿', script_id: null };
  const session = new WritingSession(api, { read: () => draft, save() {}, clear() {} });
  await session.load(); api.get = async () => { await wait.promise; return structuredClone(api.server); };
  const restoring = session.restoreRecovered(true);
  assert.equal(session.getSnapshot().busy, true); assert.equal(await session.restoreRecovered(), false);
  wait.resolve(); assert.equal(await restoring, true); assert.equal(session.getSnapshot().busy, false);
  assert.equal(api.writes.length, 0); session.dispose();
});
test('empty GET stays empty; serial script saves share freshest string version and null script ID', async () => {
  const api=transport();const q=new WritingSession(api);await q.load();
  assert.equal(q.getSnapshot().scriptId,null);assert.equal(api.writes.length,0);
  q.edit('script','  script\n');await q.flush();q.edit('script','script');await q.flush();
  assert.deepEqual(api.writes,[['script',{content:'  script\n',script_id:null,content_version:'1'}],['script',{content:'script',script_id:'12',content_version:'2'}]]);
  assert.equal(q.getSnapshot().dirty,false);q.dispose();
});
test('late acknowledgement preserves typing and coalesces edits before the next serial save', async () => {
  const api=transport(),wait=deferred(),save=api.script.bind(api);api.script=async b=>{await wait.promise;return save(b);};
  const q=new WritingSession(api);await q.load();q.edit('script','old');const flush=q.flush();
  q.edit('script','middle');q.edit('script','new');wait.resolve();await flush;
  assert.equal(q.getSnapshot().script,'new');assert.deepEqual(api.writes.map(w=>w[1]),[{content:'old',content_version:'1',script_id:null},{content:'new',content_version:'2',script_id:'12'}]);q.dispose();
});
test('409 pauses queue and retries cannot silently overwrite another writer', async () => {
  const api=transport();api.script=async()=>{throw {status:409};};const q=new WritingSession(api);await q.load();q.edit('script','mine');
  assert.equal(await q.flush(),false);assert.equal(q.getSnapshot().status,'conflict');assert.equal(q.getSnapshot().script,'mine');
  assert.equal(await q.retry(),false);q.dispose();
});

test('explicit server reload replaces a conflicting dirty draft only after the latest read succeeds', async () => {
  const api = transport(); let stored = null;
  const persistence = { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } };
  const session = new WritingSession(api, persistence); await session.load();
  api.script = async () => { throw { status: 409 }; };
  session.edit('script', '本机冲突稿'); await session.flush();
  assert.equal(await session.load(true), true); assert.equal(session.getSnapshot().script, '本机冲突稿');
  api.server = { ...api.server, content_version: '2', editing_script: record('11', '当前服务端正文') };
  const wait = deferred(); api.get = async () => { await wait.promise; return structuredClone(api.server); };
  const original = structuredClone(stored);
  const reloading = session.reloadFromServer();
  assert.equal(session.getSnapshot().busy, true);
  session.edit('script', '读取期间不接受输入');
  assert.equal(session.getSnapshot().script, '本机冲突稿'); assert.deepEqual(stored, original);
  wait.resolve(); assert.equal(await reloading, true);
  assert.equal(session.getSnapshot().script, '当前服务端正文'); assert.equal(session.getSnapshot().contentVersion, '2');
  assert.equal(session.getSnapshot().dirty, false); assert.equal(session.getSnapshot().status, 'saved');
  assert.equal(stored, null); session.dispose();
});

test('failed explicit server reload retains dirty input and its original local record', async () => {
  const api = transport(); let stored = null;
  const session = new WritingSession(api, { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } });
  await session.load(); session.edit('script', '读取失败不能清除的本机稿');
  const original = structuredClone(stored); api.get = async () => { throw new Error('offline'); };
  assert.equal(await session.reloadFromServer(), false);
  assert.equal(session.getSnapshot().script, '读取失败不能清除的本机稿');
  assert.equal(session.getSnapshot().dirty, true); assert.equal(session.getSnapshot().busy, false);
  assert.deepEqual(stored, original); session.dispose();
});

test('failed or stale explicit reload preserves a changed-base recovery record', async () => {
  const api = transport(); api.server = { ...api.server, content_version: '2', editing_script: record('11', '服务端修改') };
  const original = { base_version: '1', script: '原恢复记录', script_id: null };
  let stored = structuredClone(original);
  const session = new WritingSession(api, { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } });
  await session.load(); api.get = async () => { throw new Error('offline'); };
  assert.equal(await session.reloadFromServer(), false);
  assert.equal(session.getSnapshot().recoverable, true); assert.deepEqual(stored, original);
  const wait = deferred(); api.get = async () => { await wait.promise; return structuredClone(api.server); };
  const reloading = session.reloadFromServer(); session.suspend(); wait.resolve();
  assert.equal(await reloading, false); assert.equal(session.getSnapshot().recoverable, true);
  assert.deepEqual(stored, original); session.dispose();
});
test('lost success response reconciles exact next version then saves newer draft', async () => {
  const api=transport(),save=api.script.bind(api);let first=true;api.script=async b=>{const r=await save(b);if(first){first=false;throw {status:503};}return r;};
  const q=new WritingSession(api);await q.load();q.edit('script','saved');assert.equal(await q.flush(),true);assert.equal(api.writes.length,1);assert.equal(q.getSnapshot().dirty,false);q.dispose();
});
test('uncertain response with unrelated server change becomes conflict without overwriting draft', async () => {
  const api=transport();api.script=async()=>{api.server={...api.server,content_version:'8',novel:record('11','other')};throw {};};
  const q=new WritingSession(api);await q.load();q.edit('script','mine');assert.equal(await q.flush(),false);assert.equal(q.getSnapshot().status,'conflict');assert.equal(q.getSnapshot().script,'mine');q.dispose();
});
test('navigation barrier waits for writes; dispose prevents coalesced writes after unmount', async () => {
  const api=transport(),wait=deferred(),save=api.script.bind(api);api.script=async b=>{await wait.promise;return save(b);};
  const q=new WritingSession(api);await q.load();q.edit('script','one');const barrier=q.flush();q.edit('script','two');q.dispose();wait.resolve();assert.equal(await barrier,false);assert.equal(api.writes.length,1);
});
test('failed initial GET never enables editing and can be explicitly reloaded', async () => {
  const api=transport(),get=api.get;api.get=async()=>{throw {};};const q=new WritingSession(api);await q.load();q.edit('script','ignored');assert.equal(q.getSnapshot().loaded,false);assert.equal(q.getSnapshot().script,'');api.get=get;await q.load();assert.equal(q.getSnapshot().loaded,true);q.dispose();
});
test('network failure before commit pauses until explicit retry rechecks server version', async () => {
  const api=transport(),save=api.script.bind(api);api.script=async()=>{throw {};};const q=new WritingSession(api);await q.load();q.edit('script','keep');assert.equal(await q.flush(),false);
  assert.equal(q.getSnapshot().dirty,true);assert.equal(q.getSnapshot().status,'error');api.script=save;assert.equal(await q.retry(),true);assert.equal(api.server.editing_script.content,'keep');q.dispose();
});
test('uncertain committed save remains paused after draft returns to stale snapshot and retry saves intended text', async () => {
  const api=transport();api.server={...api.server,novel:record('11','A')};const get=api.get.bind(api),save=api.script.bind(api);
  const q=new WritingSession(api);await q.load();api.script=async body=>{await save(body);throw {};};api.get=async()=>{throw {};};
  q.edit('script','B');assert.equal(await q.flush(),false);q.edit('script','A');assert.equal(q.getSnapshot().dirty,true);assert.equal(q.getSnapshot().status,'error');assert.equal(await q.flush(),false);assert.equal(api.server.editing_script.content,'B');
  api.get=get;api.script=save;assert.equal(await q.retry(),true);assert.equal(api.server.editing_script.content,'A');assert.equal(q.getSnapshot().status,'saved');q.dispose();
});

test('AI refresh preserves dirty non-target input and safely rebases its save version', async () => {
  const api = transport(), session = new WritingSession(api);
  await session.load();
  session.edit('script', '正在手写的剧本');
  api.server = { ...api.server, content_version: '2', editing_script: null, confirmed_script_id: null };
  assert.equal(await session.refreshFromServer(), true);
  assert.equal(session.getSnapshot().script, '正在手写的剧本');
  assert.equal(session.getSnapshot().contentVersion, '2');
  await session.flush();
  assert.deepEqual(api.writes, [['script', { content: '正在手写的剧本', content_version: '2', script_id: null }]]);
  session.dispose();
});

test('AI refresh cannot rebase a changed server baseline over local input', async () => {
  const api = transport(), session = new WritingSession(api);
  await session.load(); session.edit('script', '本机剧本');
  api.server = { ...api.server, content_version: '2', editing_script: record('12', '其他窗口剧本') };
  assert.equal(await session.refreshFromServer(), false);
  assert.equal(session.getSnapshot().script, '本机剧本');
  assert.equal(session.getSnapshot().status, 'conflict');
  assert.equal(session.getSnapshot().contentVersion, '1');
  assert.equal(await session.flush(), false); session.dispose();
});

test('active rewrite tasks lock the current script until settlement', async () => {
  const api = transport(), session = new WritingSession(api); await session.load();
  session.trackTask({ task_id: '91', target: 'script', operation: 'script_rewrite', status: 'running' });
  session.edit('script', '不应写入');
  assert.equal(session.getSnapshot().script, '');
  await session.flush(); assert.deepEqual(api.writes.map(item => item[0]), []);
  assert.equal(session.getSnapshot().confirmed, false); session.dispose();
});

test('editing the same plain text does not enqueue another save', async () => {
  const api = transport(), session = new WritingSession(api);
  await session.load(); session.edit('script', '第一场\n主角：你好\n\n'); await session.flush();
  session.edit('script', '第一场\n主角：你好\n\n');
  assert.equal(session.getSnapshot().dirty, false);
  await session.flush();
  assert.equal(api.writes.length, 1);
  assert.equal(api.server.editing_script.content, '第一场\n主角：你好\n\n');
  session.dispose();
});

test('lost writing receipt across reload is reconciled before saving newer retained input', async () => {
  const api = transport(), gate = deferred(); let stored = null;
  const persistence = { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } };
  const save = api.script.bind(api);
  api.script = async body => { const result = await save(body); await gate.promise; return result; };
  const first = new WritingSession(api, persistence); await first.load(); first.edit('script', 'first');
  const writing = first.flush(); first.edit('script', 'newer'); first.dispose(); gate.resolve(); await writing;
  api.script = save;
  const next = new WritingSession(api, persistence); await next.load();
  assert.equal(next.getSnapshot().recoverable, false); assert.equal(next.getSnapshot().script, 'newer');
  assert.equal(await next.flush(), true); assert.equal(api.server.editing_script.content, 'newer');
  assert.deepEqual(api.writes.map(item => item[1].content_version), ['1', '2']); next.dispose();
});

test('durably retained writing does not need document-exit protection; memory-only writing does', async () => {
  const api = transport(); const session = new WritingSession(api, { read: () => null, save() {}, clear() {} });
  await session.load(); session.edit('script', 'retained'); assert.equal(session.hasMemoryOnlyChanges(), false); session.dispose();
  const memory = new WritingSession(api, { read: () => null, save() { throw new Error('quota'); }, clear() {} });
  await memory.load(); memory.edit('script', 'in memory'); assert.equal(memory.hasMemoryOnlyChanges(), true);
  await memory.flush(); assert.equal(memory.hasMemoryOnlyChanges(), false); memory.dispose();
});

test('recovered pending writing is not cleared when latest input equals the old server baseline', async () => {
  const api = transport(); api.server.editing_script = record('11', 'base');
  const before = structuredClone(api.server);
  let stored = { base_version: '1', script: 'base', script_id: '11',
    pending: { before, field: 'script', content: 'older in-flight input' } };
  const session = new WritingSession(api, { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } });
  await session.load();
  assert.equal(session.getSnapshot().dirty, true); assert.notEqual(stored, null);
  assert.equal(await session.flush(), true);
  assert.equal(api.writes[0][1].content, 'base'); assert.equal(api.writes[0][1].content_version, '1');
  assert.equal(api.server.editing_script.content, 'base'); session.dispose();
});

for (const field of ['script']) for (const olderWins of [false, true]) {
  test(`explicit ${field} replacement fences an unknown older request when ${olderWins ? 'older' : 'replacement'} commits first`, async () => {
    const api = transport();
    api.server = { ...api.server, editing_script: record('12', 'base', 'unconfirmed') };
    const save = api[field].bind(api); let older = null; let stored = null;
    const commit = async body => {
      if (body.content_version !== api.server.content_version) throw { status: 409 };
      return save(body);
    };
    api[field] = async body => { if (!older) { older = structuredClone(body); throw { status: 503 }; } return commit(body); };
    const session = new WritingSession(api, { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } });
    try {
      await session.load(); session.edit(field, 'older request'); assert.equal(await session.flush(), false);
      assert.equal(await session.reloadFromServer(), true);
      assert.equal(session.getSnapshot()[field], 'base'); assert.equal(session.getSnapshot().dirty, true);
      assert.equal(stored.pending.content, 'older request');
      if (olderWins) {
        await commit(older); assert.equal(await session.flush(), false);
        assert.equal(session.getSnapshot().status, 'conflict'); assert.equal(session.getSnapshot()[field], 'base');
        assert.notEqual(stored, null);
      } else {
        assert.equal(await session.flush(), true);
        await assert.rejects(commit(older), error => error.status === 409);
        assert.equal(api.server['editing_script'].content, 'base');
        assert.equal(stored, null);
      }
    } finally { session.dispose(); }
  });
}

test('explicit server replacement retains pending information embedded in a differing-base recovery record', async () => {
  const api = transport(); api.server.editing_script = record('11', 'base');
  const before = structuredClone(api.server);
  let stored = { base_version: '0', script: 'recovered input', script_id: null,
    pending: { before, field: 'script', content: 'older request' } };
  const session = new WritingSession(api, { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } });
  try {
    await session.load(); assert.equal(session.getSnapshot().recoverable, true);
    assert.equal(await session.reloadFromServer(), true);
    assert.equal(session.getSnapshot().recoverable, false); assert.equal(session.getSnapshot().dirty, true);
    assert.equal(stored.pending.content, 'older request');
    assert.equal(await session.flush(), true);
    assert.equal(api.writes[0][1].content, 'base'); assert.equal(api.writes[0][1].content_version, '1');
  } finally { session.dispose(); }
});

test('explicit server replacement after a failed initial GET fences the pending request still on disk', async () => {
  const api = transport(); api.server.editing_script = record('11', 'base');
  const before = structuredClone(api.server), get = api.get.bind(api), save = api.script.bind(api);
  const older = { content: 'older request', content_version: '1' };
  let stored = { base_version: '1', script: 'retained input', script_id: null,
    pending: { before, field: 'script', content: older.content } };
  api.get = async () => { throw new Error('offline'); };
  api.script = async body => {
    if (body.content_version !== api.server.content_version) throw { status: 409 };
    return save(body);
  };
  const session = new WritingSession(api, { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } });
  try {
    assert.equal(await session.load(), false); assert.equal(session.getSnapshot().loaded, false);
    api.get = get;
    assert.equal(await session.reloadFromServer(), true);
    assert.equal(session.getSnapshot().script, 'base'); assert.equal(session.getSnapshot().dirty, true);
    assert.equal(stored.pending.content, 'older request');
    assert.equal(await session.flush(), true);
    assert.deepEqual(api.writes[0], ['script', { content: 'base', content_version: '1', script_id: '11' }]);
    await assert.rejects(api.script(older), error => error.status === 409);
    assert.equal(api.server.editing_script.content, 'base'); assert.equal(stored, null);
  } finally { session.dispose(); }
});

test('explicit server replacement clears an unknown request after observing its advanced version', async () => {
  const api = transport(); api.server.editing_script = record('11', 'base');
  const save = api.script.bind(api); let older;
  api.script = async body => { older = body; throw { status: 503 }; };
  const session = new WritingSession(api);
  try {
    await session.load(); session.edit('script', 'accepted older input'); await session.flush();
    await save(older); assert.equal(await session.reloadFromServer(), true);
    assert.equal(session.getSnapshot().script, 'accepted older input'); assert.equal(session.getSnapshot().dirty, false);
    assert.equal(session.getSnapshot().status, 'saved'); assert.equal(api.writes.length, 1);
  } finally { session.dispose(); }
});
