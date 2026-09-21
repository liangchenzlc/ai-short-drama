import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/projects/storyboard-session.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const session = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const shot = (id, script, row = '1') => ({ id, position: Number(id), script, row_version: row, asset_ids: [], image_settings: { resolution: '2K', aspect: 'inherit', layout: 'single' }, context_hash: id.repeat(64).slice(0, 64), image: null, deleted_at: null });

test('remote reload preserves dirty shot content while accepting unrelated server changes', () => {
  const local = { episode_id: '7', storyboard_version: '3', items: [shot('1', 'local edit', '2'), shot('2', 'old', '1')], total: 2, offset: 0, limit: 100 };
  const remote = { ...local, storyboard_version: '4', items: [shot('1', 'server stale', '2'), shot('2', 'server changed', '2')] };
  const merged = session.mergeStoryboardReload(remote, local, new Set(['1']));
  assert.equal(merged.items[0].script, 'local edit');
  assert.equal(merged.items[0].row_version, '2');
  assert.equal(merged.items[1].script, 'server changed');
  assert.equal(merged.storyboard_version, '4');
});

test('reload keeps a locally created dirty row visible when a remote page omits it', () => {
  const local = { episode_id: '7', storyboard_version: '3', items: [shot('1', 'local')], total: 1, offset: 0, limit: 100 };
  const remote = { ...local, storyboard_version: '4', items: [] };
  assert.deepEqual(session.mergeStoryboardReload(remote, local, new Set(['1'])).items.map((item) => item.id), ['1']);
});

test('navigation remains guarded while a timer, dirty row, or request is unsettled', () => {
  assert.equal(session.hasUnsettledStoryboard(new Set(), new Map(), new Map()), false);
  assert.equal(session.hasUnsettledStoryboard(new Set(['1']), new Map(), new Map()), true);
  assert.equal(session.hasUnsettledStoryboard(new Set(), new Map([['1', Promise.resolve(true)]]), new Map()), true);
  assert.equal(session.hasUnsettledStoryboard(new Set(), new Map(), new Map([['1', 1]])), true);
});

test('task polling continues only while a task is queued or running', () => {
  assert.equal(session.shouldPollStoryboardTasks([{ status: 'succeeded' }, { status: 'failed' }]), false);
  assert.equal(session.shouldPollStoryboardTasks([{ status: 'succeeded' }, { status: 'running' }]), true);
});
