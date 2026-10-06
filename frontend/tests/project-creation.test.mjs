import assert from 'node:assert/strict';
import test from 'node:test';
import { prepareProjectCreation, readProjectCreation, clearProjectCreation } from '../src/features/projects/project-creation.ts';

function storage() {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}
const body = { name: '故事', aspect: '16:9', synopsis: '', style: '', workspace_mode: 'infinite_canvas' };

test('无限画布创建结果不明时复用原键，改动内容才换键', () => {
  const data = storage();
  const first = prepareProjectCreation(data, 'actor1', body, () => 'first');
  assert.equal(prepareProjectCreation(data, 'actor1', { ...body }, () => 'different').key, first.key);
  assert.equal(prepareProjectCreation(data, 'actor1', { ...body, name: '新故事' }, () => 'second').key, 'second');
  clearProjectCreation(data, 'actor1', 'first');
  assert.equal(readProjectCreation(data, 'actor1').key, 'second');
  clearProjectCreation(data, 'actor1', 'second');
  assert.equal(readProjectCreation(data, 'actor1'), null);
});

test('创建恢复按账号隔离，存储失败时请求前中止', () => {
  const data = storage();
  prepareProjectCreation(data, 'actor1', body, () => 'first');
  assert.equal(readProjectCreation(data, 'actor2'), null);
  assert.throws(() => prepareProjectCreation({ ...data, setItem: () => { throw new Error('quota'); } }, 'actor2', body, () => 'second'), /quota/);
});
