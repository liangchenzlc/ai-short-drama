import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

function compile(source) {
  return ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
}
const source = readFileSync(new URL('../../src/features/assistant/references/assistant-materials.ts', import.meta.url), 'utf8');
const materials = await import(`data:text/javascript;base64,${Buffer.from(compile(source)).toString('base64')}`);

const scene = { project_id: '1', source_kind: 'project', source_id: '1', kind: 'shot', id: '20', revision: '3', content_revision: '9', checksum: 'a'.repeat(64), name: '场景1', project_name: '作品', source_name: '作品' };
const other = { ...scene, id: '21', name: '场景2' };

test('re-submitted materials are not retained twice, which the server rejects as duplicate selection', () => {
  const existing = [
    { subject_key: 'subject:' + '1'.repeat(64), ...scene },
    { subject_key: 'subject:' + '2'.repeat(64), ...other },
  ];
  assert.deepEqual(materials.retainedSubjectKeys(existing, [scene]), ['subject:' + '2'.repeat(64)]);
  assert.deepEqual(materials.retainedSubjectKeys(existing, [scene, other]), []);
  assert.deepEqual(materials.retainedSubjectKeys(existing, []), ['subject:' + '1'.repeat(64), 'subject:' + '2'.repeat(64)]);
});

test('only canvas nodes may carry a content revision and checksums must be well formed', () => {
  assert.deepEqual(materials.materialReference(scene), {
    project_id: '1', source_kind: 'project', source_id: '1', kind: 'shot', id: '20', revision: '3', checksum: 'a'.repeat(64),
  });
  assert.deepEqual(materials.materialReference({ ...scene, kind: 'node', source_kind: 'canvas', source_id: 'c1', content_revision: '9', checksum: 'not-hex' }), {
    project_id: '1', source_kind: 'canvas', source_id: 'c1', kind: 'node', id: '20', revision: '3', content_revision: '9',
  });
});

test('materials merge by identity and stay bounded', () => {
  assert.deepEqual(materials.mergeMaterials([scene], [scene, other]), [scene, other]);
  assert.equal(materials.mergeMaterials([], Array.from({ length: 20 }, (unused, index) => ({ ...scene, id: String(index) }))).length, 16);
});
