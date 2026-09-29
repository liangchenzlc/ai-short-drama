import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/projects/assembly-editing.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { moveClip, nextPlayable, clipDuration, timecode, buildTimeline, splitClip, trimClip, addClip, locateFrame } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
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

test('splitting preserves frame coverage and immutable source lineage', () => {
  const original = [{ ...clips[0], trim_in_ms: 2000, trim_out_ms: 8000, duration_ms: 10000 }];
  const split = splitClip(original, '1', 90, 'split-a');
  assert.deepEqual(split.map(c => [c.trim_in_ms, c.trim_out_ms]), [[2000, 5000], [5000, 8000]]);
  assert.equal(split[1].source_clip_id, '1');
  assert.equal(buildTimeline(split).at(-1).end, 180);
  assert.equal(locateFrame(buildTimeline(split), 90).clip.id, 'split-a');
  const again = splitClip(split, 'split-a', 91, 'split-b');
  assert.equal(again[2].source_clip_id, '1');
  assert.equal(buildTimeline(again).at(-1).end, 180);
  assert.equal(splitClip(original, '1', 0), original);
  assert.equal(original[0].trim_out_ms, 8000);
});

test('trim is bounded and removing a segment closes the timeline gap', () => {
  const split = splitClip([clips[0]], '1', 30, 'second');
  const trimmed = trimClip(split, 'second', 1800, 2300);
  assert.equal(buildTimeline(trimmed).at(-1).end, 45);
  assert.equal(buildTimeline(trimmed.slice(1))[0].start, 0);
  assert.equal(trimClip(split, 'second', 2500, 2500), split);
  const added = addClip(trimmed, clips[0], 0, 'added');
  assert.equal(added[0].source_clip_id, '1');
  assert.equal(added[0].trim_in_ms, 0);
});
test('rough playback skips excluded or invalid sources and ends cleanly', () => {
  assert.equal(nextPlayable(clips).id, '1');
  assert.equal(nextPlayable(clips, '1').id, '4');
  assert.equal(nextPlayable(clips, '4'), undefined);
  assert.equal(clipDuration(clips[0]), 2000);
  assert.equal(clipDuration({ trim_in_ms: 500, trim_out_ms: null, duration_ms: 4000 }), 3500);
  assert.equal(timecode(61500), '01:01.5');
});
