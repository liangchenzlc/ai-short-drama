import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

function compile(path, dependencies, globals = {}) {
  const source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  runInNewContext(output, { exports, require: name => {
    if (!(name in dependencies)) throw new Error(`Missing dependency: ${name}`);
    return dependencies[name];
  }, AbortController, setTimeout, clearTimeout, ...globals });
  return exports;
}

const workflow = compile('features/projects/shot-image-workflow.ts', {});
const session = compile('features/projects/storyboard-session.ts', {});
const contract = compile('features/projects/workflow-contract.ts', {});
const shot = (id = '11') => ({ id, position: 1, row_version: '1', context_hash: 'first', script: 'original', duration_ms: 3000, source_excerpt: '', asset_ids: [], deleted_at: null, image: null, image_settings: { layout: 'single', aspect: 'inherit', resolution: '2K' } });
const page = (items, episode_id = '1') => ({ episode_id, storyboard_version: '1', items, total: items.length, offset: 0, limit: 100 });
const deferred = () => { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = async () => { for (let count = 0; count < 20; count++) await Promise.resolve(); };

function mount(context, overrides = {}) {
  const slots = [];
  let cursor = 0;
  let effects = [];
  let layouts = [];
  let tree;
  let barrier;
  const window = Object.assign(new EventTarget(), { confirm: () => true });
  const api = {
    shots: async () => page([shot(), { ...shot('12'), position: 2 }]),
    shot: async id => ({ shot: shot(id), storyboard_version: '1' }),
    update: async (id, body) => ({ shot: { ...shot(id), ...body, row_version: '2' }, storyboard_version: '2' }),
    move: async () => {}, order: async () => {}, remove: async () => {}, ...overrides,
  };
  const capabilityCalls = [];
  const capabilities = { known: true, reference_images: true, parameters: [], first_frame: false, last_frame: false };
  const configs = { capabilities: async (id, signal) => { capabilityCalls.push({ id, signal }); return capabilities; } };
  const effect = queue => (callback, dependencies) => {
    const index = cursor++;
    const previous = slots[index];
    if (!previous || !dependencies || dependencies.some((value, offset) => !Object.is(value, previous.dependencies?.[offset]))) {
      queue.push(() => { previous?.cleanup?.(); slots[index] = { dependencies, cleanup: callback() }; });
    }
  };
  const react = {
    useRef(value) { const index = cursor++; return slots[index] ??= { current: value }; },
    useState(value) { const index = cursor++; if (!(index in slots)) slots[index] = typeof value === 'function' ? value() : value; return [slots[index], next => { slots[index] = typeof next === 'function' ? next(slots[index]) : next; }]; },
    useEffect: (callback, dependencies) => effect(effects)(callback, dependencies),
    useLayoutEffect: (callback, dependencies) => effect(layouts)(callback, dependencies),
    useCallback(callback, dependencies) { const index = cursor++; const previous = slots[index]; if (!previous || dependencies.some((value, offset) => !Object.is(value, previous.dependencies[offset]))) slots[index] = { dependencies, callback }; return slots[index].callback; },
  };
  const jsx = (type, props) => ({ type, props });
  const { StoryboardStage } = compile('pages/projects/episode/StoryboardStage.tsx', {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx },
    antd: { Alert: 'Alert', Button: 'Button', Checkbox: 'Checkbox', Input: { TextArea: 'TextArea' }, InputNumber: 'InputNumber', Segmented: 'Segmented', Select: 'Select', Spin: 'Spin' },
    '../../../api/http': { ApiError: class extends Error {}, errorMessage: cause => cause.message },
    '../../../api/modules/storyboard': { storyboardApi: () => api },
    '../../../api/modules/assets': { assetLibraries: { list: async () => ({ items: [], total: 0 }) } },
    '../../../api/modules/generations': { generations: { list: async () => ({ items: [], total: 0 }) } },
    '../../../api/modules/ai-model-configs': { aiModelConfigs: configs },
    '../../../features/ai-config/config-events': { AI_CONFIGS_CHANGED: 'configs-changed' },
    '../../../features/projects/EpisodeModelSelect': { EpisodeModelSelect: 'EpisodeModelSelect' },
    '../../../features/projects/workflow-contract': contract,
    '../../../features/projects/storyboard-session': session,
    '../../../features/projects/shot-image-workflow': workflow,
    '../../../features/generations/attempt': {},
    '../../../features/generations/presentation': { taskLabel: () => '' },
    '../../../features/projects/StoryboardResultPreview': { StoryboardResultPreview: 'StoryboardResultPreview' },
    '../../../components/ui/Dialog': { Dialog: 'Dialog' },
    '../../../components/ui/LazyLoadMore': { LazyLoadMore: 'LazyLoadMore' },
    '../../../features/projects/ShotImageCandidates': { ShotImageCandidates: 'ShotImageCandidates' },
  }, { window });
  let props = { value: { aspect: '16:9', models: { storyboardText: '', storyboardImage: '' } }, readOnly: false, projectId: '1', episodeId: '1', scriptId: null, confirmed: false, writingSession: {}, registerBarrier: next => { barrier = next; }, onChange: next => { props.value = next; } };
  const render = patch => {
    props = { ...props, ...patch }; cursor = 0; effects = []; layouts = [];
    tree = StoryboardStage(props);
    for (const callback of [...layouts, ...effects]) callback();
    // Open only the first visible shot, as a user now does before editing.
    if (!nodes('ShotImageCandidates').length) {
      const row = nodes('button').find(button => button.className === 'storyboard-summary');
      if (row && !row.disabled) {
        row.onClick(); cursor = 0; effects = []; layouts = [];
        tree = StoryboardStage(props);
        for (const callback of [...layouts, ...effects]) callback();
      }
    }
    return tree;
  };
  const nodes = type => {
    const result = [];
    function visit(node) { if (!node || typeof node !== 'object') return; if (Array.isArray(node)) { node.forEach(visit); return; } if (node.type === type) result.push(node.props); visit(node.props?.children); }
    visit(tree); return result;
  };
  const unmount = () => { for (const slot of slots) slot?.cleanup?.(); };
  context.after(unmount);
  render();
  return { api, configs, capabilityCalls, window, render, nodes, unmount, get barrier() { return barrier; }, get props() { return props; } };
}

