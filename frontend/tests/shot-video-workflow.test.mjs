import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/projects/shot-video-workflow.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { videoBlockReason, shotVideoRequest, shotVideoApplyRequest } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const shot = { id: '123', row_version: '3', video_context_hash: 'a'.repeat(64), script: 'Raise the umbrella.', duration_ms: 5000,
  image: { media_id: '456', layout: 'single', is_stale: false }, video: null, video_settings: { resolution: '720p' } };
const caps = { known: true, video_input: { reference_images: true, duration_seconds: [5, 10], resolutions: ['720p', '1080p'] } };

test('video admission accepts single and grid references and requires omni reference support', () => {
  assert.equal(videoBlockReason(shot, '9', caps, false), '');
  for (const layout of ['single', 'four', 'five', 'nine']) assert.equal(videoBlockReason({ ...shot, image: { ...shot.image, layout } }, '9', caps, false), '');
  assert.notEqual(videoBlockReason(shot, '9', { known: true, video_input: { first_frame: true } }, false), '');
  for (const changed of [{ ...shot, image: null },
    { ...shot, image: { ...shot.image, is_stale: true } }, { ...shot, duration_ms: 5500 },
    { ...shot, duration_ms: 3000 }, { ...shot, video_settings: { resolution: '480p' } }]) {
    assert.notEqual(videoBlockReason(changed, '9', caps, false), '');
  }
  assert.notEqual(videoBlockReason(shot, '9', { known: true }, false), '');
});

test('video request submits saved context and reference without copying client prompt', () => {
  const body = shotVideoRequest({ ...shot, video_prompt: 'client text' }, '9');
  assert.equal(body.input, undefined);
  assert.equal(body.source.reference_media_id, '456');
  assert.equal(body.source.first_frame_media_id, undefined);
  assert.equal(body.source.context_hash, shot.video_context_hash);
  assert.deepEqual(body.parameters, { resolution: '720p', duration_ms: 5000 });
});

test('video adoption includes independent current video and optimistic context', () => {
  const body = shotVideoApplyRequest({ ...shot, video: { media_id: '789' } });
  assert.equal(body.expected_media_id, '789');
  assert.equal(body.expected_row_version, '3');
  assert.equal(body.expected_context_hash, shot.video_context_hash);
  assert.equal(body.acknowledge_stale_source, undefined);
});

test('video duration is independent of storyboard timing and respects model limits', () => {
  const changed = { ...shot, duration_ms: 3000, video_settings: { resolution: '720p', duration_ms: 15000 } };
  const model = { known: true, video_input: { reference_images: true, duration_seconds: [5, 10, 15], resolutions: ['720p'] } };
  assert.equal(videoBlockReason(changed, '9', model, false), '');
  assert.equal(shotVideoRequest(changed, '9').parameters.duration_ms, 15000);
  assert.notEqual(videoBlockReason({ ...changed, video_settings: { ...changed.video_settings, duration_ms: 16000 } }, '9', model, false), '');
});
