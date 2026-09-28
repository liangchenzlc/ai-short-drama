async (page) => {
  await page.context().unrouteAll({ behavior: 'ignoreErrors' });
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  page.removeAllListeners('dialog'); page.removeAllListeners('pageerror');
  await page.goto('about:blank');
  page.setDefaultTimeout(10000);
  const base = 'http://127.0.0.1:8080';
  const root = '/api/v1/projects/10/episodes/20';
  const requests = [], errors = [], unexpected = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('dialog', dialog => dialog.accept());
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#d6e6e2"/><path d="M100 300V100H260V300M380 300V100H540V300" fill="#aec7c0"/><circle cx="320" cy="130" r="28" fill="#52746b"/><path d="M280 270V170H360V270" fill="#52746b"/><path d="M365 200H430" stroke="#ab5555" stroke-width="12"/></svg>`;
  const frame = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  let shot = { id: '101', position: 1, row_version: '1', context_hash: 'b'.repeat(64), script: '林晚站在窗边，右手缓缓拿起红伞。镜头从中景缓推至中近景，右侧窗光保持柔和。最后停在握住伞柄的姿态。',
    duration_ms: 5000, source_excerpt: '', asset_ids: [], image_settings: { layout: 'five', aspect: 'inherit', resolution: '2K' },
    image: { media_id: '501', media_asset_id: '601', url: frame, layout: 'five', aspect: '16:9', resolution: '2K', is_stale: false },
    video_prompt: '', video_default_prompt: '以五宫格分镜图作为全能参考，林晚右手缓缓拿起红伞，目光落在伞柄上。镜头从中景缓慢推近至中近景。右侧柔和窗光保持稳定，人物握住伞柄后自然停留。',
    video_system_prompt: '分镜图是全能参考，宫格不直接作为首帧。保持人物身份、服装、道具归属和空间关系。动作按物理顺序发生，运镜保持连贯。成片不出现宫格边框或编号。',
    video_settings: { resolution: '720p' }, video_context_hash: 'a'.repeat(64), video: null, deleted_at: null };
  let tasks = [], candidates = [], videoUrl = '';
  let rejectSave = false;
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  await page.context().route('**/*', async route => {
    const request = route.request();
    if (!request.url().startsWith(base + '/')) return route.continue();
    const path = request.url().slice(base.length).split('?')[0];
    const query = Object.fromEntries((request.url().split('?')[1] || '').split('&').filter(Boolean).map(pair => pair.split('=').map(decodeURIComponent)));
    const url = { searchParams: { get: key => query[key] || null } };
    if (!path.startsWith('/api/')) return route.continue();
    const body = request.postDataJSON();
    requests.push({ path, method: request.method(), body });
    const reply = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    const paged = items => { const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 100); return { items: items.slice(offset, offset + limit), total: items.length, offset, limit }; };
    if (path === '/api/v1/ai-model-configs') { const kind = url.searchParams.get('service_type'); return reply(paged([{ id: kind === 'video' ? '79' : kind === 'image' ? '77' : '78', service_type: kind, name: `验收${kind === 'video' ? '视频' : '图片'}模型`, model_key: 'fixture', provider: 'fixture', base_url: '', enabled: 1, is_default: 1, is_deleted: 0, has_api_key: true, row_version: '1' }])); }
    if (path.endsWith('/capabilities')) return reply({ known: true, reference_images: true, first_frame: true, last_frame: false, parameters: ['duration_ms', 'resolution'], video_input: { first_frame: true, reference_images: true, duration_seconds: [5, 10, 15], resolutions: ['720p', '1080p'] } });
    if (path === `${root}/assets`) return reply(paged([]));
    if (path === `${root}/shots`) return reply({ ...paged([shot]), episode_id: '20', storyboard_version: shot.row_version });
    if (path.startsWith('/api/v1/generation-references/')) return reply({ items: [], row_version: shot.row_version });
    if (path === `${root}/shots/101`) {
      if (request.method() === 'PATCH') {
        if (rejectSave) return reply({ error: { code: 'shot_version_conflict' } }, 409);
        check(body.row_version === shot.row_version, 'save sent stale version');
        shot = { ...shot, ...body, row_version: String(Number(shot.row_version) + 1), video_context_hash: String(Number(shot.row_version) + 1).padStart(64, '0') };
        if (shot.video) shot.video = { ...shot.video, is_stale: true };
      }
      return reply({ shot, storyboard_version: shot.row_version });
    }
    if (path === '/api/v1/ai/generations/video') {
      check(body.source.row_version === shot.row_version && body.source.context_hash === shot.video_context_hash, 'generation used unsaved context');
      check(body.source.reference_media_id === '501' && !body.source.first_frame_media_id && !body.input, 'generation sent wrong reference image');
      check(body.parameters.duration_ms === 15000 && shot.video_settings.duration_ms === 15000, 'video duration not saved before submission');
      check(shot.duration_ms === 5000 && !shot.image.is_stale, 'video duration changed storyboard timing or image');
      tasks = [{ generation_id: '701', service_type: 'video', status: 'succeeded', source: body.source, can_cancel: false, can_retry: false, can_resume: false, created_at: '2026-09-28T04:00:00Z' }];
      candidates = [{ asset_id: '801', media_id: '901', generation_id: '701', record_id: '702', media_type: 'video', name: '分镜 01 候选视频', row_version: '1', url: videoUrl, duration_ms: 5000, source: body.source, created_at: '2026-09-28T04:00:00Z' }];
      return reply({ generation_id: '701', service_type: 'video', status: 'queued' }, 202);
    }
    if (path === '/api/v1/ai/generations') return reply(paged(url.searchParams.get('service_type') === 'video' ? tasks.filter(task => !url.searchParams.get('status') || task.status === url.searchParams.get('status')) : []));
    if (path === '/api/v1/media-library/items') return reply(paged(url.searchParams.get('media_type') === 'video' ? candidates : []));
    if (path === '/api/v1/media-library/items/801/apply') {
      check(body.expected_row_version === shot.row_version && body.expected_context_hash === shot.video_context_hash, 'adoption omitted current context');
      shot = { ...shot, row_version: String(Number(shot.row_version) + 1), video: { media_id: '901', media_asset_id: '801', url: videoUrl, duration_ms: 5000, resolution: '720p', first_frame_media_id: null, is_stale: false } };
      return reply({ media_id: '901', row_version: shot.row_version, context_hash: shot.video_context_hash, storyboard_version: shot.row_version, target: body.target });
    }
    unexpected.push(path); return reply({ error: { code: 'UNEXPECTED_FIXTURE_REQUEST' } }, 501);
  });
  let harness = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>分镜视频验收</title><script type="module">import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;</script></head><body><div id="root"></div><script type="module">
    import React from '/node_modules/.vite/deps/react.js'; import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js'; import '/node_modules/.vite/deps/@ant-design_v5-patch-for-react-19.js'; import { ConfigProvider } from '/node_modules/.vite/deps/antd.js'; import { BrowserRouter } from '/node_modules/.vite/deps/react-router-dom.js';
    import { StoryboardStage } from '/src/pages/projects/episode/StoryboardStage.tsx'; import { studioTheme } from '/src/app/theme.ts'; import '/src/app/generations.css'; import '/src/app/styles.css'; import '/src/app/studio.css'; import '/src/app/web.css'; import '/src/app/production.css';
    const root = ReactDOM.createRoot(document.getElementById('root')); const writingSession = { flush: async () => true, getSnapshot: () => ({ confirmed: true, scriptId: '30', contentVersion: '1' }) };
    function App() { const [value, setValue] = React.useState({ aspect: '16:9', style: '', models: { storyboardText: '', storyboardImage: '', video: '' } }); return React.createElement(ConfigProvider, { theme: studioTheme, button: { autoInsertSpace: false } }, React.createElement(BrowserRouter, null, React.createElement('main', { style: { maxWidth: 1120, margin: '24px auto', padding: 16 } }, React.createElement(StoryboardStage, { value, onChange: setValue, projectId: '10', episodeId: '20', contentVersion: '1', scriptId: '30', confirmed: true, readOnly: false, writingSession, registerBarrier: () => {} })))); } root.render(React.createElement(App));
    </script></body></html>`;
  const entry = await (await page.request.get(`${base}/src/main.tsx`)).text();
  for (const match of entry.matchAll(/"([^"\n]*\/node_modules\/\.vite\/deps\/[^"\n]+)"/g)) harness = harness.replaceAll(match[1].split('?')[0], match[1]);
  await page.route(`${base}/.runtime/shot-video-acceptance.html`, route => route.fulfill({ contentType: 'text/html', body: harness }));
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.goto(`${base}/.runtime/shot-video-acceptance.html`);
  videoUrl = await page.evaluate(async () => {
    const canvas = document.createElement('canvas'); canvas.width = 320; canvas.height = 180;
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d'); ctx.fillStyle = '#9bbfb4'; ctx.fillRect(0, 0, 320, 180);
    const stream = canvas.captureStream(0); const recorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp8' }); const chunks = [];
    recorder.ondataavailable = e => chunks.push(e.data);
    const stopped = new Promise(resolve => recorder.onstop = resolve);
    const started = new Promise(resolve => recorder.onstart = resolve); recorder.start(); await started;
    for (let i = 0; i < 8; i++) { ctx.fillStyle = i % 2 ? '#9bbfb4' : '#80a99d'; ctx.fillRect(0, 0, 320, 180); stream.getVideoTracks()[0].requestFrame(); await new Promise(resolve => setTimeout(resolve, 100)); }
    recorder.stop(); await stopped; stream.getTracks().forEach(track => track.stop()); canvas.remove();
    if (chunks.reduce((size, chunk) => size + chunk.size, 0) < 300) throw new Error('Recorded fixture has no video frames');
    return await new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.readAsDataURL(new Blob(chunks, { type: 'video/webm' })); });
  });
  await page.locator('.storyboard-summary').click();
  await page.getByText('分镜视频', { exact: true }).click();
  const generate = page.getByRole('button', { name: '生成视频', exact: true });
  await generate.waitFor();
  check(await page.getByText('查看系统提示词', { exact: true }).count() === 0, 'system prompt toggle still visible');
  await page.getByRole('spinbutton', { name: '视频时长', exact: true }).fill('15');
  await page.getByRole('spinbutton', { name: '视频时长', exact: true }).press('Tab');
  await page.getByRole('textbox', { name: '视频提示词', exact: true }).fill('林晚右手握住红伞后抬眼。镜头缓慢推近，保持柔和窗光，最后停在中近景。');
  await generate.click();
  await page.getByRole('dialog', { name: '分镜视频生成记录' }).waitFor();
  await page.getByRole('button', { name: '确认采用', exact: true }).waitFor();
  await page.locator('.shot-video-candidates video').evaluate(async video => { await video.play(); video.pause(); });
  check(requests.findIndex(item => item.method === 'PATCH') < requests.findIndex(item => item.path.endsWith('/generations/video')), 'generation did not wait for save');
  await page.screenshot({ path: '../output/playwright/shot-video-candidates.png', fullPage: true });
  await page.getByRole('button', { name: '确认采用', exact: true }).click();
  await page.getByLabel('当前采用分镜视频').waitFor();
  await page.getByRole('spinbutton', { name: '视频时长', exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../output/playwright/shot-video-desktop.png', fullPage: true });
  await page.reload();
  await page.locator('.storyboard-summary').click(); await page.getByText('分镜视频', { exact: true }).click();
  await page.getByLabel('当前采用分镜视频').waitFor();
  check(await page.getByRole('textbox', { name: '视频提示词', exact: true }).inputValue() === shot.video_prompt, 'prompt not restored');
  check(await page.getByRole('spinbutton', { name: '视频时长', exact: true }).inputValue() === '15', 'video duration not restored');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: '../output/playwright/shot-video-mobile.png', fullPage: true });
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'mobile overflow');
  rejectSave = true;
  await page.getByRole('textbox', { name: '视频提示词', exact: true }).fill('冲突后保留此提示词');
  const count = requests.filter(item => item.path.endsWith('/generations/video')).length;
  await generate.click();
  await page.getByText(/分镜已被其他窗口修改/).waitFor();
  check(requests.filter(item => item.path.endsWith('/generations/video')).length === count, 'conflicted draft submitted');
  check(await page.getByRole('textbox', { name: '视频提示词', exact: true }).inputValue() === '冲突后保留此提示词', 'conflicted draft lost');
  check(errors.length === 0, JSON.stringify(errors)); check(unexpected.length === 0, JSON.stringify(unexpected));
  console.log(JSON.stringify({ passed: ['save-before-submit', 'grid-reference-binding', 'video-playback', 'adoption', 'reload-recovery', 'mobile-no-overflow', 'conflict-preserves-draft'], requests: requests.length }));
}