test('stage holds a synchronous per-shot lock through submission and blocks editing and ordering', async context => {
  const pending = deferred();
  let reads = 0; let orders = 0; let archives = 0;
  const setup = mount(context, { shot: async () => { reads++; return pending.promise; }, order: async () => { orders++; }, remove: async () => { archives++; } });
  await flush(); setup.render();
  const original = setup.nodes('ShotImageCandidates')[0];
  assert.equal(typeof original.prepareShot, 'function');
  const preparation = original.prepareShot();
  assert.equal(await original.prepareShot(), null);
  await flush(); setup.render();
  assert.equal(setup.nodes('TextArea')[1].disabled, true);
  assert.equal(setup.nodes('ShotImageCandidates').length, 1);
  setup.nodes('TextArea')[1].onChange({ target: { value: 'blocked edit' } });
  for (const button of setup.nodes('Button').filter(button => ['上移', '下移', '归档'].includes(button.children))) {
    if (button.disabled) await button.onClick();
  }
  assert.equal(orders, 0); assert.equal(archives, 0); assert.equal(reads, 1);
  pending.resolve({ shot: shot(), storyboard_version: '1' });
  const prepared = await preparation;
  assert.equal(prepared.shot.script, 'original');
  setup.render(); assert.equal(setup.nodes('TextArea')[1].disabled, true);
  prepared.release(); prepared.release(); setup.render();
  assert.equal(setup.nodes('TextArea')[1].disabled, false);
});

test('preparation accepts changed remote context but stops until reviewed', async context => {
  const setup = mount(context, { shot: async () => ({ shot: { ...shot(), context_hash: 'changed' }, storyboard_version: '2' }) });
  await flush(); setup.render();
  assert.equal(await setup.nodes('ShotImageCandidates')[0].prepareShot(), null);
  setup.render();
  assert.equal(setup.nodes('ShotImageCandidates')[0].shot.context_hash, 'changed');
  const prepared = await setup.nodes('ShotImageCandidates')[0].prepareShot();
  assert.equal(prepared.shot.context_hash, 'changed'); prepared.release();
});

