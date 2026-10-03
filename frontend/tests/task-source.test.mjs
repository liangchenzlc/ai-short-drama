import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/generations/task-source.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { taskSourceTarget } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('completed business tasks return the source stage with exact string identifiers', () => {
  const project_id = '9007199254740993123';
  const episode_id = '9007199254740993124';
  for (const [scene, stage] of Object.entries({
    novel_script: 'source', script_assets: 'assets', asset_image: 'assets',
    script_shots: 'storyboard', shot_image: 'storyboard', shot_video: 'storyboard',
    dialogue_extract: 'assembly', dialogue_audio: 'assembly',
  })) {
    assert.deepEqual(taskSourceTarget({ source: { scene, project_id, episode_id } }),
      { kind: 'episode', project_id, episode_id, stage });
  }
});

test('project and personal asset sources have separate destinations', () => {
  assert.deepEqual(taskSourceTarget({ source: { scene: 'asset_image', project_id: '10', asset_id: '50' } }),
    { kind: 'project', project_id: '10' });
  assert.deepEqual(taskSourceTarget({ source: { scene: 'character_voice_design', project_id: '10', asset_id: '50' } }),
    { kind: 'project', project_id: '10' });
  assert.deepEqual(taskSourceTarget({ source: { scene: 'asset_image', asset_id: '50' } }),
    { kind: 'asset', asset_id: '50' });
});

test('generic and incomplete legacy sources do not invent project or episode IDs', () => {
  assert.equal(taskSourceTarget({ source: null }), null);
  assert.equal(taskSourceTarget({}), null);
  assert.equal(taskSourceTarget({ source: { scene: 'shot_image', shot_id: '101', layout: 'single' } }), null);
  assert.equal(taskSourceTarget({ source: { scene: 'novel_script', project_id: '10' } }), null);
});
