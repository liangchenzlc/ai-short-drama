import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../../src/features/projects/writing-session.ts', import.meta.url), 'utf8');
const compilerOptions = { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 };
const helpers = ts.transpileModule(readFileSync(new URL('../../src/features/projects/script-document.ts', import.meta.url), 'utf8'), { compilerOptions }).outputText;
const helperUrl = `data:text/javascript;base64,${Buffer.from(helpers).toString('base64')}`;
const compiled = ts.transpileModule(source, { compilerOptions }).outputText.replace("'./script-document'", JSON.stringify(helperUrl));
const { WritingSession } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const initial = () => ({ episode_id: '9', content_version: '1', novel: null, editing_script: null, confirmed_script_id: null });
const record = (id, content, state) => ({ id, content, updated_at: '2026-09-21T00:00:00Z', ...(state ? { state } : {}) });
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a; reject=b;}); return {promise,resolve,reject}; };
function transport() {
  let server = initial(); const writes = [];
  return { writes, get server() { return server; }, set server(v) {server=v;},
    async get() { return structuredClone(server); },
    async novel(body) { writes.push(['novel',body]); server = {...server, content_version: String(BigInt(server.content_version)+1n), novel: record('11',body.content)}; return {content_version:server.content_version,novel:server.novel}; },
    async script(body) { writes.push(['script',body]); server={...server,content_version:String(BigInt(server.content_version)+1n),editing_script:{...record('12',body.content,'unconfirmed'),editor_document:body.editor_document ?? null},confirmed_script_id:null}; return {content_version:server.content_version,script:server.editing_script}; },
    async confirm(id,body) { writes.push(['confirm',id,body]); server={...server,content_version:String(BigInt(server.content_version)+1n),editing_script:{...server.editing_script,state:'confirmed'},confirmed_script_id:id};return structuredClone(server); },
    async select() { throw new Error('unused'); },
  };
}
test('same-base crash recovery resumes exact input and the save queue without confirmation', async () => {
  const api = transport(); let stored = null;
  const persistence = { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } };
  const original = new WritingSession(api, persistence); await original.load();
  original.edit('novel', '崩溃前小说'); original.edit('script', '崩溃前剧本'); original.dispose();
  const restored = new WritingSession(api, persistence); await restored.load();
  assert.equal(restored.getSnapshot().recoverable, false); assert.equal(api.writes.length, 0);
  assert.equal(restored.getSnapshot().novel, '崩溃前小说'); assert.equal(restored.getSnapshot().script, '崩溃前剧本');
  await restored.flush(); assert.equal(stored, null); restored.dispose();
});
test('recovered draft cannot silently replace a newer server version', async () => {
  const api = transport(); const old = { base_version: '1', novel: '本机稿', script: '', script_id: null };
  const persistence = { read: () => old, save() {}, clear() {} };
  api.server = { ...api.server, content_version: '2', novel: record('11', '其他窗口修改') };
  const session = new WritingSession(api, persistence); await session.load();
  assert.equal(session.getSnapshot().recoveryChanged, true);
  assert.equal(await session.restoreRecovered(), false); assert.equal(api.writes.length, 0);
  assert.equal(session.getSnapshot().novel, '其他窗口修改');
  assert.equal(await session.restoreRecovered(true), true);
  assert.equal(session.getSnapshot().novel, '本机稿'); assert.equal(session.getSnapshot().contentVersion, '2'); session.dispose();
});
test('corrupt recovery keeps server content readable and blocks edits until explicit discard', async () => {
  const api = transport(); api.server = { ...api.server, novel: record('11', '服务端正文') };
  let corrupt = true;
  const recovery = { read: () => { if (corrupt) throw new Error('corrupt'); return null; }, save() { throw new Error('must not overwrite'); }, clear: () => { corrupt = false; } };
  const session = new WritingSession(api, recovery);
  await session.load();
  assert.equal(session.getSnapshot().loaded, true);
  assert.equal(session.getSnapshot().recoveryBlocked, true);
  assert.equal(session.getSnapshot().novel, '服务端正文');
  session.edit('novel', '不能覆盖恢复记录'); assert.equal(await session.flush(), false);
  assert.equal(session.getSnapshot().novel, '服务端正文'); assert.equal(corrupt, true);
  session.discardRecovered(); assert.equal(session.getSnapshot().recoveryBlocked, false);
  session.edit('novel', '明确弃稿后编辑'); assert.equal(session.getSnapshot().novel, '明确弃稿后编辑');
  session.dispose();
});
test('recovery is locked while fresh version is being checked', async () => {
  const api = transport(); const wait = deferred();
  const draft = { base_version: '0', novel: '完整恢复稿', script: '', script_id: null };
  const session = new WritingSession(api, { read: () => draft, save() {}, clear() {} });
  await session.load(); api.get = async () => { await wait.promise; return structuredClone(api.server); };
  const restoring = session.restoreRecovered(true);
  assert.equal(session.getSnapshot().busy, true); assert.equal(await session.restoreRecovered(), false);
  wait.resolve(); assert.equal(await restoring, true); assert.equal(session.getSnapshot().busy, false);
  assert.equal(api.writes.length, 0); session.dispose();
});
test('empty GET stays empty; serial novel/script saves share freshest string version and null script ID', async () => {
  const api=transport();const q=new WritingSession(api);await q.load();
  assert.equal(q.getSnapshot().scriptId,null);assert.equal(api.writes.length,0);
  q.edit('novel','  novel\n');q.edit('script','script');await q.flush();
  assert.deepEqual(api.writes,[['novel',{content:'  novel\n',content_version:'1'}],['script',{content:'script',script_id:null,content_version:'2'}]]);
  assert.equal(q.getSnapshot().dirty,false);q.dispose();
});
test('late acknowledgement preserves typing and coalesces edits before the next serial save', async () => {
  const api=transport(),wait=deferred(),save=api.novel.bind(api);api.novel=async b=>{await wait.promise;return save(b);};
  const q=new WritingSession(api);await q.load();q.edit('novel','old');const flush=q.flush();
  q.edit('novel','middle');q.edit('novel','new');wait.resolve();await flush;
  assert.equal(q.getSnapshot().novel,'new');assert.deepEqual(api.writes.map(w=>w[1]),[{content:'old',content_version:'1'},{content:'new',content_version:'2'}]);q.dispose();
});
test('409 pauses queue and retries cannot silently overwrite another writer', async () => {
  const api=transport();api.novel=async()=>{throw {status:409};};const q=new WritingSession(api);await q.load();q.edit('novel','mine');
  assert.equal(await q.flush(),false);assert.equal(q.getSnapshot().status,'conflict');assert.equal(q.getSnapshot().novel,'mine');
  assert.equal(await q.retry(),false);q.dispose();
});

