import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/projects/assembly-editing.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { moveClip, nextPlayable, clipDuration, timecode } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const clips = [
  { id: '1', position: 1, included: true, url: '/1.mp4', trim_in_ms: 500, trim_out_ms: 2500, duration_ms: 4000 },
  { id: '2', position: 2, included: false, url: '/2.mp4' },
  { id: '3', position: 3, included: true, issue: 'missing' },
  { id: '4', position: 4, included: true, url: '/4.mp4' },
];
test('composition ordering is independent and preserves every edit', () => {
  const moved = moveClip(clips, '1', 3);
  assert.deepEqual(moved.map(c => c.id), ['2', '3', '4', '1']);
  assert.deepEqual(moved.map(c => c.position), [1, 2, 3, 4]);
  assert.equal(moved[3].trim_in_ms, 500);
  assert.equal(clips[0].id, '1');
  assert.equal(moveClip(clips, 'missing', 1), clips);
});
test('rough playback skips excluded or invalid sources and ends cleanly', () => {
  assert.equal(nextPlayable(clips).id, '1');
  assert.equal(nextPlayable(clips, '1').id, '4');
  assert.equal(nextPlayable(clips, '4'), undefined);
  assert.equal(clipDuration(clips[0]), 2000);
  assert.equal(clipDuration({ trim_in_ms: 500, trim_out_ms: null, duration_ms: 4000 }), 3500);
  assert.equal(timecode(61500), '01:01.5');
});
