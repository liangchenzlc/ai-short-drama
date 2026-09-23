async (page) => {
  await page.context().unrouteAll({ behavior: 'ignoreErrors' });
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  page.removeAllListeners('dialog');
  page.removeAllListeners('pageerror');
  await page.goto('about:blank');
  page.setDefaultTimeout(8000);
  const base = 'http://127.0.0.1:5173';
  const root = '/api/v1/projects/10/episodes/20';
  const requests = [];
  const unexpected = [];
  const errors = [];
  const results = [];
  const evidence = {};
  const state = { conflict: false, holdPatch: false, releasePatch: null, releaseCapabilities: null, sequence: 2000 };
  const shots = Array.from({ length: 80 }, (_, index) => ({
    id: String(101 + index), position: index + 1, row_version: '1', context_hash: `context-${101 + index}-1`,
    script: `验收分镜 ${index + 1}：角色走入街道。`, duration_ms: 3000, source_excerpt: '', asset_ids: ['201'],
    image_settings: { layout: 'single', aspect: 'inherit', resolution: '2K' }, image: null, deleted_at: null,
  }));
  const reference = { id: '201', kind: 'character', name: '验收角色', label: '', description: 'fixture', prompt: '', tags: [], scene_time: '',
    state: 'confirmed', row_version: '1', media_id: '801', image: null, reference_count: 80, created_at: null, updated_at: null,
    link_id: '901', position: 1 };
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const wait = async (predicate, message) => {
    const deadline = Date.now() + 10000;
    while (!await predicate()) { if (Date.now() > deadline) throw new Error(message); await page.waitForTimeout(50); }
  };
  const test = async (name, operation) => {
    try { await operation(); results.push({ name, status: 'passed' }); }
    catch (error) { results.push({ name, status: 'failed', error: String(error) }); }
  };
  const imageHistory = () => requests.filter(request => request.path === '/api/v1/media-library/items'
    || request.path === '/api/v1/ai/generations' && request.query.service_type === 'image');
  const submits = () => requests.filter(request => request.method === 'POST' && request.path === '/api/v1/ai/generations/image');
  const shotCalls = () => requests.filter(request => request.path === `${root}/shots/101`);
  page.on('pageerror', error => errors.push(String(error)));
  await page.context().route('**/*', async route => {
    const requestUrl = route.request().url();
    if (!requestUrl.startsWith(`${base}/`)) { unexpected.push(requestUrl); return route.abort(); }
    const path = requestUrl.slice(base.length).split('?')[0];
    if (!path.startsWith('/api/')) return route.continue();
    const query = Object.fromEntries((requestUrl.split('?')[1] || '').split('&').filter(Boolean).map(pair => pair.split('=').map(decodeURIComponent)));
    const request = { order: requests.length, method: route.request().method(), path, query,
      body: route.request().postDataJSON(), key: route.request().headers()['idempotency-key'] };
    requests.push(request);
    const reply = (data, status = 200) => {
      request.responseStatus = status;
      if (data.shot) request.responseShot = JSON.parse(JSON.stringify(data.shot));
      return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    };
    const paged = items => { const offset = Number(query.offset || 0); const limit = Number(query.limit || 100); return { items: items.slice(offset, offset + limit), total: items.length, offset, limit }; };
    if (path === '/api/v1/ai-model-configs') return reply(paged([{ id: query.service_type === 'image' ? '77' : '78',
      name: query.service_type === 'image' ? '默认验收图片模型' : '默认验收文字模型', provider: 'mock', service_type: query.service_type,
      model_key: `fixture-${query.service_type}`, base_url: '', enabled: 1, is_default: 1, is_deleted: 0, has_api_key: true, row_version: '1' }]));
    if (path === '/api/v1/ai-model-configs/77/capabilities') {
      await new Promise(resolve => { state.releaseCapabilities = resolve; });
      return reply({ known: true, reference_images: true, parameters: ['aspect', 'resolution', 'count'] });
    }
    if (path === `${root}/shots`) return reply({ ...paged(shots), episode_id: '20', storyboard_version: '1' });
    if (path === `${root}/assets`) return reply(paged([reference]));
    if (path === '/api/v1/assets/201') return reply(reference);
    if (path === '/api/v1/ai/generations' || path === '/api/v1/media-library/items') return reply(paged([]));
    if (path === `${root}/shots/101`) {
      if (request.method === 'PATCH') {
        if (state.holdPatch) await new Promise(resolve => { state.releasePatch = resolve; });
        if (state.conflict) return reply({ error: { code: 'shot_version_conflict', message: 'mock concurrent edit' } }, 409);
        if (request.body.row_version !== shots[0].row_version) return reply({ error: { code: 'shot_version_conflict' } }, 409);
        const version = String(Number(shots[0].row_version) + 1);
        shots[0] = { ...shots[0], ...request.body, row_version: version, context_hash: `context-101-${version}` };
      }
      return reply({ shot: shots[0], storyboard_version: '1' });
    }
    if (path === '/api/v1/ai/generations/image' && request.method === 'POST') {
      const source = request.body.source;
      if (source.shot_id !== '101' || source.row_version !== shots[0].row_version || source.context_hash !== shots[0].context_hash) {
        return reply({ error: { code: 'source_changed' } }, 409);
      }
      return reply({ generation_id: String(++state.sequence), service_type: 'image', status: 'queued' }, 202);
    }
    unexpected.push(request); return reply({ error: { code: 'UNEXPECTED_FIXTURE_REQUEST' } }, 501);
  });
  let harness = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>StoryboardStage mocked browser acceptance</title>
    <script type="module">import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;</script>
    </head><body><div id="root"></div><script type="module">
    import React from '/node_modules/.vite/deps/react.js';
    import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';
    import '/node_modules/.vite/deps/@ant-design_v5-patch-for-react-19.js';
    import { ConfigProvider } from '/node_modules/.vite/deps/antd.js';
    import { BrowserRouter } from '/node_modules/.vite/deps/react-router-dom.js';
    import { StoryboardStage } from '/src/pages/projects/episode/StoryboardStage.tsx';
    import { studioTheme } from '/src/app/theme.ts';
    import '/src/app/generations.css'; import '/src/app/styles.css'; import '/src/app/studio.css'; import '/src/app/web.css';
    sessionStorage.clear();
    const root = ReactDOM.createRoot(document.getElementById('root'));
    let value = { version: 2, novel: '', scriptDraft: '', scriptCandidates: [], approvedScript: null, aspect: '9:16', style: '',
      models: { script: '', analysis: '', assetImage: '', storyboardText: '', storyboardImage: '', video: '' },
      assets: [], shots: [], gridBatches: [], storyboardMode: 'frames', reviews: {}, legacyNotes: {} };
    const writingSession = { async flush() { return true; }, snapshot() { return {}; } };
    window.stageAcceptance = { barrier: null, value: () => value, unmount: () => root.unmount() };
    function render() { root.render(React.createElement(ConfigProvider, { theme: studioTheme, button: { autoInsertSpace: false } },
      React.createElement(BrowserRouter, null, React.createElement('main', { style: { maxWidth: 1120, margin: '24px auto', padding: 16 } },
        React.createElement(StoryboardStage, { value, readOnly: false, projectId: '10', episodeId: '20', contentVersion: '1', scriptId: '30', confirmed: true,
          writingSession, registerBarrier: barrier => { window.stageAcceptance.barrier = barrier; },
          onChange: next => { value = next; render(); }
        }))))); }
    render();
    </script></body></html>`;
  const entry = await (await page.request.get(`${base}/src/main.tsx`)).text();
  for (const match of entry.matchAll(/"([^"\n]*\/node_modules\/\.vite\/deps\/[^"\n]+)"/g)) {
    harness = harness.replaceAll(match[1].split('?')[0], match[1]);
  }
  await page.route(`${base}/.runtime/shot-image-stage-acceptance.html`, route => route.fulfill({ contentType: 'text/html', body: harness }));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${base}/.runtime/shot-image-stage-acceptance.html`);
  const firstShot = page.locator('.storyboard-item').first();
  const generate = firstShot.getByRole('button', { name: /^(?:loading\s*)?生成图片$/ });
  const script = firstShot.locator('textarea').first();
  try {
    await wait(async () => await page.locator('.storyboard-item').count() === 80, `80 shots did not render: ${JSON.stringify(errors)}`);
    await test('80 mounted shots keep image operations collapsed with zero per-shot requests', async () => {
      await wait(() => !!state.releaseCapabilities, 'default model did not request capabilities');
      await page.waitForTimeout(1100);
      check(await page.getByRole('button', { name: '展开图片操作', exact: true }).count() === 80, 'image operations are not all collapsed');
      check(imageHistory().length === 0, 'collapsed shots fetched image history');
      check(!requests.some(request => /^\/api\/v1\/assets\/\d+$/.test(request.path)), 'collapsed shots fetched individual references');
      check(shotCalls().length === 0 && submits().length === 0, 'collapsed mount read or mutated individual shots');
      evidence.collapsed = { shotCount: 80, imageHistoryCalls: imageHistory().length, shotDetailCalls: shotCalls().length };
    });
    await test('opening one shot resolves the default model and gates generation on capabilities', async () => {
      await firstShot.getByRole('button', { name: '展开图片操作', exact: true }).click();
      await wait(() => imageHistory().length >= 4, 'opened shot history did not load');
      check(await generate.isDisabled(), 'generation enabled before capabilities resolved');
      await firstShot.getByText('正在核对模型能力…', { exact: true }).waitFor();
      state.releaseCapabilities();
      await wait(() => generate.isEnabled(), 'default model capabilities did not enable generation');
      check(imageHistory().every(request => request.query.source_id === '101' && request.query.source_scene === 'shot_image'), 'other shots fetched history');
      check(await page.getByRole('button', { name: '展开图片操作', exact: true }).count() === 79, 'opening one shot expanded others');
      check(await page.evaluate(() => window.stageAcceptance.value().models.storyboardImage === ''), 'default resolution unexpectedly changed saved model selection');
      evidence.defaultModel = { resolvedId: '77', capabilitiesEndpoint: '/api/v1/ai-model-configs/77/capabilities', openedShot: '101' };
    });
    await test('edited shot generates only after PATCH completes and GET reads the newest version', async () => {
      state.holdPatch = true;
      const before = requests.length;
      await script.fill('edited draft: walk into the rain');
      await generate.click();
      await wait(() => !!state.releasePatch, 'save PATCH did not start');
      check(submits().length === 0, 'generation started before save completed');
      check(!requests.slice(before).some(request => request.path === `${root}/shots/101` && request.method === 'GET'), 'fresh GET started before PATCH completed');
      check(await script.isDisabled(), 'shot draft not locked during preparation');
      check(await page.evaluate(() => window.stageAcceptance.barrier.hasUnsettled()), 'navigation barrier ignored preparation');
      state.holdPatch = false; state.releasePatch();
      await wait(() => submits().length === 1 && submits()[0].responseStatus === 202, 'generation did not follow successful save');
      await wait(() => generate.isEnabled(), 'generation preparation lock did not release');
      const relevant = requests.slice(before).filter(request => request.path === `${root}/shots/101` || request.path === '/api/v1/ai/generations/image');
      check(relevant.map(request => request.method).join(',') === 'PATCH,GET,POST', 'wrong save/read/submit ordering');
      const [patch, read, submit] = relevant;
      check(patch.body.script === 'edited draft: walk into the rain' && patch.body.row_version === '1', 'PATCH did not preserve the edited draft');
      check(read.responseShot.row_version === '2' && submit.body.source.row_version === '2'
        && submit.body.source.context_hash === read.responseShot.context_hash, 'POST used stale source tokens');
      check(submit.body.config_id === '77' && submit.body.parameters.aspect === '9:16' && !!submit.key, 'resolved model, inherited aspect or idempotency missing');
      check(await script.inputValue() === 'edited draft: walk into the rain', 'successful save lost draft');
      check(!await page.evaluate(() => window.stageAcceptance.barrier.hasUnsettled()), 'save left navigation barrier unsettled');
      evidence.saveBeforeGenerate = relevant;
      await firstShot.screenshot({ path: 'output/playwright/shot-image-stage-generated.png' });
      evidence.viewportCaptures = [];
      for (const viewport of [{ name: 'desktop', width: 1440, height: 1000 }, { name: 'narrow', width: 390, height: 844 }]) {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await firstShot.locator('.shot-image-candidates').evaluate(element => element.scrollIntoView({ block: 'start' }));
        const path = `output/playwright/shot-image-stage-top-${viewport.name}.png`;
        await page.screenshot({ path });
        evidence.viewportCaptures.push({ ...viewport, path });
      }
      await page.setViewportSize({ width: 1440, height: 1000 });
    });
    await test('remote context change stops the first click and the second uses refreshed context', async () => {
      const before = submits().length;
      shots[0] = { ...shots[0], context_hash: 'remote-context-change-with-same-row-version' };
      await generate.click();
      await page.getByText('分镜上下文、图片或设置已变化，已刷新当前分镜，请核对后重新操作。', { exact: true }).waitFor();
      await wait(() => generate.isEnabled(), 'changed-context preparation did not unlock');
      check(submits().length === before, 'first click submitted stale context');
      await generate.click();
      await wait(() => submits().length === before + 1 && submits().at(-1).responseStatus === 202, 'second click did not submit refreshed context');
      await wait(() => generate.isEnabled(), 'second click remained locked');
      check(submits().at(-1).body.source.context_hash === shots[0].context_hash && submits().at(-1).body.source.row_version === '2', 'refreshed source tokens incorrect');
      evidence.remoteContext = { firstClickPostCount: 0, secondClick: submits().at(-1) };
    });
    await test('PATCH 409 preserves the unsaved draft and prevents detail read and generation', async () => {
      state.conflict = true;
      const before = requests.length;
      const submitted = submits().length;
      await script.fill('conflicting draft must remain visible');
      await generate.click();
      await page.getByText('分镜已被其他窗口修改。你的输入仍保留，请下载草稿或刷新后手动合并。', { exact: true }).waitFor();
      await wait(() => generate.isEnabled(), 'conflict did not release preparation lock');
      await page.waitForTimeout(1100);
      check(await script.inputValue() === 'conflicting draft must remain visible', '409 discarded the draft');
      check(submits().length === submitted, '409 caused generation');
      const after = requests.slice(before).filter(request => request.path === `${root}/shots/101`);
      check(after.length > 0 && after.every(request => request.method === 'PATCH' && request.responseStatus === 409), '409 preparation continued into GET');
      check(await page.evaluate(() => window.stageAcceptance.barrier.hasUnsettled()), 'dirty draft lost navigation protection');
      check(shots[0].script === 'edited draft: walk into the rain', 'mock server accepted conflicting changes');
      evidence.conflict = { draft: await script.inputValue(), postCount: submits().length - submitted, requests: after, unsettled: true };
      await page.getByText('分镜已被其他窗口修改。你的输入仍保留，请下载草稿或刷新后手动合并。', { exact: true }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: 'output/playwright/shot-image-stage-conflict.png' });
    });
    await test('stage integration stays within mocked APIs without runtime errors', async () => {
      check(unexpected.length === 0, JSON.stringify(unexpected));
      check(errors.length === 0, JSON.stringify(errors));
      check(imageHistory().every(request => request.query.source_id === '101'), 'unopened shots fetched image history later');
    });
  } catch (error) { results.push({ name: 'stage harness initialization', status: 'failed', error: String(error) }); }
  finally {
    state.releasePatch?.(); state.releaseCapabilities?.();
    await page.evaluate(() => window.stageAcceptance?.unmount());
  }
  return { scope: 'Real StoryboardStage, model selection, preparation, autosave, navigation barrier and ShotImageCandidates; mocked APIs; not full app or backend integration',
    passed: results.filter(result => result.status === 'passed').length, failed: results.filter(result => result.status === 'failed'),
    results, evidence, requests, unexpected, errors };
}
