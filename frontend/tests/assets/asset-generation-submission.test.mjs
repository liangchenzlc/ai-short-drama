import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../../src/features/creation/assets/images/asset-generation-submission.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { assetGenerationAcceptanceUnknown, isPendingAssetGeneration } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('network, timeout and 503 preserve uncertain requests; validation and version rejection are definite', () => {
  for (const cause of [new Error('network'), { status: 503 }, { status: 500 }, { status: 408 }]) assert.equal(assetGenerationAcceptanceUnknown(cause), true);
  for (const cause of [{ status: 422 }, { status: 409 }, { status: 403 }]) assert.equal(assetGenerationAcceptanceUnknown(cause), false);
});

test('submission recovery requires exact key, saved source version and full request', () => {
  const request = { key: 'original-key', body: { source: { scene: 'asset_image', asset_id: '9007199254740993', row_version: '7', draft_id: '24', draft_version: '8' }, input: { prompt: '', reference_media_ids: [] }, parameters: { count: 2 } } };
  assert.equal(isPendingAssetGeneration(request), true);
  assert.equal(isPendingAssetGeneration({ ...request, key: '' }), false);
  assert.equal(isPendingAssetGeneration({ ...request, body: { ...request.body, source: { ...request.body.source, row_version: undefined } } }), false);
  assert.equal(isPendingAssetGeneration({ ...request, body: { ...request.body, source: { scene: 'shot_image', shot_id: '99' } } }), false);
  assert.equal(isPendingAssetGeneration({ ...request, body: { ...request.body, parameters: undefined } }), false);
});
