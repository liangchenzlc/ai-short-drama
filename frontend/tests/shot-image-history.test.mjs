import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/projects/shot-image-history.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { createShotImageHistory, emptyShotImageHistory } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

const task = (id, status = 'succeeded', shotId = 'shot-1') => ({
  generation_id: String(id), service_type: 'image', status,
  source: { scene: 'shot_image', shot_id: shotId, layout: 'single' },
  created_at: '2026-09-23T08:00:00Z', can_cancel: status === 'running' || status === 'queued',
  can_retry: status === 'failed', can_resume: false,
});
const candidate = (id, shotId = 'shot-1') => ({
  asset_id: String(id), generation_id: '1', record_id: '1', media_id: String(id),
  media_type: 'image', name: `Image ${id}`, row_version: '1', url: null,
  source: { scene: 'shot_image', shot_id: shotId, layout: 'single' },
  created_at: '2026-09-23T08:00:00Z',
});
const page = (items, filters) => ({ items: items.slice(filters.offset, filters.offset + filters.limit), total: items.length, offset: filters.offset, limit: filters.limit });
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
};
const flush = async () => { for (let turn = 0; turn < 40; turn++) await Promise.resolve(); };

function fixture(context, initial = {}) {
  const data = { tasks: [], candidates: [], ...initial };
  const calls = [];
  const timers = new Map();
  let timerId = 0;
  let state;
  const api = {
    async listTasks(filters, signal) {
      calls.push({ kind: 'tasks', filters, signal });
      return page(data.tasks.filter(item => !filters.status || item.status === filters.status), filters);
    },
    async listCandidates(filters, signal) {
      calls.push({ kind: 'candidates', filters, signal });
      return page(data.candidates, filters);
    },
    async detail(id, signal) {
      calls.push({ kind: 'detail', id, signal });
      return { ...data.tasks.find(item => item.generation_id === id), input: {}, parameters: {}, result: { text: null, assets: [], partial: false } };
    },
  };
  const history = createShotImageHistory({
    shotId: 'shot-1', api, onChange: next => { state = next; },
    describeError: cause => cause.message,
    schedule: (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; },
    cancel: id => timers.delete(id),
  });
  context.after(() => history.stop());
  return { data, calls, timers, api, history, get state() { return state; } };
}

test('loads twenty history/candidates and all active pages without active IDs advancing history offsets', async context => {
  const tasks = Array.from({ length: 65 }, (_, index) => task(100 - index, index >= 25 ? 'running' : 'succeeded'));
  const setup = fixture(context, { tasks, candidates: Array.from({ length: 43 }, (_, index) => candidate(100 - index)) });
  await setup.history.start();
  assert.equal(setup.state.tasks.length, 60);
  assert.equal(setup.state.candidates.length, 20);
  assert.equal(setup.state.hasMoreTasks, true);
  await setup.history.loadMoreTasks();
  await setup.history.loadMoreCandidates();
  assert.equal(setup.state.tasks.length, 65);
  assert.equal(setup.state.candidates.length, 40);
  assert.deepEqual(setup.calls.filter(call => call.kind === 'tasks' && !call.filters.status).map(call => call.filters.offset), [0, 20]);
  for (const call of setup.calls.filter(call => call.filters)) {
    assert.equal(call.filters.source_scene, 'shot_image');
    assert.equal(call.filters.source_id, 'shot-1');
    assert.equal(call.filters.limit, 20);
  }
  await setup.history.loadMoreTasks();
  await setup.history.loadMoreTasks();
  await setup.history.loadMoreCandidates();
  assert.equal(setup.state.hasMoreTasks, false);
  assert.equal(setup.state.hasMoreCandidates, false);
});

test('reconciles an old active task and preserves cancelled partial output even when candidate refresh fails', async context => {
  const setup = fixture(context, { tasks: [...Array.from({ length: 25 }, (_, index) => task(100 - index)), task('1', 'running')] });
  await setup.history.start();
  setup.data.tasks[setup.data.tasks.length - 1] = task('1', 'cancelled');
  setup.api.detail = async id => ({ ...task(id, 'cancelled'), input: {}, parameters: {}, result: { text: null, assets: [candidate('501')], partial: true } });
  setup.api.listCandidates = async () => { throw new Error('offline'); };
  await setup.history.refresh();
  assert.equal(setup.state.tasks.find(item => item.generation_id === '1').status, 'cancelled');
  assert.deepEqual(setup.state.candidates.map(item => item.asset_id), ['501']);
  assert.match(setup.state.error, /offline/);
  setup.api.listCandidates = async filters => page(Array.from({ length: 22 }, (_, index) => candidate(501 - index)), filters);
  await setup.history.refresh();
  await setup.history.loadMoreCandidates();
  assert.equal(setup.state.candidates.length, 22);
  assert.equal(setup.state.hasMoreCandidates, false);
  assert.equal(setup.state.error, '');
});