test('explicit server reload replaces a conflicting dirty draft only after the latest read succeeds', async () => {
  const api = transport(); let stored = null;
  const persistence = { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } };
  const session = new WritingSession(api, persistence); await session.load();
  api.novel = async () => { throw { status: 409 }; };
  session.edit('novel', '本机冲突稿'); await session.flush();
  assert.equal(await session.load(true), true); assert.equal(session.getSnapshot().novel, '本机冲突稿');
  api.server = { ...api.server, content_version: '2', novel: record('11', '当前服务端正文') };
  const wait = deferred(); api.get = async () => { await wait.promise; return structuredClone(api.server); };
  const original = structuredClone(stored);
  const reloading = session.reloadFromServer();
  assert.equal(session.getSnapshot().busy, true);
  session.edit('novel', '读取期间不接受输入');
  assert.equal(session.getSnapshot().novel, '本机冲突稿'); assert.deepEqual(stored, original);
  wait.resolve(); assert.equal(await reloading, true);
  assert.equal(session.getSnapshot().novel, '当前服务端正文'); assert.equal(session.getSnapshot().contentVersion, '2');
  assert.equal(session.getSnapshot().dirty, false); assert.equal(session.getSnapshot().status, 'saved');
  assert.equal(stored, null); session.dispose();
});