test('late saves cannot overwrite another episode or continue preparation', async context => {
  const pending = deferred(); let reads = 0;
  const setup = mount(context, { update: async () => pending.promise, shot: async () => { reads++; return { shot: shot(), storyboard_version: '1' }; } });
  await flush(); setup.render();
  setup.nodes('TextArea')[1].onChange({ target: { value: 'old draft' } });
  const preparation = setup.nodes('ShotImageCandidates')[0].prepareShot();
  setup.api.shots = async () => page([{ ...shot(), script: 'new episode' }], '2');
  setup.render({ episodeId: '2' }); await flush(); setup.render();
  pending.resolve({ shot: { ...shot(), script: 'old draft', row_version: '2' }, storyboard_version: '2' });
  assert.equal(await preparation, null);
  setup.render();
  assert.equal(setup.nodes('ShotImageCandidates')[0].shot.script, 'new episode');
  assert.equal(reads, 0);
});

test('resolved default is queried once and focus invalidates capability without dropping model ID', async context => {
  const setup = mount(context);
  await flush(); setup.render();
  const select = setup.nodes('EpisodeModelSelect').find(item => item.kind === 'image');
  assert.equal(typeof select.onResolvedChange, 'function');
  select.onResolvedChange('7'); setup.render(); await flush(); setup.render();
  assert.deepEqual(setup.capabilityCalls.map(call => call.id), ['7']);
  assert.equal(setup.nodes('ShotImageCandidates').length, 1);
  assert.ok(setup.nodes('ShotImageCandidates').every(item => item.modelId === '7' && item.capabilities?.known));
  setup.window.dispatchEvent(new Event('focus')); setup.render();
  assert.ok(setup.nodes('ShotImageCandidates').every(item => item.modelId === '7' && item.capabilities === null && item.capabilitiesLoading));
  select.onResolvedChange('8'); setup.render(); await flush(); setup.render();
  assert.deepEqual(setup.capabilityCalls.map(call => call.id), ['7', '8']);
  assert.ok(setup.nodes('ShotImageCandidates').every(item => item.modelId === '8' && item.capabilities?.known));
});

test('save completes before single-shot read and read errors retain the saved draft', async context => {
  const calls = [];
  const pending = deferred();
  const setup = mount(context, {
    update: async (id, body) => { calls.push({ kind: 'save', id, body }); return pending.promise; },
    shot: async () => { calls.push({ kind: 'read' }); throw new Error('read offline'); },
  });
  await flush(); setup.render();
  setup.nodes('TextArea')[1].onChange({ target: { value: 'draft to save' } });
  const preparation = setup.nodes('ShotImageCandidates')[0].prepareShot();
  await flush();
  assert.deepEqual(calls.map(call => call.kind), ['save']);
  assert.equal(calls[0].body.script, 'draft to save');
  pending.resolve({ shot: { ...shot(), script: 'draft to save', row_version: '2' }, storyboard_version: '2' });
  assert.equal(await preparation, null);
  setup.render();
  assert.deepEqual(calls.map(call => call.kind), ['save', 'read']);
  assert.equal(setup.nodes('TextArea')[1].value, 'draft to save');
  assert.equal(setup.nodes('TextArea')[1].disabled, false);
  assert.ok(setup.nodes('Alert').some(alert => alert.message === 'read offline'));
});

test('failed saves keep dirty drafts through refresh and never read the shot', async context => {
  let reads = 0;
  const setup = mount(context, { update: async () => { throw new Error('save conflict'); }, shot: async () => { reads++; throw new Error('must not read'); } });
  await flush(); setup.render();
  setup.nodes('TextArea')[1].onChange({ target: { value: 'unsaved draft' } });
  assert.equal(await setup.nodes('ShotImageCandidates')[0].prepareShot(), null);
  setup.render();
  assert.equal(setup.barrier.hasUnsettled(), true);
  setup.nodes('ShotImageCandidates')[0].onChanged(); setup.render(); await flush(); setup.render();
  assert.equal(setup.nodes('TextArea')[1].value, 'unsaved draft');
  assert.equal(setup.nodes('TextArea')[1].disabled, false);
  assert.equal(reads, 0);
});

