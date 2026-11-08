import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../../src/features/creation/storyboard/videos/shot-video-workflow.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { videoBlockReason, shotVideoRequest } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const shot = { id: '123', row_version: '3', video_context_hash: 'a'.repeat(64), script: 'Raise the umbrella.', duration_ms: 5000,
  image: { media_id: '456', layout: 'single', is_stale: false }, video: null, video_settings: { resolution: '720p' } };
const caps = { known: true, video_input: { reference_images: true, duration_seconds: [5, 10], resolutions: ['720p', '1080p'] } };

test('video admission accepts text alone and checks explicit reference capability and limits', () => {
  assert.equal(videoBlockReason({ ...shot, image: null }, '9', caps, false, 0), '');
  assert.equal(videoBlockReason(shot, '9', caps, false, 1), '');
  assert.equal(videoBlockReason(shot, '9', { known: true, video_input: { first_frame: true } }, false, 0), '');
  assert.notEqual(videoBlockReason(shot, '9', { known: true, video_input: { first_frame: true } }, false, 1), '');
  assert.notEqual(videoBlockReason(shot, '9', caps, false, 10), '');
  for (const changed of [{ ...shot, duration_ms: 5500 }, { ...shot, duration_ms: 3000 },
    { ...shot, video_settings: { resolution: '480p' } }])
    assert.notEqual(videoBlockReason(changed, '9', caps, false, 0), '');
});

test('video request submits saved context without implicitly selecting the current image', () => {
  const body = shotVideoRequest({ ...shot, video_prompt: 'client text' }, '9');
  assert.equal(body.input, undefined);
  assert.equal(body.source.reference_media_id, undefined);
  assert.equal(body.source.first_frame_media_id, undefined);
  assert.equal(body.source.context_hash, shot.video_context_hash);
  assert.deepEqual(body.parameters, { resolution: '720p', duration_ms: 5000 });
});

test('video duration is independent of storyboard timing and respects model limits', () => {
  const changed = { ...shot, duration_ms: 3000, video_settings: { resolution: '720p', duration_ms: 15000 } };
  const model = { known: true, video_input: { reference_images: true, duration_seconds: [5, 10, 15], resolutions: ['720p'] } };
  assert.equal(videoBlockReason(changed, '9', model, false), '');
  assert.equal(shotVideoRequest(changed, '9').parameters.duration_ms, 15000);
  assert.notEqual(videoBlockReason({ ...changed, video_settings: { ...changed.video_settings, duration_ms: 16000 } }, '9', model, false), '');
});