test('failed explicit server reload retains dirty input and its original local record', async () => {
  const api = transport(); let stored = null;
  const session = new WritingSession(api, { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } });
  await session.load(); session.edit('novel', '读取失败不能清除的本机稿');
  const original = structuredClone(stored); api.get = async () => { throw new Error('offline'); };
  assert.equal(await session.reloadFromServer(), false);
  assert.equal(session.getSnapshot().novel, '读取失败不能清除的本机稿');
  assert.equal(session.getSnapshot().dirty, true); assert.equal(session.getSnapshot().busy, false);
  assert.deepEqual(stored, original); session.dispose();
});

test('failed or stale explicit reload preserves a changed-base recovery record', async () => {
  const api = transport(); api.server = { ...api.server, content_version: '2', novel: record('11', '服务端修改') };
  const original = { base_version: '1', novel: '原恢复记录', script: '', script_id: null };
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
  const api=transport(),save=api.novel.bind(api);let first=true;api.novel=async b=>{const r=await save(b);if(first){first=false;throw {status:503};}return r;};
  const q=new WritingSession(api);await q.load();q.edit('novel','saved');assert.equal(await q.flush(),true);assert.equal(api.writes.length,1);assert.equal(q.getSnapshot().dirty,false);q.dispose();
});
test('uncertain response with unrelated server change becomes conflict without overwriting draft', async () => {
  const api=transport();api.novel=async()=>{api.server={...api.server,content_version:'8',novel:record('11','other')};throw {};};
  const q=new WritingSession(api);await q.load();q.edit('novel','mine');assert.equal(await q.flush(),false);assert.equal(q.getSnapshot().status,'conflict');assert.equal(q.getSnapshot().novel,'mine');q.dispose();
});
test('confirmation flushes content and confirms same script ID with latest version', async () => {
  const api=transport(),q=new WritingSession(api);await q.load();q.edit('script','my script');assert.equal(await q.confirm(),true);
  assert.deepEqual(api.writes[1],['confirm','12',{content_version:'2'}]);assert.equal(q.getSnapshot().confirmed,true);q.dispose();
});
test('navigation barrier waits for writes; dispose prevents coalesced writes after unmount', async () => {
  const api=transport(),wait=deferred(),save=api.novel.bind(api);api.novel=async b=>{await wait.promise;return save(b);};
  const q=new WritingSession(api);await q.load();q.edit('novel','one');const barrier=q.flush();q.edit('script','two');q.dispose();wait.resolve();assert.equal(await barrier,false);assert.equal(api.writes.length,1);
});
test('failed initial GET never enables editing and can be explicitly reloaded', async () => {
  const api=transport(),get=api.get;api.get=async()=>{throw {};};const q=new WritingSession(api);await q.load();q.edit('novel','ignored');assert.equal(q.getSnapshot().loaded,false);assert.equal(q.getSnapshot().novel,'');api.get=get;await q.load();assert.equal(q.getSnapshot().loaded,true);q.dispose();
});
test('navigation flush waits for pending confirmation and saves text typed during confirmation', async () => {
  const api=transport(),wait=deferred(),confirm=api.confirm.bind(api);api.confirm=async(...args)=>{await wait.promise;return confirm(...args);};
  const q=new WritingSession(api);await q.load();q.edit('script','first');const confirmation=q.confirm();
  await new Promise(resolve=>setTimeout(resolve,0));q.edit('script','later');let completed=false;const barrier=q.flush().then(ok=>{completed=true;return ok;});
  await new Promise(resolve=>setTimeout(resolve,0));assert.equal(completed,false);wait.resolve();await confirmation;assert.equal(await barrier,true);
  assert.equal(q.getSnapshot().script,'later');assert.equal(api.server.editing_script.content,'later');assert.equal(q.getSnapshot().confirmed,false);q.dispose();
});
test('network failure before commit pauses until explicit retry rechecks server version', async () => {
  const api=transport(),save=api.novel.bind(api);api.novel=async()=>{throw {};};const q=new WritingSession(api);await q.load();q.edit('novel','keep');assert.equal(await q.flush(),false);
  assert.equal(q.getSnapshot().dirty,true);assert.equal(q.getSnapshot().status,'error');api.novel=save;assert.equal(await q.retry(),true);assert.equal(api.server.novel.content,'keep');q.dispose();
});
test('novel editing leaves the current confirmed script unchanged', async () => {
  const api=transport(),q=new WritingSession(api);await q.load();q.edit('script','script');await q.confirm();q.edit('novel','new novel');await q.flush();assert.equal(q.getSnapshot().confirmed,true);assert.equal(api.server.editing_script.content,'script');q.dispose();
});
test('selection saves existing draft first and sends selected ID with newest version', async () => {
  const api=transport();api.select=async(id,body)=>{api.writes.push(['select',id,body]);api.server={...api.server,content_version:'3',editing_script:record(id,'selected','unconfirmed')};return api.server;};
  const q=new WritingSession(api);await q.load();q.edit('script','current');assert.equal(await q.select('22'),true);
  assert.deepEqual(api.writes[1],['select','22',{content_version:'2'}]);assert.equal(q.getSnapshot().scriptId,'22');assert.equal(q.getSnapshot().script,'selected');q.dispose();
});
test('typing during pending selection is preserved and requires explicit reconciliation', async () => {
  const api=transport(),wait=deferred();api.select=async id=>{await wait.promise;api.server={...api.server,content_version:'3',editing_script:record(id,'selected','unconfirmed')};return api.server;};
  const q=new WritingSession(api);await q.load();q.edit('script','original');const selection=q.select('22');await new Promise(resolve=>setTimeout(resolve,0));
  q.edit('script','new typing');wait.resolve();assert.equal(await selection,false);assert.equal(q.getSnapshot().script,'new typing');assert.equal(q.getSnapshot().status,'conflict');assert.equal(api.server.editing_script.content,'selected');q.dispose();
});
test('uncertain committed save remains paused after draft returns to stale snapshot and retry saves intended text', async () => {
  const api=transport();api.server={...api.server,novel:record('11','A')};const get=api.get.bind(api),save=api.novel.bind(api);
  const q=new WritingSession(api);await q.load();api.novel=async body=>{await save(body);throw {};};api.get=async()=>{throw {};};
  q.edit('novel','B');assert.equal(await q.flush(),false);q.edit('novel','A');assert.equal(q.getSnapshot().dirty,true);assert.equal(q.getSnapshot().status,'error');assert.equal(await q.flush(),false);assert.equal(api.server.novel.content,'B');
  api.get=get;api.novel=save;assert.equal(await q.retry(),true);assert.equal(api.server.novel.content,'A');assert.equal(q.getSnapshot().status,'saved');q.dispose();
});

