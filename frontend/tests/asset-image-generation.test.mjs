import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/assets/asset-image-generation.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { assetImageGenerationBlockReason, buildAssetImageRequest, mergeAssetImageTasks, missingActiveTaskIds, shouldRefreshAssetCandidates } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`
);

test('builds an asset source from the saved server version without implicit image context', () => {
  const body = buildAssetImageRequest(
    { id: '101', row_version: '8' },
    { configId: '201', supplement: '', count: 1 },
  );

  assert.deepEqual(body.source, { scene: 'asset_image', asset_id: '101', row_version: '8' });
  assert.deepEqual(body.input, { prompt: '', reference_media_ids: [] });
  assert.deepEqual(body.parameters, { count: 1 });
  assert.equal(body.config_id, '201');
});

test('includes only image options the user selected', () => {
  const body = buildAssetImageRequest(
    { id: '22', row_version: '4' },
    { supplement: 'film still', count: 3, aspect: '16:9', resolution: '1920x1080' },
  );

  assert.deepEqual(body.parameters, { count: 3, aspect: '16:9', resolution: '1920x1080' });
  assert.equal(body.config_id, undefined);
  assert.equal('scope' in body.source, false);
});

const task = (generation_id, status, created_at, assets = []) => ({
  generation_id,
  service_type: 'image',
  status,
  created_at,
  result: { assets },
  can_cancel: status === 'queued' || status === 'running',
  can_retry: status === 'failed',
  can_resume: false,
});

test('newer task state replaces queued state and duplicate IDs occur once', () => {
  const merged = mergeAssetImageTasks(
    [task('9', 'queued', '2026-09-23T08:00:00Z')],
    [task('9', 'succeeded', '2026-09-23T08:00:00Z', [{ media_id: '88' }])],
  );

  assert.equal(merged.length, 1);
  assert.equal(merged[0].status, 'succeeded');
  assert.equal(merged[0].result.assets[0].media_id, '88');
});

test('cross-page merge keeps active old tasks and sorts newest first', () => {
  const merged = mergeAssetImageTasks(
    [task('2', 'running', '2026-09-20T08:00:00Z')],
    [task('11', 'succeeded', '2026-09-23T08:00:00Z'), task('10', 'failed', '2026-09-22T08:00:00Z', [{ media_id: '7' }])],
  );

  assert.deepEqual(merged.map((item) => item.generation_id), ['11', '10', '2']);
  assert.equal(merged[1].result.assets.length, 1);
});

test('candidate refresh is requested once while active or when an active task becomes terminal', () => {
  const running = task('5', 'running', '2026-09-23T08:00:00Z');
  const succeeded = task('5', 'succeeded', '2026-09-23T08:00:00Z');
  const oldFailure = task('4', 'failed', '2026-09-22T08:00:00Z');

  assert.equal(shouldRefreshAssetCandidates([], [running]), true);
  assert.equal(shouldRefreshAssetCandidates([running], [succeeded]), true);
  assert.equal(shouldRefreshAssetCandidates([oldFailure], [oldFailure]), false);
  assert.equal(shouldRefreshAssetCandidates([], [oldFailure]), true);
});
test('an active task missing from filtered pages is selected for direct reconciliation', () => {
  const oldRunning = task('2', 'running', '2026-09-20T08:00:00Z');
  const latest = task('11', 'succeeded', '2026-09-23T08:00:00Z');

  assert.deepEqual(missingActiveTaskIds([latest, oldRunning], [latest]), ['2']);
  assert.deepEqual(missingActiveTaskIds([latest, oldRunning], [latest, oldRunning]), []);
});
test('generation gate explains missing usable model and empty saved visual content', () => {
  const complete = { name: 'Hero', description: 'silver coat', prompt: '' };

  assert.equal(assetImageGenerationBlockReason(complete, undefined), '请选择可用的生图模型。');
  assert.equal(assetImageGenerationBlockReason({ name: 'Hero', description: '', prompt: '' }, '9'), '请先填写素材描述或提示词。');
  assert.equal(assetImageGenerationBlockReason({ name: ' ', description: 'coat', prompt: '' }, '9'), '请先填写素材名称。');
  assert.equal(assetImageGenerationBlockReason(complete, '9'), null);
});