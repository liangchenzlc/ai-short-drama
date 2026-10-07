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
  const controls = overrides.creationControls ?? {};
  const scrolls = [];
  const document = { getElementById: id => ({ scrollIntoView: () => scrolls.push(id), focus() {} }) };
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
    antd: { Alert: 'Alert', Button: 'Button', Dropdown: 'Dropdown', Checkbox: 'Checkbox', Input: { TextArea: 'TextArea' }, InputNumber: 'InputNumber', Segmented: 'Segmented', Select: 'Select', Spin: 'Spin' },
    '../../../api/http': { ApiError: class extends Error {}, errorMessage: cause => cause.message },
    '../../../api/modules/storyboard': { storyboardApi: () => api },
    '../../../api/modules/assets': { assetLibraries: { list: overrides.listAssets ?? (async () => ({ items: [], total: 0 })) } },
    '../../../api/modules/generations': { generations: { list: overrides.listTasks ?? (async () => ({ items: [], total: 0 })) } },
    '../../../api/modules/ai-model-configs': { aiModelConfigs: configs },
    '../../../features/ai-config/config-events': { AI_CONFIGS_CHANGED: 'configs-changed' },
    '../../../features/projects/EpisodeModelSelect': { EpisodeModelSelect: 'EpisodeModelSelect' },
    '../../../features/projects/EpisodeCreationWorkspace': { CreationSlot: 'CreationSlot', useEpisodeCreationControls: () => controls },
    '../../../features/projects/StoryboardShotCard': { StoryboardShotCard: 'StoryboardShotCard' },
    '../../../features/projects/storyboard-layout.css': {},
    '../../../features/projects/workflow-refinement.css': {},
    '../../../features/projects/workflow-contract': contract,
    '../../../features/projects/storyboard-session': session,
    '../../../features/projects/shot-image-workflow': workflow,
    '../../../features/generations/attempt': { attemptStorage: () => null, requestAttempt: async () => 'same-attempt', clearAttempt() {} },
    '../../../features/generations/BatchGeneration': { BatchLauncher: 'BatchLauncher', useBatchSelection: () => ({ enabled: false, ids: [], setIds() {}, toggle() {} }) },
    '../../../features/generations/presentation': { taskLabel: () => '' },
    '../../../features/generations/TaskDetail': { TaskDetail: 'TaskDetail' },
    '../../../features/projects/StoryboardResultPreview': { StoryboardResultPreview: 'StoryboardResultPreview' },
    '../../../components/ui/Dialog': { Dialog: 'Dialog' },
    '../../../components/ui/Icon': { Icon: 'Icon' },
    '../../../components/ui/confirm': { confirmAction: async () => true },
    '../../../components/ui/LazyLoadMore': { LazyLoadMore: 'LazyLoadMore' },
    '../../../features/projects/ShotAssetPicker': { ShotAssetPicker: 'ShotAssetPicker' },
    '../../../features/projects/ShotImageCandidates': { ShotImageCandidates: 'ShotImageCandidates' },
    '../../../features/projects/ShotVideoCandidates': { ShotVideoCandidates: 'ShotVideoCandidates' },
    '../../../features/projects/NativeVoicePanel': { NativeDialoguePanel: 'NativeDialoguePanel', NativeSoundMode: 'NativeSoundMode' },
  }, { window, document });
  let props = { value: { aspect: '16:9', models: { storyboardText: '', storyboardImage: '' } }, readOnly: false, projectId: '1', episodeId: '1', scriptId: null, confirmed: false, writingSession: {}, registerBarrier: next => { barrier = next; }, onChange: next => { props.value = next; } };
  const render = patch => {
    props = { ...props, ...patch }; cursor = 0; effects = []; layouts = [];
    tree = StoryboardStage(props);
    for (const callback of [...layouts, ...effects]) callback();
    // Select the first visible card before editing in the information panel.
    if (overrides.autoSelect !== false && !nodes('ShotImageCandidates').length) {
      const row = nodes('StoryboardShotCard')[0];
      if (row && !row.disabled) {
        row.onSelect(); cursor = 0; effects = []; layouts = [];
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
  return { api, configs, capabilityCalls, window, scrolls, render, nodes, unmount, get scriptInput() { return nodes('TextArea').find(input => /^分镜 \d+ 脚本$/.test(input['aria-label'] ?? '')); }, get barrier() { return barrier; }, get props() { return props; } };
}

test('restored off-page shot is editable without changing the list pagination offset', async context => {
  const rows = Array.from({ length: 80 }, (_, index) => ({ ...shot(String(101 + index)), position: index + 1 }));
  const offsets = []; const saves = [];
  const controls = { subject: { type: 'shot', id: '150', label: '' }, selectSubject: next => { controls.subject = next; } };
  const setup = mount(context, {
    autoSelect: false, creationControls: controls,
    shots: async (_signal, _archived, offset = 0) => { offsets.push(offset); return { ...page(rows.slice(offset, offset + 20)), total: 80, offset, limit: 20 }; },
    shot: async id => ({ shot: rows.find(row => row.id === id), storyboard_version: '1' }),
    update: async (id, body) => { saves.push({ id, body }); return { shot: { ...rows.find(row => row.id === id), ...body, row_version: '2' }, storyboard_version: '2' }; },
  });
  await flush(); setup.render(); await flush(); setup.render();
  assert.equal(setup.scriptInput['aria-label'], '分镜 50 脚本');
  assert.equal(setup.nodes('StoryboardShotCard').length, 20);
  setup.nodes('LazyLoadMore').find(node => node.hasMore).onLoad(); await flush(); setup.render();
  assert.deepEqual(offsets, [0, 20]);
  setup.scriptInput.onChange({ target: { value: 'off-page draft' } });
  assert.equal(await setup.barrier.flush(), true); setup.render();
  assert.equal(saves[0].id, '150'); assert.equal(saves[0].body.script, 'off-page draft');
  assert.equal(setup.scriptInput.value, 'off-page draft');
});

test('off-page autosave accepts its receipt before the initial list is ready', async context => {
  const list = deferred(); const saves = []; let listReads = 0;
  const visible = Array.from({ length: 20 }, (_, index) => ({ ...shot(String(101 + index)), position: index + 1 }));
  const restored = { ...shot('150'), position: 50 };
  let serverShot = restored;
  const controls = { subject: { type: 'shot', id: restored.id, label: '' }, selectSubject: next => { controls.subject = next; } };
  const setup = mount(context, {
    autoSelect: false, creationControls: controls,
    shots: async () => ++listReads === 1 ? list.promise : { ...page(visible), storyboard_version: String(saves.length + 1), total: 80, limit: 20 },
    shot: async () => ({ shot: serverShot, storyboard_version: String(saves.length + 1) }),
    update: async (id, body) => {
      saves.push({ id, body });
      const version = String(saves.length + 1);
      serverShot = { ...serverShot, ...body, row_version: version, context_hash: `saved-${version}` };
      return { shot: serverShot, storyboard_version: version };
    },
  });
  await flush(); setup.render(); await flush(); setup.render();
  assert.equal(setup.nodes('StoryboardShotCard').length, 0);
  assert.equal(setup.scriptInput.disabled, false);
  setup.scriptInput.onChange({ target: { value: 'saved while the list is pending' } });
  assert.equal(setup.barrier.hasUnsettled(), true);
  await new Promise(resolve => setTimeout(resolve, 1100)); await flush(); setup.render();
  assert.equal(saves.length, 1);
  assert.equal(saves[0].body.row_version, '1');
  assert.equal(setup.nodes('ShotImageCandidates')[0].shot.row_version, '2');
  assert.equal(setup.nodes('ShotImageCandidates')[0].shot.context_hash, 'saved-2');
  assert.equal(setup.scriptInput.value, 'saved while the list is pending');
  assert.equal(setup.barrier.hasUnsettled(), false);
  await flush(); setup.render();
  assert.equal(listReads, 2);
  setup.scriptInput.onChange({ target: { value: 'next edit uses the accepted version' } });
  assert.equal(await setup.barrier.flush(), true); setup.render();
  assert.equal(saves[1].body.row_version, '2');
  assert.equal(setup.nodes('ShotImageCandidates')[0].shot.row_version, '3');
  assert.equal(setup.nodes('ShotImageCandidates')[0].shot.context_hash, 'saved-3');
  assert.equal(setup.barrier.hasUnsettled(), false);
  list.resolve({ ...page(visible), total: 80, limit: 20 });
  await flush(); setup.render();
  assert.equal(setup.scriptInput.value, 'next edit uses the accepted version');
});

test('late restored detail cannot overwrite a newer selected shot', async context => {
  const responses = new Map([['150', deferred()], ['151', deferred()]]);
  const controls = { subject: { type: 'shot', id: '150', label: '' }, selectSubject: next => { controls.subject = next; } };
  const setup = mount(context, { autoSelect: false, creationControls: controls, shot: id => responses.get(id).promise });
  await flush(); setup.render();
  controls.subject = { type: 'shot', id: '151', label: '' }; setup.render(); setup.render();
  responses.get('151').resolve({ shot: { ...shot('151'), position: 51 }, storyboard_version: '1' });
  await flush(); setup.render();
  responses.get('150').resolve({ shot: { ...shot('150'), position: 50 }, storyboard_version: '1' });
  await flush(); setup.render();
  assert.equal(setup.scriptInput['aria-label'], '分镜 51 脚本');
  assert.equal(controls.subject.id, '151');
});

test('late adjacent pagination merges its list without replacing a newer selected shot', async context => {
  const nextPage = deferred();
  const rows = Array.from({ length: 80 }, (_, index) => ({ ...shot(String(101 + index)), position: index + 1 }));
  const controls = { subject: { type: 'shot', id: '120', label: '' }, selectSubject: next => { controls.subject = next; } };
  const setup = mount(context, { autoSelect: false, creationControls: controls,
    shots: async (_signal, _archived, offset = 0) => offset === 0 ? { ...page(rows.slice(0, 20)), total: 80 } : nextPage.promise,
    shot: async id => ({ shot: rows.find(row => row.id === id), storyboard_version: '1' }),
  });
  await flush(); setup.render(); await flush(); setup.render();
  setup.nodes('Button').find(node => node['aria-label'] === '下一个分镜').onClick();
  await flush(); setup.render();
  controls.subject = { type: 'shot', id: '150', label: '' };
  setup.render(); setup.render(); await flush(); setup.render();
  assert.equal(setup.scriptInput['aria-label'], '分镜 50 脚本');
  nextPage.resolve({ ...page(rows.slice(20, 40)), total: 80, offset: 20 });
  await flush(); setup.render();
  assert.equal(setup.nodes('StoryboardShotCard').length, 40);
  assert.equal(controls.subject.id, '150');
  assert.equal(setup.scriptInput['aria-label'], '分镜 50 脚本');
});

test('off-page dirty drafts survive refresh and joining a later loaded page', async context => {
  const rows = Array.from({ length: 80 }, (_, index) => ({ ...shot(String(101 + index)), position: index + 1 }));
  const controls = { subject: { type: 'shot', id: '150', label: '' }, selectSubject: next => { controls.subject = next; } };
  const setup = mount(context, { autoSelect: false, creationControls: controls,
    shots: async (_signal, _archived, offset = 0) => ({ ...page(rows.slice(offset, offset + 20)), total: 80, offset, limit: 20 }),
    shot: async id => ({ shot: rows.find(row => row.id === id), storyboard_version: '1' }),
  });
  await flush(); setup.render(); await flush(); setup.render();
  setup.scriptInput.onChange({ target: { value: 'retained off-page draft' } }); setup.render();
  setup.nodes('ShotImageCandidates')[0].onChanged(); setup.render(); await flush(); setup.render();
  for (let count = 0; count < 2; count++) { setup.nodes('LazyLoadMore').find(node => node.hasMore).onLoad(); await flush(); setup.render(); }
  assert.equal(setup.nodes('StoryboardShotCard').length, 60);
  assert.equal(setup.scriptInput.value, 'retained off-page draft');
  assert.equal(setup.barrier.hasUnsettled(), true);
});

test('Agent artifact refresh checks archived detail before clearing its loaded scope', async context => {
  const controls = { subject: { type: 'shot', id: '11', label: '分镜 01' }, selectSubject: next => { controls.subject = next; } };
  const setup = mount(context, { autoSelect: false, creationControls: controls, shot: async () => ({ shot: { ...shot(), deleted_at: '2026-10-04T00:00:00Z' }, storyboard_version: '2' }) });
  await flush(); setup.render();
  assert.equal(controls.subject.id, '11');
  setup.render({ refreshToken: 1 }); await flush(); setup.render();
  assert.equal(controls.subject, null);
});

test('only archived shot detail clears an unavailable Agent scope', async context => {
  const selections = [];
  const controls = { subject: { type: 'shot', id: '150', label: '' }, selectSubject: next => { selections.push(next); controls.subject = next; } };
  const setup = mount(context, { autoSelect: false, creationControls: controls, shot: async () => ({ shot: { ...shot('150'), deleted_at: '2026-10-04T00:00:00Z' }, storyboard_version: '2' }) });
  await flush(); setup.render(); await flush(); setup.render();
  assert.equal(selections.includes(null), true); assert.equal(controls.subject, null);
  assert.equal(setup.scriptInput, undefined);
});

test('detail read failure retains its scope and offers an explicit retry', async context => {
  let reads = 0;
  const controls = { subject: { type: 'shot', id: '150', label: '' } };
  const setup = mount(context, { autoSelect: false, creationControls: controls, shot: async () => { reads++; if (reads === 1) throw new Error('detail offline'); return { shot: { ...shot('150'), position: 50 }, storyboard_version: '1' }; } });
  await flush(); setup.render(); await flush(); setup.render();
  assert.equal(controls.subject.id, '150');
  const alert = setup.nodes('Alert').find(node => node.message.includes('detail offline'));
  assert.ok(alert); alert.action.props.onClick(); setup.render(); await flush(); setup.render();
  assert.equal(setup.scriptInput['aria-label'], '分镜 50 脚本');
});

test('creating a shot selects its returned ID before it is loaded into the list', async context => {
  const controls = { subject: null, selectSubject: next => { controls.subject = next; } };
  const rows = Array.from({ length: 20 }, (_, index) => ({ ...shot(String(101 + index)), position: index + 1 }));
  const setup = mount(context, { autoSelect: false, creationControls: controls, create: async () => ({ shot: { ...shot('181'), position: 81 }, storyboard_version: '2' }), shot: async () => ({ shot: { ...shot('181'), position: 81 }, storyboard_version: '2' }), shots: async () => ({ ...page(rows), total: 80 }) });
  await flush(); setup.render();
  setup.nodes('Button').find(node => node.children === '新增分镜').onClick(); await flush(); setup.render();
  assert.equal(controls.subject.id, '181');
  assert.equal(setup.scriptInput['aria-label'], '分镜 81 脚本');
});

for (const mode of ['replace', 'append']) test(`${mode} adoption updates its scope correctly`, async context => {
  const controls = { subject: { type: 'shot', id: '11', label: '分镜 01' }, selectSubject: next => { controls.subject = next; } };
  const setup = mount(context, {
    autoSelect: false, creationControls: controls,
    listTasks: async () => ({ items: [{ generation_id: '8001', status: 'succeeded', created_at: '2026-10-04T00:00:00Z' }], total: 1 }),
    apply: async () => ({ shot_ids: ['31'], storyboard_version: '2' }),
  });
  await flush(); setup.render({ writingSession: { getSnapshot: () => ({ contentVersion: '1' }) } });
  setup.nodes('Button').find(node => node.children === '历史记录').onClick(); setup.render(); await flush(); setup.render();
  setup.nodes('Button').find(node => node.children === '查看分镜').onClick(); setup.render();
  setup.nodes('StoryboardResultPreview')[0].onApply(mode); await flush(); setup.render();
  assert.equal(controls.subject?.id ?? null, mode === 'replace' ? null : '11');
});

test('asset failure does not hide loaded shots and retry restores only the asset dependency', async context => {
  let failAssets = true; let shotReads = 0;
  const setup = mount(context, { shots: async () => { shotReads++; return page([shot()]); }, listAssets: async () => { if (failAssets) throw new Error('assets offline'); return { items: [], total: 0 }; } });
  await flush(); setup.render();
  assert.equal(setup.nodes('StoryboardShotCard').length, 1);
  assert.equal(setup.nodes('ShotAssetPicker')[0].disabled, true);
  setup.scriptInput.onChange({ target: { value: 'keep this draft' } }); setup.render();
  failAssets = false;
  setup.nodes('Alert').find(node => node.message.includes('assets offline')).action.props.onClick();
  setup.render(); await flush(); setup.render();
  assert.equal(shotReads, 1); assert.equal(setup.scriptInput.value, 'keep this draft');
  assert.equal(setup.nodes('ShotAssetPicker')[0].disabled, false);
});

test('storyboard history distinguishes loading, failed initial read, and successful empty result', async context => {
  const response = deferred(); let failed = true;
  const setup = mount(context, { listTasks: async () => { await response.promise; if (failed) throw new Error('history offline'); return { items: [], total: 0 }; } });
  await flush(); setup.render();
  setup.nodes('Button').find(node => node.children === '历史记录').onClick(); setup.render();
  assert.ok(setup.nodes('p').some(node => node.role === 'status' && node.children === '正在加载分镜生成记录…'));
  assert.equal(setup.nodes('p').some(node => node.children === '暂无分镜生成记录，确认剧本后开始生成。'), false);
  response.resolve(); await flush(); setup.render();
  const alert = setup.nodes('Alert').find(node => node.message.includes('history offline'));
  assert.ok(alert);
  assert.equal(setup.nodes('p').some(node => node.children === '暂无分镜生成记录，确认剧本后开始生成。'), false);
  failed = false; alert.action.props.onClick(); setup.render(); await flush(); setup.render();
  assert.equal(setup.nodes('p').some(node => node.children === '暂无分镜生成记录，确认剧本后开始生成。'), true);
});

test('failed history refresh keeps previously loaded records', async context => {
  let fail = false;
  const setup = mount(context, { listTasks: async () => { if (fail) throw new Error('history refresh offline'); return { items: [{ generation_id: '8001', status: 'succeeded', created_at: '2026-10-04T00:00:00Z' }], total: 1 }; } });
  await flush(); setup.render();
  fail = true; setup.nodes('Button').find(node => node.children === '历史记录').onClick(); setup.render(); await flush(); setup.render();
  assert.equal(setup.nodes('Button').filter(node => node.children === '查看分镜').length, 1);
  assert.ok(setup.nodes('Alert').some(node => node.message.includes('history refresh offline')));
});

test('shot menu saves drafts before moving and archives the current scope explicitly', async context => {
  const calls = [];
  const controls = { subject: { type: 'shot', id: '11', label: '分镜 01' }, selectSubject: next => { controls.subject = next; } };
  const setup = mount(context, { autoSelect: false, creationControls: controls,
    update: async (id, body) => { calls.push({ kind: 'save', body }); return { shot: { ...shot(id), ...body, row_version: '2' }, storyboard_version: '2' }; },
    move: async (id, version, direction) => { calls.push({ kind: 'move', id, version, direction }); return { storyboard_version: '3' }; },
    remove: async (id, version) => calls.push({ kind: 'archive', id, version }),
  });
  await flush(); setup.render();
  setup.scriptInput.onChange({ target: { value: 'save before moving' } });
  const menu = setup.nodes('Dropdown')[0].menu;
  assert.equal(menu.items.find(item => item.key === 'up').disabled, true);
  await menu.onClick({ key: 'down' }); setup.render(); await flush(); setup.render();
  assert.deepEqual(calls.map(call => call.kind), ['save', 'move']);
  assert.equal(calls[1].version, '2'); assert.equal(calls[1].direction, 1);
  await setup.nodes('Dropdown')[0].menu.onClick({ key: 'archive' }); await flush(); setup.render();
  assert.equal(calls.at(-1).kind, 'archive'); assert.equal(controls.subject, null);
});

test('pending menu mutation disables editing and rejects an older change handler', async context => {
  const response = deferred(); let saves = 0;
  const setup = mount(context, {
    move: async () => response.promise,
    update: async (id, body) => { saves++; return { shot: { ...shot(id), ...body, row_version: '2' }, storyboard_version: '2' }; },
  });
  await flush(); setup.render();
  const previousInput = setup.scriptInput;
  const moving = setup.nodes('Dropdown')[0].menu.onClick({ key: 'down' });
  await flush(); setup.render();
  assert.equal(setup.scriptInput.disabled, true);
  assert.equal(setup.nodes('InputNumber').find(node => node['aria-label'] === '分镜 1 时长').disabled, true);
  previousInput.onChange({ target: { value: 'must not create a draft during the move' } });
  setup.render();
  assert.equal(setup.scriptInput.value, 'original');
  response.resolve({ storyboard_version: '2' }); await moving;
  setup.render(); await flush(); setup.render();
  assert.equal(setup.scriptInput.value, 'original');
  assert.equal(setup.barrier.hasUnsettled(), false);
  assert.equal(saves, 0);
});

test('failed save blocks menu mutation and preserves the editing draft', async context => {
  let moves = 0;
  const setup = mount(context, { update: async () => { throw new Error('save conflict'); }, move: async () => moves++ });
  await flush(); setup.render();
  setup.scriptInput.onChange({ target: { value: 'draft for recovery' } });
  await setup.nodes('Dropdown')[0].menu.onClick({ key: 'down' }); setup.render();
  assert.equal(moves, 0); assert.equal(setup.scriptInput.value, 'draft for recovery');
  assert.equal(setup.barrier.hasUnsettled(), true);
  setup.render({ readOnly: true });
  assert.equal(setup.nodes('Dropdown').length, 0);
});

test('one dialogue editor blocks changing the selected shot while its draft is unsettled', async context => {
  const controls = { subject: { type: 'shot', id: '11', label: '?? 01' }, selectSubject: next => { controls.subject = next; } };
  const setup = mount(context, { autoSelect: false, creationControls: controls });
  await flush(); setup.render();
  setup.nodes('button').find(node => node.id === 'storyboard-video-tab').onClick(); setup.render();
  const dialogue = setup.nodes('NativeDialoguePanel');
  assert.equal(dialogue.length, 1);
  dialogue[0].registerBarrier({ hasUnsettled: () => true, flush: async () => false });
  assert.equal(setup.barrier.hasUnsettled(), true);
  assert.equal(await setup.barrier.flush(), false);
  assert.equal(controls.subject.id, '11');
});

test('adoption survives focus and model refresh while generation preparation is invalidated', async context => {
  const pending = deferred();
  const setup = mount(context, { shot: async () => pending.promise });
  await flush(); setup.render();
  const preparation = setup.nodes('ShotImageCandidates')[0].prepareShot('adoption');
  await flush();
  setup.window.dispatchEvent(new Event('focus')); setup.render();
  pending.resolve({ shot: shot(), storyboard_version: '1' });
  const prepared = await preparation;
  assert.ok(prepared, 'model refresh must not silently cancel adopting an existing image');
  assert.equal(prepared.isCurrent(), true);
  setup.window.dispatchEvent(new Event('focus')); setup.render();
  assert.equal(prepared.isCurrent(), true, 'stale-image confirmation may restore focus again');
  prepared.release();
  const generation = setup.nodes('ShotImageCandidates')[0].prepareShot();
  setup.window.dispatchEvent(new Event('focus')); setup.render();
  assert.equal(await generation, null);
});

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
  assert.equal(setup.scriptInput.disabled, true);
  assert.equal(setup.nodes('ShotImageCandidates').length, 1);
  assert.equal(setup.nodes('ShotAssetPicker')[0].disabled, true);
  setup.scriptInput.onChange({ target: { value: 'blocked edit' } });
  for (const button of setup.nodes('Button').filter(button => ['上移', '下移', '归档'].includes(button.children))) {
    if (button.disabled) await button.onClick();
  }
  assert.equal(orders, 0); assert.equal(archives, 0); assert.equal(reads, 1);
  pending.resolve({ shot: shot(), storyboard_version: '1' });
  const prepared = await preparation;
  assert.equal(prepared.shot.script, 'original');
  setup.render(); assert.equal(setup.scriptInput.disabled, true);
  prepared.release(); prepared.release(); setup.render();
  assert.equal(setup.scriptInput.disabled, false);
  assert.equal(setup.nodes('ShotAssetPicker')[0].disabled, false);
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
  setup.scriptInput.onChange({ target: { value: 'old draft' } });
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
  setup.scriptInput.onChange({ target: { value: 'draft to save' } });
  const preparation = setup.nodes('ShotImageCandidates')[0].prepareShot();
  await flush();
  assert.deepEqual(calls.map(call => call.kind), ['save']);
  assert.equal(calls[0].body.script, 'draft to save');
  pending.resolve({ shot: { ...shot(), script: 'draft to save', row_version: '2' }, storyboard_version: '2' });
  assert.equal(await preparation, null);
  setup.render();
  assert.deepEqual(calls.map(call => call.kind), ['save', 'read']);
  assert.equal(setup.scriptInput.value, 'draft to save');
  assert.equal(setup.scriptInput.disabled, false);
  assert.equal(setup.nodes('ShotAssetPicker')[0].disabled, false);
  assert.ok(setup.nodes('Alert').some(alert => alert.message === 'read offline'));
});

