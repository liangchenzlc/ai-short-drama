async (page) => {
  await page.context().unrouteAll({ behavior: 'ignoreErrors' });
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  page.removeAllListeners('dialog');
  page.removeAllListeners('pageerror');
  await page.goto('about:blank');
  page.setDefaultTimeout(5000);
  const base = 'http://127.0.0.1:5173';
  const results = [];
  const requests = [];
  const dialogs = [];
  const unexpected = [];
  const errors = [];
  const evidence = {};
  const state = { submit: 'success', stale: false, active: false, missingSource: false };
  const source = { scene: 'shot_image', shot_id: '101', layout: 'four', context_mode: 'saved', row_version: 'original-version', context_hash: 'original-context' };
  const image = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450"><rect width="800" height="450" fill="#e8ded0"/><path d="M0 380 200 140 380 340 570 90 800 380" fill="#6d8277"/><text x="40" y="70" font-size="32" fill="#303b39">MOCK SHOT IMAGE</text></svg>')}`;
  const assets = Array.from({ length: 23 }, (_, index) => ({
    asset_id: String(500 - index), record_id: '600', generation_id: String(1000 - index), media_id: String(700 - index),
    media_type: 'image', name: `候选图片 ${index + 1}`, row_version: '1', url: image, source,
    width: 800, height: 450, created_at: new Date(Date.UTC(2026, 8, 23, 10, 0, 30 - index)).toISOString(),
  }));
  const tasks = assets.map(asset => ({ generation_id: asset.generation_id, service_type: 'image', status: 'succeeded', source,
    created_at: asset.created_at, config: { id: '77', name: '验收模拟图片模型', model_key: 'fixture-image', provider: 'mock' },
    can_cancel: false, can_retry: false, can_resume: false }));
  const reference = { id: '201', kind: 'character', name: '已确认角色', label: '', description: 'fixture', prompt: '', tags: [], scene_time: '',
    state: 'confirmed', row_version: '3', media_id: '801', image: { media_id: '801', url: image }, reference_count: 1, created_at: null, updated_at: null };
  const shot = { id: '101', row_version: 'target-version-7', context_hash: 'target-context-7', position: 0, script: '角色走入雨夜街道。',
    duration_ms: 3000, source_excerpt: '', asset_ids: ['201', '202'], deleted_at: null,
    image_settings: { layout: 'nine', aspect: '9:16', resolution: '4K' },
    image: { media_id: 'old-image', media_asset_id: null, url: image, layout: 'single', aspect: '16:9', resolution: '1K', is_stale: false } };
  const detail = id => ({ ...(tasks.find(task => task.generation_id === id) || tasks[0]), generation_id: id,
    source: state.missingSource ? null : source, input: { prompt: '原始生成提示词', reference_media_ids: ['801'] },
    parameters: { aspect: '16:9', resolution: '2K', count: 1 },
    source_snapshot: { shot_id: '101', reference_media_ids: ['801'] }, effective_prompt: 'MOCK effective prompt',
    result: { text: null, assets: assets.filter(asset => asset.generation_id === id), partial: false } });
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const wait = async (predicate, message) => {
    const deadline = Date.now() + 8000;
    while (!await predicate()) { if (Date.now() > deadline) throw new Error(message); await page.waitForTimeout(50); }
  };
  const test = async (name, operation) => {
    try { await operation(); results.push({ name, status: 'passed' }); }
    catch (error) { results.push({ name, status: 'failed', error: String(error) }); }
  };
  const button = name => page.getByRole('button', { name: new RegExp(`^(?:loading\\s*)?${name}$`) });
  const posts = () => requests.filter(request => request.method === 'POST' && request.path.endsWith('/generations/image'));
  const applies = () => requests.filter(request => request.path.endsWith('/apply'));
  const histories = () => requests.filter(request => request.path === '/api/v1/ai/generations' || request.path === '/api/v1/media-library/items');
  page.on('pageerror', error => errors.push(String(error)));
  page.on('dialog', async dialog => { dialogs.push(dialog.message()); await dialog.accept(); });
  await page.context().route('**/*', async route => {
    const requestUrl = route.request().url();
    const pathname = requestUrl.slice(base.length).split('?')[0];
    const query = Object.fromEntries((requestUrl.split('?')[1] || '').split('&').filter(Boolean).map(pair => pair.split('=').map(decodeURIComponent)));
    if (!requestUrl.startsWith(`${base}/`) && !requestUrl.startsWith('data:') && !requestUrl.startsWith('about:')) {
      unexpected.push(route.request().url()); return route.abort();
    }
    if (!pathname.startsWith('/api/')) return route.continue();
    const request = { method: route.request().method(), path: pathname, query,
      body: route.request().postDataJSON(), key: route.request().headers()['idempotency-key'] };
    requests.push(request);
    const reply = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    const paged = items => { const offset = Number(query.offset || 0); const limit = Number(query.limit || 20); return { items: items.slice(offset, offset + limit), total: items.length, offset, limit }; };
    if (request.path === '/api/v1/assets/201') return reply(reference);
    if (request.path === '/api/v1/assets/202') return reply({ ...reference, id: '202', name: '未确认场景', kind: 'scene', state: 'unconfirmed', media_id: null, image: null });
    if (request.path === '/api/v1/ai/generations/image' && request.method === 'POST') {
      const mode = state.submit;
      if (mode === 'delayed') await new Promise(resolve => { state.releaseReceipt = resolve; });
      else await page.waitForTimeout(300);
      if (mode === 'network') return route.abort('connectionfailed');
      return reply({ generation_id: '1100', service_type: 'image', status: 'queued' }, 202);
    }
    if (request.path === '/api/v1/ai/generations') {
      if (query.status) return reply(paged(state.active && query.status === 'running'
        ? [{ ...tasks[0], generation_id: '900', status: 'running' }] : []));
      return reply(paged(tasks));
    }
    if (request.path === '/api/v1/media-library/items') return reply(paged(assets));
    if (/\/ai\/generations\/\d+\/records$/.test(request.path)) return reply({ items: [{ record_id: '600', call_no: 1, status: 'succeeded', created_at: tasks[0].created_at }] });
    if (/\/ai\/generations\/\d+$/.test(request.path)) return reply(detail(request.path.split('/').at(-1)));
    if (/\/media-library\/items\/\d+\/apply$/.test(request.path)) {
      if (state.stale && !request.body.acknowledge_stale_source) return reply({ error: { code: 'stale_generation_source', message: 'fixture stale source' } }, 409);
      return reply({ applied: true });
    }
    unexpected.push(request); return reply({ error: { code: 'UNEXPECTED_FIXTURE_REQUEST' } }, 501);
  });
  let harness = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Shot image mocked browser acceptance</title>
    <script type="module">import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;</script>
    <script type="module" src="/@vite/client"></script></head><body><div id="root"></div><script type="module">
    import React from '/node_modules/.vite/deps/react.js';
    import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';
    import '/node_modules/.vite/deps/@ant-design_v5-patch-for-react-19.js';
    import { ConfigProvider } from '/node_modules/.vite/deps/antd.js';
    import { BrowserRouter } from '/node_modules/.vite/deps/react-router-dom.js';
    import { ShotImageCandidates } from '/src/features/projects/ShotImageCandidates.tsx';
    import { studioTheme } from '/src/app/theme.ts';
    import '/src/app/generations.css'; import '/src/app/styles.css'; import '/src/app/studio.css'; import '/src/app/web.css';
    const shot = ${JSON.stringify(shot)};
    const root = ReactDOM.createRoot(document.getElementById('root'));
    let revision = 0; let mode = 'supported'; let mounted = true;
    window.acceptance = { prepared: 0, released: 0, changed: 0,
      mode(value) { mode = value; render(); },
      unmount() { mounted = false; render(); },
      remount() { mounted = true; revision++; render(); },
      shot };
    function render() { root.render(React.createElement(ConfigProvider, { theme: studioTheme, button: { autoInsertSpace: false } },
      React.createElement(BrowserRouter, null, React.createElement('main', { style: { maxWidth: 1080, margin: '24px auto', padding: 16 } },
        React.createElement('h1', null, '分镜图片 · 浏览器模拟验收'),
        mounted && React.createElement(ShotImageCandidates, { key: revision, shot, disabled: false, modelId: mode === 'missing' ? undefined : '77',
          capabilities: mode === 'unknown' ? null : { known: true, reference_images: mode !== 'unsupported' }, capabilitiesLoading: false,
          onRefreshCapabilities() {}, onChanged() { window.acceptance.changed++; }, episodeAspect: '16:9',
          async prepareShot() { window.acceptance.prepared++; return { shot: structuredClone(shot), isCurrent: () => true, release() { window.acceptance.released++; } }; }
        }))))); }
    render();
    </script></body></html>`;
  const entry = await (await page.request.get(`${base}/src/main.tsx`)).text();
  for (const match of entry.matchAll(/"([^"\n]*\/node_modules\/\.vite\/deps\/[^"\n]+)"/g)) {
    harness = harness.replaceAll(match[1].split('?')[0], match[1]);
  }
  await page.route(`${base}/.runtime/shot-image-acceptance.html`, route => route.fulfill({ contentType: 'text/html', body: harness }));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${base}/.runtime/shot-image-acceptance.html`);
  await page.waitForFunction(() => !!window.acceptance).catch(error => { throw new Error(String(error) + JSON.stringify(errors)); });
  await page.evaluate(() => sessionStorage.clear());
  await test('collapsed mount issues no history or reference requests', async () => {
    await button('展开图片操作').waitFor(); await page.waitForTimeout(500);
    check(requests.length === 0, JSON.stringify(requests));
  });
  await button('展开图片操作').click();
  await wait(() => button('生成图片').isEnabled(), 'references did not finish loading');
  await test('expanded references and model capability blocks', async () => {
    await page.getByText('已关联 2 个素材 · 1 张确认参考图', { exact: true }).waitFor();
    await page.getByText('未确认场景', { exact: true }).waitFor();
    for (const mode of ['unsupported', 'unknown', 'missing']) {
      await page.evaluate(value => window.acceptance.mode(value), mode);
      await wait(() => button('生成图片').isDisabled(), `generation enabled for ${mode}`);
      const message = mode === 'unsupported' ? '不会自动丢弃关联图片' : mode === 'unknown' ? '模型能力尚未确认' : '请先选择一个已启用';
      await page.getByText(message, { exact: false }).waitFor();
    }
    check(posts().length === 0, 'blocked model submitted generation');
    await page.evaluate(() => window.acceptance.mode('supported'));
    await wait(() => button('生成图片').isEnabled(), 'supported model remained disabled');
  });
  await test('double-click creates one request with saved source tokens', async () => {
    await page.getByPlaceholder('例如：逆光、雨夜街道，突出人物眼神').fill('验收补充：逆光');
    await page.getByRole('spinbutton', { name: '生成张数', exact: true }).fill('2');
    await button('生成图片').evaluate(element => { element.click(); element.click(); });
    await page.getByText('图片任务 1100 已提交。', { exact: true }).waitFor();
    check(posts().length === 1, `submitted ${posts().length} requests`);
    const request = posts()[0];
    check(!!request.key && request.body.source.context_mode === 'saved', 'missing idempotency key or saved context');
    check(request.body.source.row_version === shot.row_version && request.body.source.context_hash === shot.context_hash, 'wrong source tokens');
    check(request.body.parameters.count === 2 && request.body.input.prompt === '验收补充：逆光', 'wrong user input');
    check(request.body.input.reference_media_ids.length === 0, 'saved references must resolve on backend');
  });
  await test('candidate preview and adoption use original parameters', async () => {
    await button('预览候选图片 1').click();
    const preview = page.getByRole('dialog', { name: '分镜图片预览', exact: true });
    await preview.getByText('原生成参数：four · 16:9 · 2K', { exact: true }).waitFor();
    await preview.screenshot({ path: 'output/playwright/shot-image-preview-desktop.png' });
    const before = applies().length;
    await preview.getByRole('button', { name: '确认采用', exact: true }).click();
    await wait(() => applies().length === before + 1, 'adoption request not sent');
    await page.getByText('图片已采用。', { exact: true }).waitFor();
    const body = applies().at(-1).body;
    check(!('parameters' in body), 'adoption overwrote original parameters');
    check(body.expected_media_id === 'old-image' && body.expected_row_version === shot.row_version && body.expected_context_hash === shot.context_hash, 'wrong target tokens');
    check(dialogs.at(-1).includes('four · 16:9 · 2K') && !dialogs.at(-1).includes('4K'), 'confirmation displayed current settings');
    check(await page.evaluate(() => window.acceptance.shot.image_settings.layout === 'nine'), 'next-generation settings changed');
    await preview.getByRole('button', { name: '关闭', exact: true }).click();
  });
  await test('stale source confirmation retries with identical target tokens', async () => {
    state.stale = true;
    const before = applies().length;
    await page.locator('.image-candidate').first().getByRole('button', { name: '确认采用', exact: true }).click();
    await wait(() => applies().length === before + 2, 'stale confirmation retry missing');
    const [initial, retry] = applies().slice(-2).map(request => request.body);
    check(JSON.stringify({ ...initial, acknowledge_stale_source: true }) === JSON.stringify(retry), 'stale retry changed target tokens');
    check(dialogs.at(-1).includes('较早的创作上下文'), 'stale confirmation was not shown');
    check(await page.evaluate(() => window.acceptance.prepared === window.acceptance.released), 'preparation lock leaked');
    state.stale = false;
  });
  await test('missing candidate source prevents adoption', async () => {
    state.missingSource = true;
    const before = applies().length;
    await page.locator('.image-candidate').first().getByRole('button', { name: '确认采用', exact: true }).click();
    await page.getByText('候选来源或生成参数不完整，无法直接采用；请查看任务详情。', { exact: true }).waitFor();
    check(applies().length === before, 'invalid source adopted');
    state.missingSource = false;
  });
  await test('task and candidate pagination retain previous pages', async () => {
    await button('加载更多候选').click();
    await wait(async () => await page.locator('.image-candidate').count() === 23, 'candidate pagination did not append');
    await button('加载更多记录').click();
    await page.getByText('生成记录（已加载 23）', { exact: true }).waitFor();
    check(histories().some(request => request.path.endsWith('/items') && request.query.offset === '20'), 'missing candidate offset');
    check(histories().some(request => request.path.endsWith('/generations') && request.query.offset === '20'), 'missing task offset');
    await button('刷新状态与候选').click();
    await wait(() => button('刷新状态与候选').getAttribute('class').then(value => !value.includes('loading')), 'refresh never completed');
    check(await page.locator('.image-candidate').count() === 23, 'refresh dropped candidate tail');
  });
  await test('task detail exposes original input and invocation records', async () => {
    await page.getByText('生成记录（已加载 23）', { exact: true }).click();
    await button('查看任务').first().click();
    const drawer = page.getByRole('dialog', { name: '任务详情', exact: true });
    await drawer.getByText('查看提交内容', { exact: true }).click();
    await drawer.getByText('原始生成提示词', { exact: false }).waitFor();
    await drawer.getByText('调用记录（1）', { exact: true }).click();
    await drawer.getByText('第 1 次调用', { exact: true }).waitFor();
    await drawer.getByRole('button', { name: 'Close', exact: true }).click();
  });
  await test('desktop and narrow screenshots and horizontal fit', async () => {
    const summary = page.locator('.writing-task-history');
    if (await summary.getAttribute('open') !== null) await summary.locator('summary').click();
    await page.screenshot({ path: 'output/playwright/shot-image-desktop.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: 'output/playwright/shot-image-narrow.png', fullPage: true });
    const overflow = await page.evaluate(() => ({ viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth,
      elements: [...document.querySelectorAll('main *')].map(element => ({ tag: element.tagName, className: element.className,
        right: Math.round(element.getBoundingClientRect().right), width: Math.round(element.getBoundingClientRect().width) })).filter(element => element.right > innerWidth + 1).slice(0, 8) }));
    await button('预览候选图片 1').click();
    const preview = page.getByRole('dialog', { name: '分镜图片预览', exact: true });
    await preview.getByText('原生成参数：four · 16:9 · 2K', { exact: true }).waitFor();
    await page.screenshot({ path: 'output/playwright/shot-image-preview-narrow.png' });
    await preview.getByRole('button', { name: '关闭', exact: true }).click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    check(overflow.scrollWidth <= overflow.viewport + 1, `narrow page horizontally overflows: ${JSON.stringify(overflow)}`);
  });
  await test('collapse stops polling even with active task', async () => {
    state.active = true;
    await button('刷新状态与候选').click();
    await page.getByText('生成记录（已加载 24）', { exact: true }).waitFor();
    await button('收起图片操作').click();
    const before = histories().length;
    await page.waitForTimeout(3500);
    check(histories().length === before, 'collapsed component kept polling');
    check(await page.locator('.image-candidate').count() === 0, 'candidates remain expanded');
    state.active = false;
  });
  await test('uncertain network blocks automatic regeneration across remount and reload', async () => {
    await button('展开图片操作').click();
    await wait(() => button('生成图片').isEnabled(), 'generate disabled before network case');
    state.submit = 'network';
    const before = posts().length;
    await button('生成图片').click();
    await page.getByText('上次生成请求的受理结果尚未确认，请先查看记录，避免重复计费。', { exact: true }).waitFor();
    check(await button('生成图片').isDisabled(), 'uncertain submit remained enabled');
    await page.evaluate(() => window.acceptance.unmount());
    await page.locator('.shot-image-candidates').waitFor({ state: 'detached' });
    await page.evaluate(() => window.acceptance.remount());
    await button('展开图片操作').click();
    await page.getByRole('button', { name: '已核对记录', exact: true }).waitFor();
    check(await button('生成图片').isDisabled(), 'remount lost uncertain protection');
    await page.reload();
    await button('展开图片操作').click();
    await button('已核对记录').waitFor();
    check(await button('生成图片').isDisabled(), 'reload lost uncertain protection');
    await page.waitForTimeout(500);
    check(posts().length === before + 1, 'automatic regeneration occurred');
    state.submit = 'success';
    await button('已核对记录').click();
    await wait(() => button('生成图片').isEnabled(), 'explicit acknowledgement failed');
    check(posts().length === before + 1, 'acknowledgement itself submitted generation');
  });
  await test('late A receipt cannot clear newer uncertain B ownership across remount', async () => {
    state.submit = 'delayed';
    const before = posts().length;
    await button('生成图片').click();
    await wait(() => !!state.releaseReceipt, 'A receipt was not held');
    const ownerA = await page.evaluate(() => sessionStorage.getItem('shot-image-uncertain:101'));
    check(!!ownerA && ownerA !== '1', 'A marker is not opaque ownership');
    await page.evaluate(() => window.acceptance.unmount());
    await page.locator('.shot-image-candidates').waitFor({ state: 'detached' });
    await page.evaluate(() => window.acceptance.remount());
    await button('展开图片操作').click();
    await button('已核对记录').click();
    await wait(() => button('生成图片').isEnabled(), 'acknowledgement did not unlock B');
    await page.getByPlaceholder('例如：逆光、雨夜街道，突出人物眼神').fill('new attempt B after explicit acknowledgement');
    state.submit = 'network';
    await button('生成图片').click();
    await button('已核对记录').waitFor();
    await wait(async () => await page.evaluate(() => window.acceptance.released === window.acceptance.prepared - 1), 'B did not finish while A stayed pending');
    const markerB = await page.evaluate(() => ({ owner: sessionStorage.getItem('shot-image-uncertain:101'), attempt: sessionStorage.getItem('generation-attempt:shot-image:101') }));
    check(!!markerB.owner && markerB.owner !== ownerA && !!markerB.attempt, 'B did not get distinct ownership');
    state.releaseReceipt();
    await wait(async () => await page.evaluate(() => window.acceptance.released === window.acceptance.prepared), 'late A response was not processed');
    const afterA = await page.evaluate(() => ({ owner: sessionStorage.getItem('shot-image-uncertain:101'), attempt: sessionStorage.getItem('generation-attempt:shot-image:101') }));
    check(JSON.stringify(afterA) === JSON.stringify(markerB), 'late A cleared B ownership or idempotency record');
    await page.evaluate(() => window.acceptance.unmount());
    await page.locator('.shot-image-candidates').waitFor({ state: 'detached' });
    await page.evaluate(() => window.acceptance.remount());
    await button('展开图片操作').click();
    await button('已核对记录').waitFor();
    check(await button('生成图片').isDisabled(), 'B protection was lost across remount after late A');
    check(posts().length === before + 2, 'late A caused automatic regeneration');
    evidence.lateReceipt = { ownerA, ownerB: markerB.owner, ownerAfterA: afterA.owner, idempotencyPreserved: markerB.attempt === afterA.attempt, submissions: 2, blockedAfterRemount: true };
    state.submit = 'success';
    await button('已核对记录').click();
    await wait(() => button('生成图片').isEnabled(), 'final explicit acknowledgement did not unlock');
  });
  await test('all APIs mocked and no browser runtime errors', async () => {
    check(unexpected.length === 0, JSON.stringify(unexpected));
    check(errors.length === 0, JSON.stringify(errors));
  });
  await button('收起图片操作').click();
  const report = { scope: 'Real ShotImageCandidates and TaskDetail in isolated browser harness; all API responses mocked; no backend, DB, or model integration',
    results, evidence, requests, dialogs, unexpected, errors };
  return { passed: results.filter(result => result.status === 'passed').length, failed: results.filter(result => result.status === 'failed'), ...report };
}