const boldDocument = content => ({ ops: [{ insert: content, attributes: { bold: true } }, { insert: '\n' }] });
const italicDocument = content => ({ ops: [{ insert: content, attributes: { italic: true } }, { insert: '\n' }] });

test('format-only editing is dirty, unconfirms, and saves plain text plus Delta in one version', async () => {
  const api = transport(), session = new WritingSession(api);
  await session.load(); session.edit('script', '第一场\n主角：你好'); await session.confirm();
  const document = boldDocument(session.getSnapshot().script);
  session.edit('script', session.getSnapshot().script, document);
  assert.equal(session.getSnapshot().dirty, true); assert.equal(session.getSnapshot().confirmed, false);
  await session.flush();
  assert.deepEqual(api.writes.at(-1)[1], { content: '第一场\n主角：你好', content_version: '3', script_id: '12', editor_document: document });
  const refreshed = new WritingSession(api); await refreshed.load();
  assert.deepEqual(refreshed.getSnapshot().editorDocument, document); assert.equal(refreshed.getSnapshot().dirty, false);
  session.dispose(); refreshed.dispose();
});

test('late format save acknowledgement keeps a newer format in the same serial queue', async () => {
  const api = transport(), session = new WritingSession(api), waiting = deferred();
  await session.load(); session.edit('script', '正文'); await session.flush();
  const save = api.script.bind(api); let first = true;
  api.script = async body => { if (first) { first = false; await waiting.promise; } return save(body); };
  session.edit('script', '正文', boldDocument('正文')); const saving = session.flush();
  session.edit('script', '正文', italicDocument('正文')); waiting.resolve(); await saving;
  assert.deepEqual(api.writes.slice(-2).map(write => write[1].content_version), ['2', '3']);
  assert.deepEqual(session.getSnapshot().editorDocument, italicDocument('正文'));
  assert.deepEqual(api.server.editing_script.editor_document, italicDocument('正文')); session.dispose();
});

