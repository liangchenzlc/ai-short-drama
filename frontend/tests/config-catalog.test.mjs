import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/ai-config/config-catalog.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { ConfigCatalog } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};
const item = id => ({ id, serviceType: 'text', enabled: true, isDefault: true });

test('selectors share all catalog pages and releasing one leaves the shared request alive', async () => {
  const first = deferred();
  const reads = [];
  const catalog = new ConfigCatalog(async (kind, offset, limit, signal) => {
    reads.push({ kind, offset, limit, signal });
    return offset === 0 ? first.promise : { items: [item('2')], total: 2 };
  }, String);
  const releaseA = catalog.subscribe('text', () => {});
  const releaseB = catalog.subscribe('text', () => {});
  assert.equal(reads.length, 1);
  releaseA(); await tick(); assert.equal(reads[0].signal.aborted, false);
  first.resolve({ items: [item('1')], total: 2 }); await tick();
  assert.deepEqual(reads.map(read => read.offset), [0, 1]);
  assert.deepEqual(catalog.getSnapshot('text').items.map(value => value.id), ['1', '2']);
  releaseB(); await tick();
  const releaseC = catalog.subscribe('text', () => {});
  assert.equal(reads.length, 2);
  releaseC();
});

test('last unsubscribe cancels I/O; immediate StrictMode resubscribe preserves one request', async () => {
  const requests = [];
  const catalog = new ConfigCatalog(async (_kind, _offset, _limit, signal) => {
    requests.push(signal); return deferred().promise;
  }, String);
  const first = catalog.subscribe('text', () => {});
  first();
  const second = catalog.subscribe('text', () => {});
  await tick(); assert.equal(requests.length, 1); assert.equal(requests[0].aborted, false);
  second(); await tick(); assert.equal(requests[0].aborted, true);
  const third = catalog.subscribe('text', () => {});
  assert.equal(requests.length, 2);
  third();
});

test('errors are shared, explicit retry recovers, and obsolete responses cannot replace new data', async () => {
  const attempts = [deferred(), deferred(), deferred()];
  let read = 0;
  const catalog = new ConfigCatalog(() => attempts[read++].promise, error => error.message);
  const release = catalog.subscribe('text', () => {});
  attempts[0].reject(new Error('temporary')); await tick();
  assert.equal(catalog.getSnapshot('text').error, 'temporary');
  catalog.refresh('text');
  catalog.refresh('text');
  attempts[2].resolve({ items: [item('new')], total: 1 }); await tick();
  attempts[1].resolve({ items: [item('old')], total: 1 }); await tick();
  assert.equal(catalog.getSnapshot('text').error, '');
  assert.deepEqual(catalog.getSnapshot('text').items.map(value => value.id), ['new']);
  release();
});

test('account catalogs isolate results and invalidation only fetches subscribed kinds', async () => {
  const reads = [];
  const loader = async (kind, _offset, _limit, signal) => {
    reads.push({ kind, signal }); return { items: [item(String(reads.length))], total: 1 };
  };
  const firstAccount = new ConfigCatalog(loader, String);
  const secondAccount = new ConfigCatalog(loader, String);
  const release = firstAccount.subscribe('text', () => {});
  await tick();
  assert.deepEqual(secondAccount.getSnapshot('text').items, []);
  firstAccount.getSnapshot('image');
  firstAccount.invalidate(); await tick();
  assert.deepEqual(reads.map(read => read.kind), ['text', 'text']);
  const secondRelease = secondAccount.subscribe('text', () => {}); await tick();
  assert.equal(reads.length, 3);
  assert.notDeepEqual(firstAccount.getSnapshot('text').items, secondAccount.getSnapshot('text').items);
  release(); secondRelease();
});
