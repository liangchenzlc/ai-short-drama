import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import test from 'node:test';
import ts from 'typescript';

globalThis.crypto ??= webcrypto;
const source = readFileSync(new URL('../src/features/generations/attempt.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { requestAttempt, clearAttempt, isServerId, setAttemptAccount } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const memory = () => {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key), values };
};
test('a failed or interrupted creation reuses its key without storing prompt content', async () => {
  const store = memory();
  const first = await requestAttempt('image', { input: { prompt: 'private story' } }, store);
  assert.equal(await requestAttempt('image', { input: { prompt: 'private story' } }, store), first);
  assert.ok(!JSON.stringify([...store.values.values()]).includes('private story'));
});
test('different payloads, different types and successful attempts use fresh keys', async () => {
  const store = memory();
  const first = await requestAttempt('text', { prompt: 'A' }, store);
  assert.notEqual(await requestAttempt('text', { prompt: 'B' }, store), first);
  assert.notEqual(await requestAttempt('video', { prompt: 'A' }, store), first);
  clearAttempt('text', store);
  assert.notEqual(await requestAttempt('text', { prompt: 'A' }, store), first);
});
test('only positive unsigned decimal server IDs are accepted without number conversion', () => {
  for (const id of ['1', '2071234567890123456', '18446744073709551615']) assert.equal(isServerId(id), true);
  for (const id of ['', '0', 'shot-1', '-1', '1.1', '1e3', '18446744073709551616']) assert.equal(isServerId(id), false);
});

test('in-memory receipts remain account-scoped when browser storage is unavailable', async () => {
  setAttemptAccount('21');
  const first = await requestAttempt('storage-disabled', { prompt: 'A' }, null);
  setAttemptAccount('22');
  assert.notEqual(await requestAttempt('storage-disabled', { prompt: 'A' }, null), first);
  setAttemptAccount('21');
  assert.equal(await requestAttempt('storage-disabled', { prompt: 'A' }, null), first);
  clearAttempt('storage-disabled', null);
  setAttemptAccount(null);
});

test('switching accounts never replays another account creation receipt', async () => {
  const store = memory();
  const previousWindow = globalThis.window;
  globalThis.window = { sessionStorage: store };
  try {
    setAttemptAccount('11');
    const first = await requestAttempt('shared-project', { prompt: 'A' }, store);
    setAttemptAccount('12');
    const second = await requestAttempt('shared-project', { prompt: 'A' }, store);
    assert.notEqual(first, second);
    clearAttempt('shared-project', store);
    setAttemptAccount('11');
    assert.equal(await requestAttempt('shared-project', { prompt: 'A' }, store), first);
  } finally { setAttemptAccount(null); if (previousWindow) globalThis.window = previousWindow; else delete globalThis.window; }
});