test('format conflict retains local Delta and does not send a second save', async () => {
  const api = transport(), session = new WritingSession(api);
  await session.load(); session.edit('script', '正文'); await session.flush();
  api.script = async () => { throw { status: 409 }; };
  session.edit('script', '正文', boldDocument('正文'));
  assert.equal(await session.flush(), false); assert.equal(session.getSnapshot().status, 'conflict');
  assert.deepEqual(session.getSnapshot().editorDocument, boldDocument('正文'));
  assert.equal(await session.retry(), false); session.dispose();
});

test('same-base format recovery restores Delta and continues saving without confirmation', async () => {
  const api = transport(); api.server.editing_script = record('12', '正文', 'unconfirmed');
  let saved = null;
  const recovery = { read: () => saved, save: draft => { saved = structuredClone(draft); }, clear: () => { saved = null; } };
  const first = new WritingSession(api, recovery); await first.load(); first.edit('script', '正文', boldDocument('正文')); first.dispose();
  const next = new WritingSession(api, recovery); await next.load();
  assert.equal(next.getSnapshot().recoverable, false); assert.equal(api.writes.length, 0);
  assert.deepEqual(next.getSnapshot().editorDocument, boldDocument('正文'));
  await next.flush(); assert.equal(saved, null); assert.deepEqual(api.server.editing_script.editor_document, boldDocument('正文')); next.dispose();
});

test('lost format response checks Delta as well as content before acknowledging', async () => {
  const api = transport(), session = new WritingSession(api);
  await session.load(); session.edit('script', '正文'); await session.flush();
  api.script = async body => {
    api.server = { ...api.server, content_version: '3', editing_script: { ...api.server.editing_script, editor_document: italicDocument(body.content) } };
    throw {};
  };
  session.edit('script', '正文', boldDocument('正文'));
  assert.equal(await session.flush(), false); assert.equal(session.getSnapshot().status, 'conflict');
  assert.deepEqual(session.getSnapshot().editorDocument, boldDocument('正文')); session.dispose();
});

test('format editing during candidate adoption keeps the draft for explicit reconciliation', async () => {
  const api = transport(), session = new WritingSession(api), waiting = deferred();
  await session.load(); session.edit('script', '正文'); await session.flush();
  api.select = async id => { await waiting.promise; return { ...api.server, content_version: '3', editing_script: record(id, '候选', 'unconfirmed') }; };
  const selection = session.select('22'); await new Promise(resolve => setTimeout(resolve, 0));
  session.edit('script', '正文', boldDocument('正文')); waiting.resolve();
  assert.equal(await selection, false); assert.equal(session.getSnapshot().status, 'conflict');
  assert.deepEqual(session.getSnapshot().editorDocument, boldDocument('正文')); session.dispose();
});

test('legacy text editing clears saved formats and mismatched Delta never replaces the draft', async () => {
  const api = transport(); api.server.editing_script = { ...record('12', '正文', 'unconfirmed'), editor_document: boldDocument('正文') };
  const session = new WritingSession(api); await session.load();
  session.edit('script', '新正文', boldDocument('别的内容'));
  assert.equal(session.getSnapshot().script, '正文'); assert.equal(session.getSnapshot().status, 'error');
  session.edit('script', '新正文'); await session.retry();
  assert.equal(api.server.editing_script.editor_document, null); assert.equal(api.server.editing_script.content, '新正文'); session.dispose();
});