test('failed branches retain data, successful branches update, failed page retries the same offset', async context => {
  const setup = fixture(context, { tasks: [task('1', 'running')], candidates: Array.from({ length: 23 }, (_, index) => candidate(100 - index)) });
  await setup.history.start();
  const listTasks = setup.api.listTasks;
  const listCandidates = setup.api.listCandidates;
  setup.api.listTasks = async () => { throw new Error('tasks offline'); };
  setup.data.candidates.unshift(candidate('101'));
  await setup.history.refresh();
  assert.equal(setup.state.tasks[0].status, 'running');
  assert.equal(setup.state.candidates[0].asset_id, '101');
  assert.match(setup.state.error, /offline/);
  setup.api.listCandidates = async () => { throw new Error('page offline'); };
  await setup.history.loadMoreCandidates();
  setup.api.listCandidates = listCandidates;
  setup.api.listTasks = listTasks;
  await setup.history.loadMoreCandidates();
  assert.equal(setup.calls.at(-1).filters.offset, 21);
  assert.equal(setup.state.candidates.length, 24);
});

test('merges successful active pages when a later active page fails', async context => {
  const setup = fixture(context, { tasks: [...Array.from({ length: 25 }, (_, index) => task(100 - index)), ...Array.from({ length: 25 }, (_, index) => task(50 - index, 'queued'))] });
  const listTasks = setup.api.listTasks;
  setup.api.listTasks = async (filters, signal) => {
    if (filters.status === 'queued' && filters.offset === 20) throw new Error('second active page failed');
    return listTasks(filters, signal);
  };
  await setup.history.start();
  assert.equal(setup.state.tasks.length, 40);
  assert.match(setup.state.error, /second active/);
  assert.equal([...setup.timers.values()][0].delay, 3000);
});

test('serializes pagination with refresh, coalesces double clicks and rebases after new first-page records', async context => {
  const setup = fixture(context, { tasks: Array.from({ length: 65 }, (_, index) => task(100 - index)) });
  await setup.history.start();
  const pending = deferred();
  const listTasks = setup.api.listTasks;
  setup.api.listTasks = async (filters, signal) => {
    if (!filters.status && filters.offset === 20) { await pending.promise; }
    return listTasks(filters, signal);
  };
  const first = setup.history.loadMoreTasks();
  const duplicate = setup.history.loadMoreTasks();
  const refresh = setup.history.refresh();
  await flush();
  assert.equal(setup.calls.filter(call => call.kind === 'tasks' && !call.filters.status).length, 1);
  pending.resolve();
  await Promise.all([first, duplicate, refresh]);
  setup.data.tasks.unshift(task('102'), task('101'));
  await setup.history.refresh();
  await setup.history.loadMoreTasks();
  assert.deepEqual(setup.calls.filter(call => call.kind === 'tasks' && !call.filters.status).map(call => call.filters.offset), [0, 20, 0, 0, 42]);
  assert.equal(setup.state.tasks.length, 62);
});

test('a completely replaced first page restarts coverage without dropping loaded history', async context => {
  const setup = fixture(context, { tasks: Array.from({ length: 45 }, (_, index) => task(100 - index)) });
  await setup.history.start();
  await setup.history.loadMoreTasks();
  setup.data.tasks.unshift(...Array.from({ length: 25 }, (_, index) => task(200 - index)));
  await setup.history.refresh();
  await setup.history.loadMoreTasks();
  assert.equal(setup.calls.at(-1).filters.offset, 20);
  assert.ok(setup.state.tasks.some(item => item.generation_id === '176'));
  assert.ok(setup.state.tasks.some(item => item.generation_id === '61'));
});

test('polling waits for completion, uses retained active status on failure and stops/resumes safely', async context => {
  const setup = fixture(context, { tasks: [task('1', 'running')] });
  assert.equal(setup.calls.length, 0);
  await setup.history.start();
  assert.equal([...setup.timers.values()][0].delay, 3000);
  const pending = deferred();
  setup.api.listTasks = async () => pending.promise;
  const scheduled = [...setup.timers.values()][0];
  setup.timers.clear();
  scheduled.callback();
  await flush();
  assert.equal(setup.timers.size, 0);
  setup.history.stop();
  assert.ok(setup.calls.every(call => call.signal.aborted));
  const before = setup.state;
  pending.resolve({ items: [task('2')], total: 1, offset: 0, limit: 20 });
  await flush();
  assert.deepEqual(setup.state, before);
  assert.equal(setup.timers.size, 0);
  setup.api.listTasks = async () => { throw new Error('offline'); };
  await setup.history.start();
  assert.equal([...setup.timers.values()][0].delay, 3000);
  setup.data.tasks = [task('1')];
  setup.api.listTasks = async filters => page(filters.status ? [] : setup.data.tasks, filters);
  await setup.history.refresh();
  assert.equal([...setup.timers.values()][0].delay, 15000);
});

