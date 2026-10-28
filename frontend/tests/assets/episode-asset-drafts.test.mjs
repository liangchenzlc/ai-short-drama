import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../../src/features/creation/assets/sessions/local-asset-draft-model.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { assetCreationAttempt, assetCreationUnknown, assetDraftBody, assetDraftHasInput, blankAssetDraft, localAssetDraftKey, readLocalAssetDrafts } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

const referencesSource = readFileSync(new URL('../../src/features/creation/assets/images/asset-prompt-references.ts', import.meta.url), 'utf8');
const referencesCompiled = ts.transpileModule(referencesSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { combineAssetPromptReferences, remapAssetPromptReferences } = await import(`data:text/javascript;base64,${Buffer.from(referencesCompiled).toString('base64')}`);

const reference = id => ({ media_id: id, url: `/${id}.png`, name: id });

test('reference numbering follows shared then private order and keeps decimal media identifiers intact', () => {
  const shared = [reference('9007199254740993'), reference('2')];
  const combined = combineAssetPromptReferences(shared, [reference('2'), reference('3'), reference('3')]);
  assert.deepEqual(combined.map(item => item.media_id), ['9007199254740993', '2', '3']);
  assert.equal(combined[1], shared[1]);
});

test('reference reordering preserves image identity for repeated and adjoining prompt mentions', () => {
  const before = [reference('a'), reference('b')];
  assert.equal(remapAssetPromptReferences('让@图片1站在@图片2旁，@图片1 保持外观', before, [before[1], before[0]]), '让@图片2站在@图片1旁，@图片2 保持外观');
  assert.equal(remapAssetPromptReferences('@图片1@图片2', before, [before[1], before[0]]), '@图片2@图片1');
});

test('removing a reference deletes its mentions without redirecting later numbered images', () => {
  const before = Array.from({ length: 12 }, (_, index) => reference(String(index + 1)));
  const after = before.filter(item => item.media_id !== '2');
  assert.equal(remapAssetPromptReferences('@图片2、@图片10、@图片12；普通文字和@图片99', before, after), '、@图片9、@图片11；普通文字和@图片99');
  assert.equal(remapAssetPromptReferences('第一行\n  @图片1\n第二行', before, before.map(item => ({ ...item, url: '/renewed.png' }))), '第一行\n  @图片1\n第二行');
  assert.equal(remapAssetPromptReferences('@图片1', before, []), '');
});

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

test('actor/project scopes differ, interrupted creation restores unknown, private input counts as draft', () => {
  const key = localAssetDraftKey('1', '10');
  assert.notEqual(key, localAssetDraftKey('2', '10'));
  assert.notEqual(key, localAssetDraftKey('1', '11'));
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
  const key = localAssetDraftKey('1', '10');
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
