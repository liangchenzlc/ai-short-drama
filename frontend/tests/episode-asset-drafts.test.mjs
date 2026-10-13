import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/assets/episode-asset-drafts.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { assetCreationAttempt, assetCreationUnknown, assetDraftBody, assetDraftHasInput, blankAssetDraft, localAssetDraftKey, readLocalAssetDrafts } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('all category creates a temporary empty type; named categories preserve their selected type', () => {
  for (const kind of ['all', 'character', 'scene', 'prop']) {
    const draft = blankAssetDraft(kind);
    assert.equal(draft.kind, kind === 'all' ? null : kind);
    assert.match(draft.id, /^draft:/);
    assert.equal(assetDraftBody(draft), null);
    assert.equal(draft.name, '');
    assert.equal(draft.description, '');
    assert.equal(draft.prompt, '');
  }
  const blank = blankAssetDraft('all');
  assert.equal(assetDraftBody({ ...blank, name: '林晚' }), null);
  assert.equal(assetDraftBody({ ...blank, kind: 'character' }), null);
  assert.deepEqual(assetDraftBody({ ...blank, name: ' 林晚 ', kind: 'character' }), { kind: 'character', name: '林晚', label: '', description: '', prompt: '', tags: [], scene_time: '' });
});

test('unknown creation retains exact original key and input instead of making another asset', () => {
  const draft = { ...blankAssetDraft('scene'), name: '旧庭院' };
  const original = assetCreationAttempt(draft);
  assert.ok(original);
  const changed = { ...draft, attempt: original, status: 'unknown', name: '新庭院', description: '新输入' };
  assert.equal(assetCreationAttempt(changed), original);
  assert.equal(assetCreationAttempt(changed).body.name, '旧庭院');
  const knownFailure = assetCreationAttempt({ ...changed, status: 'error' });
  assert.notEqual(knownFailure.key, original.key);
  assert.equal(knownFailure.body.name, '新庭院');
  assert.equal(assetCreationUnknown({ status: 503 }), true);
  assert.equal(assetCreationUnknown(new Error('offline')), true);
  assert.equal(assetCreationUnknown({ status: 422 }), false);
});

test('actor/project/episode scopes differ, interrupted creation restores unknown, private input counts as draft', () => {
  const key = localAssetDraftKey('1', '10', '20');
  assert.notEqual(key, localAssetDraftKey('2', '10', '20'));
  assert.notEqual(key, localAssetDraftKey('1', '11', '20'));
  assert.notEqual(key, localAssetDraftKey('1', '10', '21'));
  const draft = { ...blankAssetDraft('prop'), status: 'saving', name: '灯笼' };
  draft.attempt = assetCreationAttempt(draft);
  globalThis.localStorage = { getItem: () => JSON.stringify([draft]) };
  const restored = readLocalAssetDrafts(key);
  assert.equal(restored[0].status, 'unknown');
  assert.deepEqual(restored[0].attempt, draft.attempt);
  assert.equal(assetDraftHasInput({ ...blankAssetDraft('all'), generation: { input_text: '个人输入', reference_media_ids: [] } }), true);
  globalThis.localStorage = { getItem: () => '{invalid' };
  assert.throws(() => readLocalAssetDrafts(key));
  delete globalThis.localStorage;
});

test('nested generation recovery validates its complete shape and leaves malformed raw records untouched', () => {
  const key = localAssetDraftKey('1', '10', '20');
  const generation = { model_config_id: '77', input_text: '个人输入', image_settings: { count: 1, aspect: '16:9', resolution: '2K' }, video_settings: null, reference_media_ids: ['9007199254740993'] };
  const draft = { ...blankAssetDraft('scene'), generation };
  const malformed = [
    {}, null, [],
    { ...generation, model_config_id: 77 },
    { ...generation, input_text: null },
    { ...generation, image_settings: {} },
    { ...generation, image_settings: { count: '1' } },
    { ...generation, image_settings: { count: 1, aspect: 16 } },
    { ...generation, video_settings: [] },
    { ...generation, video_settings: { duration_ms: '3000' } },
    { ...generation, reference_media_ids: null },
    { ...generation, reference_media_ids: [9007199254740993] },
  ];
  try {
    for (const value of malformed) {
      const raw = JSON.stringify([{ ...draft, generation: value }]);
      let stored = raw;
      globalThis.localStorage = { getItem: () => stored, setItem: (_, value) => { stored = value; }, removeItem: () => { stored = null; } };
      assert.throws(() => readLocalAssetDrafts(key), /素材草稿恢复记录损坏/);
      assert.equal(stored, raw);
    }
    for (const value of [generation, { ...generation, model_config_id: null, image_settings: null, video_settings: { aspect: '9:16', resolution: '720p', duration_ms: 3000 } }]) {
      globalThis.localStorage = { getItem: () => JSON.stringify([{ ...draft, generation: value }]) };
      const recovered = readLocalAssetDrafts(key);
      assert.deepEqual(recovered[0].generation, value);
      assert.equal(assetDraftHasInput(recovered[0]), true);
    }
  } finally { delete globalThis.localStorage; }
});