for (const invalidation of ['readOnly', 'aspect', 'episode', 'unmount']) {
  test(`preparation rejects a late read after ${invalidation} changes`, async context => {
    const pending = deferred();
    const setup = mount(context, { shot: async () => pending.promise });
    await flush(); setup.render();
    const preparation = setup.nodes('ShotImageCandidates')[0].prepareShot();
    await flush();
    if (invalidation === 'readOnly') setup.render({ readOnly: true });
    if (invalidation === 'aspect') setup.render({ value: { ...setup.props.value, aspect: '9:16' } });
    if (invalidation === 'episode') setup.render({ episodeId: '2' });
    if (invalidation === 'unmount') setup.unmount();
    pending.resolve({ shot: { ...shot(), script: 'stale remote' }, storyboard_version: '2' });
    assert.equal(await preparation, null);
    if (invalidation !== 'unmount') {
      await flush(); setup.render();
      assert.equal(setup.nodes('TextArea')[1].value, 'original');
    }
  });
}

test('older capability responses cannot replace a new model, disabled selection, or refreshed capability', async context => {
  const setup = mount(context);
  const pending = [];
  setup.configs.capabilities = async (id, signal) => { const response = deferred(); pending.push({ id, signal, ...response }); return response.promise; };
  await flush(); setup.render();
  const resolve = id => setup.nodes('EpisodeModelSelect').find(item => item.kind === 'image').onResolvedChange(id);
  resolve('7'); setup.render();
  resolve('8'); setup.render();
  assert.equal(pending[0].signal.aborted, true);
  pending[1].resolve({ known: true, reference_images: false }); await flush(); setup.render();
  pending[0].resolve({ known: true, reference_images: true }); await flush(); setup.render();
  assert.equal(setup.nodes('ShotImageCandidates')[0].modelId, '8');
  assert.equal(setup.nodes('ShotImageCandidates')[0].capabilities.reference_images, false);
  setup.window.dispatchEvent(new Event('configs-changed')); setup.render();
  assert.equal(setup.nodes('ShotImageCandidates')[0].modelId, '8');
  assert.equal(setup.nodes('ShotImageCandidates')[0].capabilities, null);
  resolve('8'); setup.render();
  pending[2].reject(new Error('offline')); await flush(); setup.render();
  assert.equal(setup.nodes('ShotImageCandidates')[0].capabilitiesLoading, false);
  assert.equal(setup.nodes('ShotImageCandidates')[0].capabilities, null);
  setup.nodes('ShotImageCandidates')[0].onRefreshCapabilities(); setup.render();
  assert.equal(setup.nodes('ShotImageCandidates')[0].modelId, '8');
  assert.equal(setup.nodes('ShotImageCandidates')[0].capabilitiesLoading, true);
  resolve(undefined); setup.render();
  pending[3].resolve({ known: true, reference_images: true }); await flush(); setup.render();
  assert.equal(setup.nodes('ShotImageCandidates')[0].modelId, undefined);
  assert.equal(setup.nodes('ShotImageCandidates')[0].capabilities, null);
  assert.equal(setup.nodes('ShotImageCandidates')[0].capabilitiesLoading, false);
  assert.equal(pending.length, 4);
});

test('navigation waits for release and stale release cannot unlock a new context operation', async context => {
  const setup = mount(context);
  await flush(); setup.render();
  const original = await setup.nodes('ShotImageCandidates')[0].prepareShot();
  assert.equal(setup.barrier.hasUnsettled(), true);
  assert.equal(await setup.barrier.flush(), false);
  setup.render({ episodeId: '2' }); await flush(); setup.render();
  const next = await setup.nodes('ShotImageCandidates')[0].prepareShot();
  original.release(); setup.render();
  assert.equal(setup.nodes('TextArea')[1].disabled, true);
  next.release(); setup.render();
  assert.equal(await setup.barrier.flush(), true);
  assert.equal(setup.nodes('TextArea')[1].disabled, false);
});