test('rejects wrong-shot and wrong-scene list/detail results without moving pagination by merged IDs', async context => {
  const setup = fixture(context, { tasks: [task('1', 'running')] });
  await setup.history.start();
  setup.data.tasks = [task('2', 'succeeded', 'shot-2'), { ...task('3'), source: { scene: 'asset_image', asset_id: 'shot-1' } }];
  setup.data.candidates = [candidate('9', 'shot-2'), { ...candidate('10'), media_type: 'video' }];
  setup.api.detail = async () => ({ ...task('1', 'succeeded', 'shot-2'), result: { assets: [candidate('11')] } });
  await setup.history.refresh();
  assert.deepEqual(setup.state.tasks.map(item => item.generation_id), ['1']);
  assert.deepEqual(setup.state.candidates, []);
});

test('reconciles disappearance from active queries even when history still reports the old running snapshot', async context => {
  const setup = fixture(context, { tasks: [task('1', 'running')] });
  await setup.history.start();
  setup.api.listTasks = async filters => page(filters.status ? [] : [task('1', 'running')], filters);
  setup.api.detail = async () => ({ ...task('1', 'failed'), input: {}, parameters: {}, result: { text: null, assets: [candidate('7')], partial: true } });
  await setup.history.refresh();
  assert.equal(setup.state.tasks[0].status, 'failed');
  assert.equal(setup.state.candidates[0].asset_id, '7');
});

