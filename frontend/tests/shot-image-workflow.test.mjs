import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/projects/shot-image-workflow.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { summarizeShotReferences, shotPreparationChanged, prepareShotOperation, imageGenerationBlockReason } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const shot = () => ({ id: '11', row_version: '9', context_hash: 'a'.repeat(64), script: 'scene', deleted_at: null, image: null, image_settings: { layout: 'single', aspect: 'inherit', resolution: '2K' } });

test('references only include confirmed images and deduplicate media', () => {
  assert.deepEqual(summarizeShotReferences(['1', '2', '3', '4'], [{ id: '1', state: 'confirmed', media_id: '90' }, { id: '2', state: 'confirmed', media_id: '90' }, { id: '3', state: 'unconfirmed', media_id: '91' }]), { linkedCount: 4, referenceMediaIds: ['90'], unreadyAssetIds: ['3'], missingAssetIds: ['4'] });
  assert.deepEqual(summarizeShotReferences([], []), { linkedCount: 0, referenceMediaIds: [], unreadyAssetIds: [], missingAssetIds: [] });
});

test('preparation compares context settings version and media but ignores renewed URLs', () => {
  const original = shot();
  for (const patch of [{ context_hash: 'b'.repeat(64) }, { row_version: '10' }, { deleted_at: 'today' }, { image: { media_id: '8' } }, { image_settings: { ...original.image_settings, resolution: '4K' } }]) assert.equal(shotPreparationChanged(original, { ...original, ...patch }), true);
  assert.equal(shotPreparationChanged({ ...original, image: { media_id: '8', url: 'old' } }, { ...original, image: { media_id: '8', url: 'new' } }), false);
});

test('failed save never reads and releases lock', async () => {
  let reads = 0; let releases = 0;
  assert.equal(await prepareShotOperation({ save: async () => false, local: shot, read: async () => { reads++; return shot(); }, valid: () => true, accept: () => {}, changed: () => {}, release: () => releases++ }), null);
  assert.deepEqual([reads, releases], [0, 1]);
});

test('changed or rejected read stops preparation and releases lock', async () => {
  let accepted = 0; let changed = 0; let releases = 0;
  const options = { save: async () => true, local: shot, read: async () => ({ ...shot(), context_hash: 'b'.repeat(64) }), valid: () => true, accept: () => accepted++, changed: () => changed++, release: () => releases++ };
  assert.equal(await prepareShotOperation(options), null);
  assert.deepEqual([accepted, changed, releases], [1, 1, 1]);
  await assert.rejects(prepareShotOperation({ ...options, read: async () => { throw new Error('offline'); } }));
  assert.equal(releases, 2);
});

test('stale response is ignored and successful preparation retains lock until release', async () => {
  let releases = 0; let accepted = 0;
  const options = { save: async () => true, local: shot, read: async () => shot(), valid: () => false, accept: () => accepted++, changed: () => {}, release: () => releases++ };
  assert.equal(await prepareShotOperation(options), null); assert.equal(accepted, 0);
  const prepared = await prepareShotOperation({ ...options, valid: () => true });
  assert.equal(releases, 1); assert.equal(prepared.shot.id, '11');
  prepared.release(); prepared.release(); assert.equal(releases, 2);
});

test('unsupported or unknown capabilities never silently drop references', () => {
  const known = { known: true, reference_images: true };
  assert.equal(imageGenerationBlockReason('1', known, 3, false), '');
  assert.equal(imageGenerationBlockReason('1', { ...known, reference_images: false }, 0, false), '');
  assert.ok(imageGenerationBlockReason('1', { ...known, reference_images: false }, 1, false));
  assert.ok(imageGenerationBlockReason('1', { ...known, known: false }, 0, false));
  assert.ok(imageGenerationBlockReason(undefined, known, 0, false));
  assert.ok(imageGenerationBlockReason('1', known, 0, true));
});

test('prepared operation can be invalidated before the network mutation', async () => {
  let current = true;
  const prepared = await prepareShotOperation({ save: async () => true, local: shot, read: async () => shot(), valid: () => current, accept: () => {}, changed: () => {}, release: () => {} });
  assert.equal(prepared.isCurrent(), true);
  current = false;
  assert.equal(prepared.isCurrent(), false);
  prepared.release();
});
