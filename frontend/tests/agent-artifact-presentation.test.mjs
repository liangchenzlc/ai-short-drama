import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const compiled = ts.transpileModule(readFileSync(new URL('../src/features/agents/agent-artifact-presentation.ts', import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { artifactVersion, diffValue, artifactEffect } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
test('adoption version conversion refuses unsafe or missing optimistic tokens', () => {
  assert.equal(artifactVersion('42'), 42);
  for (const version of ['0', '-1', '9007199254740993', undefined, NaN]) assert.throws(() => artifactVersion(version));
});
test('diffs use product units and never serialize private nested objects', () => {
  assert.equal(diffValue('duration_ms', 5000), '5 秒');
  assert.equal(diffValue('description', { tool_call_id: 'private' }), '空');
  assert.match(artifactEffect('script_candidate'), /单独确认定稿/);
});