const hookSource = readFileSync(new URL('../src/features/projects/useShotImageGeneration.ts', import.meta.url), 'utf8');
const hookCompiled = ts.transpileModule(hookSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

test('refresh requested during an in-flight refresh runs afterward and stale same-shot failures cannot replace resumed state', async context => {
  const setup = fixture(context, { tasks: [task('1')] });
  const pending = deferred();
  const listTasks = setup.api.listTasks;
  let waitOnce = true;
  setup.api.listTasks = async (filters, signal) => {
    if (!filters.status && waitOnce) { waitOnce = false; return pending.promise; }
    return listTasks(filters, signal);
  };
  const first = setup.history.start();
  await flush();
  setup.data.tasks = [task('2')];
  const next = setup.history.refresh();
  const duplicate = setup.history.refresh();
  pending.resolve({ items: [task('1')], total: 1, offset: 0, limit: 20 });
  await Promise.all([first, next, duplicate]);
  assert.deepEqual(setup.state.tasks.map(item => item.generation_id), ['2', '1']);
  assert.equal(setup.calls.filter(call => call.kind === 'tasks' && !call.filters.status).length, 1);
  const late = deferred();
  setup.api.listTasks = async () => late.promise;
  const old = setup.history.refresh();
  await flush();
  setup.history.stop();
  setup.api.listTasks = listTasks;
  await setup.history.start();
  const resumed = setup.state;
  late.reject(new Error('late failure'));
  await old;
  assert.deepEqual(setup.state, resumed);
  assert.equal(setup.state.error, '');
  assert.equal(setup.timers.size, 1);
});

test('pagination does not skip a boundary item removed from its old offset by a concurrent deletion', async context => {
  const setup = fixture(context, { candidates: Array.from({ length: 65 }, (_, index) => candidate(100 - index)) });
  await setup.history.start();
  await setup.history.loadMoreCandidates();
  setup.data.candidates.splice(5, 1);
  await setup.history.loadMoreCandidates();
  await setup.history.refresh();
  while (setup.state.hasMoreCandidates) await setup.history.loadMoreCandidates();
  assert.ok(setup.state.candidates.some(item => item.asset_id === '60'));
  assert.ok(setup.state.candidates.some(item => item.asset_id === '36'));
});

test('manual refresh renews signed URLs beyond page one after candidate pagination is exhausted', async context => {
  const setup = fixture(context, { candidates: Array.from({ length: 21 }, (_, index) => ({ ...candidate(100 - index), url: 'old-url' })) });
  await setup.history.start();
  await setup.history.loadMoreCandidates();
  assert.equal(setup.state.hasMoreCandidates, false);
  setup.data.candidates = setup.data.candidates.map(item => ({ ...item, url: 'renewed-url' }));
  setup.calls.length = 0;
  await setup.history.refresh();
  assert.equal(setup.state.candidates.find(item => item.asset_id === '80').url, 'renewed-url');
  assert.equal(setup.state.candidates.length, 21);
  assert.equal(setup.state.hasMoreCandidates, false);
  assert.deepEqual(setup.calls.filter(call => call.kind === 'candidates').map(call => call.filters.offset), [0, 20]);
});

test('polling refreshes only the head while manual refresh revalidates the loaded range and preserves the next page', async context => {
  const setup = fixture(context, { candidates: Array.from({ length: 63 }, (_, index) => ({ ...candidate(100 - index), url: 'old-url' })) });
  await setup.history.start();
  await setup.history.loadMoreCandidates();
  setup.data.candidates = setup.data.candidates.map(item => ({ ...item, url: 'renewed-url' }));
  setup.calls.length = 0;
  const scheduled = [...setup.timers.values()][0];
  setup.timers.clear();
  scheduled.callback();
  await flush();
  assert.deepEqual(setup.calls.filter(call => call.kind === 'candidates').map(call => call.filters.offset), [0]);
  assert.equal(setup.state.candidates.find(item => item.asset_id === '80').url, 'old-url');
  setup.calls.length = 0;
  await setup.history.refresh();
  assert.equal(setup.state.candidates.find(item => item.asset_id === '61').url, 'renewed-url');
  assert.equal(setup.state.candidates.length, 40);
  assert.equal(setup.state.hasMoreCandidates, true);
  assert.deepEqual(setup.calls.filter(call => call.kind === 'candidates').map(call => call.filters.offset), [0, 20]);
  await setup.history.loadMoreCandidates();
  assert.equal(setup.calls.at(-1).filters.offset, 40);
  assert.equal(setup.state.candidates.length, 60);
});

test('failed loaded-page revalidation retains old candidates and retries without skipping the failed range', async context => {
  const setup = fixture(context, { candidates: Array.from({ length: 43 }, (_, index) => ({ ...candidate(100 - index), url: 'old-url' })) });
  await setup.history.start();
  await setup.history.loadMoreCandidates();
  await setup.history.loadMoreCandidates();
  const listCandidates = setup.api.listCandidates;
  setup.data.candidates = setup.data.candidates.map(item => ({ ...item, url: 'renewed-url' }));
  setup.api.listCandidates = async (filters, signal) => {
    if (filters.offset === 20) throw new Error('page two offline');
    return listCandidates(filters, signal);
  };
  await setup.history.refresh();
  assert.equal(setup.state.candidates.length, 43);
  assert.equal(setup.state.candidates[0].url, 'renewed-url');
  assert.equal(setup.state.candidates.at(-1).url, 'old-url');
  assert.match(setup.state.error, /page two offline/);
  setup.api.listCandidates = listCandidates;
  await setup.history.loadMoreCandidates();
  assert.equal(setup.calls.at(-1).filters.offset, 20);
  setup.calls.length = 0;
  await setup.history.refresh();
  assert.equal(setup.state.candidates.at(-1).url, 'renewed-url');
  assert.equal(setup.state.hasMoreCandidates, false);
  assert.deepEqual(setup.calls.filter(call => call.kind === 'candidates').map(call => call.filters.offset), [0, 20, 40]);
});

test('a terminal history row still reconciles disappeared active detail and recovers partial candidates on list failure', async context => {
  const setup = fixture(context, { tasks: [task('1', 'running')] });
  await setup.history.start();
  setup.data.tasks = [task('1', 'cancelled')];
  setup.api.listCandidates = async () => { throw new Error('offline'); };
  setup.api.detail = async () => ({ ...task('1', 'cancelled'), input: {}, parameters: {}, result: { text: null, assets: [candidate('7')], partial: true } });
  await setup.history.refresh();
  assert.deepEqual(setup.state.candidates.map(item => item.asset_id), ['7']);
});

function mountHook(context, api) {
  const slots = [];
  let cursor = 0;
  let effects = [];
  let props = { shotId: 'shot-1', enabled: false };
  const document = Object.assign(new EventTarget(), { visibilityState: 'visible' });
  const window = new EventTarget();
  const timers = new Map();
  let timerId = 0;
  const changed = (before, after) => !before || before.some((value, index) => value !== after[index]);
  const react = {
    useRef(value) { const index = cursor++; return slots[index] ??= { current: value }; },
    useState(initial) {
      const index = cursor++;
      slots[index] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slots[index].value, next => { slots[index].value = typeof next === 'function' ? next(slots[index].value) : next; }];
    },
    useCallback(callback, dependencies) {
      const index = cursor++;
      if (changed(slots[index]?.dependencies, dependencies)) slots[index] = { dependencies, callback };
      return slots[index].callback;
    },
    useEffect(callback, dependencies) {
      const index = cursor++;
      if (changed(slots[index]?.dependencies, dependencies)) effects.push({ index, callback, dependencies });
    },
  };
  const modules = {
    react,
    '../../api/http': { errorMessage: cause => cause.message },
    '../../api/modules/generations': { generations: { list: (...args) => api.listTasks(...args), detail: (...args) => api.detail(...args) } },
    '../../api/modules/media-library': { mediaLibrary: { list: (...args) => api.listCandidates(...args) } },
    './shot-image-history': {
      emptyShotImageHistory,
      createShotImageHistory: options => createShotImageHistory({ ...options,
        schedule: (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; },
        cancel: id => timers.delete(id),
      }),
    },
  };
  const exports = {};
  new Function('require', 'exports', 'document', 'window', hookCompiled)(name => {
    assert.ok(modules[name], `Unexpected import ${name}`);
    return modules[name];
  }, exports, document, window);
  const render = (next = props) => {
    props = next;
    cursor = 0;
    effects = [];
    let value = exports.useShotImageGeneration(props);
    if (effects.length) {
      for (const effect of effects) slots[effect.index]?.cleanup?.();
      for (const effect of effects) slots[effect.index] = { dependencies: effect.dependencies, cleanup: effect.callback() };
      cursor = 0;
      effects = [];
      value = exports.useShotImageGeneration(props);
    }
    return value;
  };
  const unmount = () => { for (const slot of slots) slot?.cleanup?.(); };
  context.after(unmount);
  return { render, unmount, document, window, timers };
}

