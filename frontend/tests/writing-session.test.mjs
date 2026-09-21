import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/projects/writing-session.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { WritingSession } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const initial = () => ({ episode_id: '9', content_version: '1', novel: null, editing_script: null, confirmed_script_id: null });
const record = (id, content, state) => ({ id, content, updated_at: '2026-09-21T00:00:00Z', ...(state ? { state } : {}) });
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a; reject=b;}); return {promise,resolve,reject}; };
function transport() {
  let server = initial(); const writes = [];
  return { writes, get server() { return server; }, set server(v) {server=v;},
    async get() { return structuredClone(server); },
    async novel(body) { writes.push(['novel',body]); server = {...server, content_version: String(BigInt(server.content_version)+1n), novel: record('11',body.content)}; return {content_version:server.content_version,novel:server.novel}; },
    async script(body) { writes.push(['script',body]); server={...server,content_version:String(BigInt(server.content_version)+1n),editing_script:record('12',body.content,'unconfirmed'),confirmed_script_id:null}; return {content_version:server.content_version,script:server.editing_script}; },
    async confirm(id,body) { writes.push(['confirm',id,body]); server={...server,content_version:String(BigInt(server.content_version)+1n),editing_script:{...server.editing_script,state:'confirmed'},confirmed_script_id:id};return structuredClone(server); },
    async select() { throw new Error('unused'); },
  };
}
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
  q.edit('novel','B');assert.equal(await q.flush(),false);q.edit('novel','A');assert.equal(q.getSnapshot().dirty,false);assert.equal(q.getSnapshot().status,'error');assert.equal(await q.flush(),false);assert.equal(api.server.novel.content,'B');
  api.get=get;api.novel=save;assert.equal(await q.retry(),true);assert.equal(api.server.novel.content,'A');assert.equal(q.getSnapshot().status,'saved');q.dispose();
});
