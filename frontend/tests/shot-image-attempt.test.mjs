import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/projects/shot-image-attempt.ts', import.meta.url), 'utf8');
const output = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { pendingShotAttempt, startShotAttempt, finishShotAttempt } = await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
const memory = () => { const values = new Map(); return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }; };

test('late receipt or rejection after remount cannot clear a newer submission owner', () => {
  const storage = memory();
  startShotAttempt('11', 'owner-A', storage);
  assert.equal(finishShotAttempt('11', 'owner-A', storage), true);
  startShotAttempt('11', 'owner-B', storage);
  let idempotencyClears = 0;
  if (finishShotAttempt('11', 'owner-A', storage)) idempotencyClears++;
  assert.equal(idempotencyClears, 0);
  assert.equal(pendingShotAttempt('11', storage), 'owner-B');
  assert.equal(finishShotAttempt('11', 'owner-B', storage), true);
  assert.equal(pendingShotAttempt('11', storage), null);
});

test('ownership remains safe with browser storage unavailable', () => {
  const broken = { getItem() { throw new Error(); }, setItem() { throw new Error(); }, removeItem() { throw new Error(); } };
  startShotAttempt('12', 'owner-A', broken);
  startShotAttempt('12', 'owner-B', broken);
  assert.equal(finishShotAttempt('12', 'owner-A', broken), false);
  assert.equal(pendingShotAttempt('12', null), 'owner-B');
  assert.equal(finishShotAttempt('12', 'owner-B', broken), true);
});