test('hook stays idle when disabled/hidden, resumes on visibility/focus and ignores shot-switch/unmount responses', async context => {
  const calls = [];
  const delayed = deferred();
  let delay = false;
  const api = {
    async listTasks(filters, signal) {
      calls.push({ filters, signal });
      if (delay && filters.source_id === 'shot-1') return delayed.promise;
      return page(filters.status ? [] : [task(filters.source_id, 'succeeded', filters.source_id)], filters);
    },
    async listCandidates(filters, signal) { calls.push({ filters, signal }); return page([], filters); },
    async detail() { throw new Error('No missing active tasks expected'); },
  };
  const hook = mountHook(context, api);
  await hook.render().refresh();
  assert.equal(calls.length, 0);
  hook.document.visibilityState = 'hidden';
  hook.render({ shotId: 'shot-1', enabled: true });
  await flush();
  assert.equal(calls.length, 0);
  hook.document.visibilityState = 'visible';
  hook.document.dispatchEvent(new Event('visibilitychange'));
  await flush();
  assert.equal(hook.render().tasks[0].generation_id, 'shot-1');
  assert.equal(hook.timers.size, 1);
  hook.document.visibilityState = 'hidden';
  hook.document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(hook.timers.size, 0);
  const hiddenCount = calls.length;
  await hook.render().loadMoreTasks();
  hook.window.dispatchEvent(new Event('focus'));
  await flush();
  assert.equal(calls.length, hiddenCount);
  hook.document.visibilityState = 'visible';
  hook.window.dispatchEvent(new Event('focus'));
  await flush();
  assert.ok(calls.length > hiddenCount);
  hook.render({ shotId: 'shot-1', enabled: false });
  assert.equal(hook.timers.size, 0);
  assert.ok(calls.every(call => call.signal.aborted));
  delay = true;
  hook.render({ shotId: 'shot-1', enabled: true });
  await flush();
  hook.render({ shotId: 'shot-2', enabled: true });
  await flush();
  assert.equal(hook.render().tasks[0].generation_id, 'shot-2');
  delayed.resolve({ items: [task('late')], total: 1, offset: 0, limit: 20 });
  await flush();
  assert.deepEqual(hook.render().tasks.map(item => item.generation_id), ['shot-2']);
  const finalPending = deferred();
  api.listTasks = async () => finalPending.promise;
  const refresh = hook.render().refresh();
  await flush();
  hook.unmount();
  finalPending.resolve({ items: [task('after-unmount', 'succeeded', 'shot-2')], total: 1, offset: 0, limit: 20 });
  await refresh;
  assert.deepEqual(hook.render().tasks.map(item => item.generation_id), ['shot-2']);
  assert.equal(hook.timers.size, 0);
});
