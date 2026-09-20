import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/ai-config/config-selection.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { resolveConfigSelection, savedConfigId } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const config = (id, serviceType, isDefault = true, enabled = true) => ({ id, serviceType, isDefault, enabled });

test('new selections follow the enabled default for their own model type', () => {
  const configs = [config('11', 'text'), config('22', 'image'), config('33', 'video')];
  assert.equal(resolveConfigSelection(configs, 'text', undefined, true), '11');
  assert.equal(resolveConfigSelection(configs, 'image', '', true), '22');
  assert.equal(resolveConfigSelection(configs, 'video', undefined, true), '33');
});

test('changing the default updates automatic selections without replacing manual choices', () => {
  const before = [config('11', 'text'), config('12', 'text', false)];
  const after = [config('11', 'text', false), config('12', 'text')];
  assert.equal(resolveConfigSelection(before, 'text', undefined, true), '11');
  assert.equal(resolveConfigSelection(after, 'text', undefined, true), '12');
  assert.equal(resolveConfigSelection(after, 'text', '11', true), '11');
  assert.equal(resolveConfigSelection(after, 'text', undefined, true), '12');
});

test('disabled, missing, or non-default models are not silently selected', () => {
  const configs = [config('11', 'text', true, false), config('12', 'text', false), config('22', 'image')];
  assert.equal(resolveConfigSelection(configs, 'text', undefined, true), undefined);
  assert.equal(resolveConfigSelection([], 'text', undefined, true), undefined);
  assert.equal(resolveConfigSelection([], 'text', '11', true), '11');
});

test('filters stay unfiltered and retries can preserve the original model', () => {
  const configs = [config('11', 'text')];
  assert.equal(resolveConfigSelection(configs, 'text', undefined, false), undefined);
  assert.equal(resolveConfigSelection(configs, 'text', '12', false), '12');
});

test('old demo labels fall back to defaults, while Snowflake IDs retain full precision', () => {
  for (const value of ['', 'demo-script-A', '演示剧本模型', '0', '-1', '1e3', '18446744073709551616'])
    assert.equal(savedConfigId(value), undefined);
  assert.equal(savedConfigId('359995498431516672'), '359995498431516672');
  assert.equal(savedConfigId('18446744073709551615'), '18446744073709551615');
});