test('failed saves keep dirty drafts through refresh and never read the shot', async context => {
  let reads = 0;
  const setup = mount(context, { update: async () => { throw new Error('save conflict'); }, shot: async () => { reads++; throw new Error('must not read'); } });
  await flush(); setup.render();
  setup.scriptInput.onChange({ target: { value: 'unsaved draft' } });
  assert.equal(await setup.nodes('ShotImageCandidates')[0].prepareShot(), null);
  setup.render();
  assert.equal(setup.barrier.hasUnsettled(), true);
  setup.nodes('ShotImageCandidates')[0].onChanged(); setup.render(); await flush(); setup.render();
  assert.equal(setup.scriptInput.value, 'unsaved draft');
  assert.equal(setup.scriptInput.disabled, false);
  assert.equal(setup.nodes('ShotAssetPicker')[0].disabled, false);
  assert.equal(reads, 0);
});

for (const purpose of ['generation', 'adoption']) for (const invalidation of ['readOnly', 'aspect', 'episode', 'unmount']) {
  test(`${purpose} preparation rejects a late read after ${invalidation} changes`, async context => {
    const pending = deferred();
    const setup = mount(context, { shot: async () => pending.promise });
    await flush(); setup.render();
    const preparation = setup.nodes('ShotImageCandidates')[0].prepareShot(purpose);
    await flush();
    if (invalidation === 'readOnly') setup.render({ readOnly: true });
    if (invalidation === 'aspect') setup.render({ value: { ...setup.props.value, aspect: '9:16' } });
    if (invalidation === 'episode') setup.render({ episodeId: '2' });
    if (invalidation === 'unmount') setup.unmount();
    pending.resolve({ shot: { ...shot(), script: 'stale remote' }, storyboard_version: '2' });
    assert.equal(await preparation, null);
    if (invalidation !== 'unmount') {
      await flush(); setup.render();
      assert.equal(setup.scriptInput.value, 'original');
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
  assert.equal(setup.scriptInput.disabled, true);
  next.release(); setup.render();
  assert.equal(await setup.barrier.flush(), true);
  assert.equal(setup.scriptInput.disabled, false);
  assert.equal(setup.nodes('ShotAssetPicker')[0].disabled, false);
});