test('lost writing receipt across reload is reconciled before saving newer retained input', async () => {
  const api = transport(), gate = deferred(); let stored = null;
  const persistence = { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } };
  const save = api.novel.bind(api);
  api.novel = async body => { const result = await save(body); await gate.promise; return result; };
  const first = new WritingSession(api, persistence); await first.load(); first.edit('novel', 'first');
  const writing = first.flush(); first.edit('novel', 'newer'); first.dispose(); gate.resolve(); await writing;
  api.novel = save;
  const next = new WritingSession(api, persistence); await next.load();
  assert.equal(next.getSnapshot().recoverable, false); assert.equal(next.getSnapshot().novel, 'newer');
  assert.equal(await next.flush(), true); assert.equal(api.server.novel.content, 'newer');
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
  const api = transport(); api.server.novel = record('11', 'base');
  const before = structuredClone(api.server);
  let stored = { base_version: '1', novel: 'base', script: '', script_id: null,
    pending: { before, field: 'novel', content: 'older in-flight input', editorDocument: null } };
  const session = new WritingSession(api, { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } });
  await session.load();
  assert.equal(session.getSnapshot().dirty, true); assert.notEqual(stored, null);
  assert.equal(await session.flush(), true);
  assert.equal(api.writes[0][1].content, 'base'); assert.equal(api.writes[0][1].content_version, '1');
  assert.equal(api.server.novel.content, 'base'); session.dispose();
});

for (const field of ['novel', 'script']) for (const olderWins of [false, true]) {
  test(`explicit ${field} replacement fences an unknown older request when ${olderWins ? 'older' : 'replacement'} commits first`, async () => {
    const api = transport();
    api.server = { ...api.server, novel: record('11', 'base'), editing_script: record('12', 'base', 'unconfirmed') };
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
        assert.equal(api.server[field === 'novel' ? 'novel' : 'editing_script'].content, 'base');
        assert.equal(stored, null);
      }
    } finally { session.dispose(); }
  });
}

test('explicit server replacement retains pending information embedded in a differing-base recovery record', async () => {
  const api = transport(); api.server.novel = record('11', 'base');
  const before = structuredClone(api.server);
  let stored = { base_version: '0', novel: 'recovered input', script: '', script_id: null,
    pending: { before, field: 'novel', content: 'older request', editorDocument: null } };
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
  const api = transport(); api.server.novel = record('11', 'base');
  const before = structuredClone(api.server), get = api.get.bind(api), save = api.novel.bind(api);
  const older = { content: 'older request', content_version: '1' };
  let stored = { base_version: '1', novel: 'retained input', script: '', script_id: null,
    pending: { before, field: 'novel', content: older.content, editorDocument: null } };
  api.get = async () => { throw new Error('offline'); };
  api.novel = async body => {
    if (body.content_version !== api.server.content_version) throw { status: 409 };
    return save(body);
  };
  const session = new WritingSession(api, { read: () => stored, save: value => { stored = structuredClone(value); }, clear: () => { stored = null; } });
  try {
    assert.equal(await session.load(), false); assert.equal(session.getSnapshot().loaded, false);
    api.get = get;
    assert.equal(await session.reloadFromServer(), true);
    assert.equal(session.getSnapshot().novel, 'base'); assert.equal(session.getSnapshot().dirty, true);
    assert.equal(stored.pending.content, 'older request');
    assert.equal(await session.flush(), true);
    assert.deepEqual(api.writes[0], ['novel', { content: 'base', content_version: '1' }]);
    await assert.rejects(api.novel(older), error => error.status === 409);
    assert.equal(api.server.novel.content, 'base'); assert.equal(stored, null);
  } finally { session.dispose(); }
});

test('explicit server replacement clears an unknown request after observing its advanced version', async () => {
  const api = transport(); api.server.novel = record('11', 'base');
  const save = api.novel.bind(api); let older;
  api.novel = async body => { older = body; throw { status: 503 }; };
  const session = new WritingSession(api);
  try {
    await session.load(); session.edit('novel', 'accepted older input'); await session.flush();
    await save(older); assert.equal(await session.reloadFromServer(), true);
    assert.equal(session.getSnapshot().novel, 'accepted older input'); assert.equal(session.getSnapshot().dirty, false);
    assert.equal(session.getSnapshot().status, 'saved'); assert.equal(api.writes.length, 1);
  } finally { session.dispose(); }
});
