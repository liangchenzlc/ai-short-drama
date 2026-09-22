import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/projects/asset-extraction-contract.ts', import.meta.url), 'utf8');
const reviewSource = readFileSync(new URL('../src/features/projects/ScriptAssetExtraction.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { scriptAssetsRequest, defaultAdoption, extractionApplyRequest } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const item = (id, matches = [], applied = null) => ({ candidate_id: id, draft: { name: id }, matches, applied });
const match = (id, exact = true) => ({ asset_id: id, row_version: '9', exact });
const result = items => ({ result_version: '3', content_version: '8', stale: false, items });

test('extraction submits saved script, chosen categories and instructions without image input', () => {
  assert.deepEqual(scriptAssetsRequest('11', '22', '33', '44', ['prop', 'scene', 'prop'], '重要道具'), {
    source: { scene: 'script_assets', project_id: '11', episode_id: '22', script_id: '33', content_version: '44' },
    extraction: { kinds: ['prop', 'scene'] }, instructions: '重要道具', parameters: {},
  });
  assert.throws(() => scriptAssetsRequest('1', '2', '3', '4', [], ''));
});

test('candidate review explains narrative importance before editable visual details', () => {
  assert.match(reviewSource, /core:\s*'推动剧情'/);
  assert.match(reviewSource, /continuity:\s*'维持连续性'/);
  assert.match(reviewSource, /item\.original\.story_function/);
  assert.match(reviewSource, /extraction-story-function/);
});

test('ambiguous names and aliases require explicit review', () => {
  assert.equal(defaultAdoption(item('a')), 'create');
  assert.equal(defaultAdoption(item('a', [match('91')])), '91');
  assert.equal(defaultAdoption(item('a', [match('91'), match('92')])), '');
  assert.equal(defaultAdoption(item('a', [match('91', false)])), '');
  assert.equal(defaultAdoption({ ...item('a'), duplicate_candidates: ['b'] }), '');
});

test('adoption includes selected candidates across categories and safe reuse versions only', () => {
  const data = result([item('a'), item('b', [match('99999999999999999')]), item('c'), item('d', [], { asset_id: '4' })]);
  assert.deepEqual(extractionApplyRequest(data, new Set(['a', 'b', 'd']), { a: 'create', b: '99999999999999999' }, '10'), {
    result_version: '3', content_version: '10', items: [
      { candidate_id: 'a', action: 'create', confirm_duplicate: false },
      { candidate_id: 'b', action: 'reuse', asset_id: '99999999999999999', expected_row_version: '9' },
    ],
  });
});

test('explicit duplicate creation is acknowledged but stale and unresolved results are blocked', () => {
  const data = result([item('a', [match('91')])]);
  assert.equal(extractionApplyRequest(data, new Set(['a']), { a: 'create' }, '8').items[0].confirm_duplicate, true);
  assert.throws(() => extractionApplyRequest({ ...data, stale: true }, new Set(['a']), { a: '91' }, '8'));
  assert.throws(() => extractionApplyRequest(data, new Set(['a']), { a: '92' }, '8'));
  assert.throws(() => extractionApplyRequest(data, new Set(), {}, '8'));
});
