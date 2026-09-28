async (page) => {
  const base = 'http://127.0.0.1:8080';
  const root = '/api/v1/projects/10/episodes/20/assembly';
  await page.context().unrouteAll({ behavior: 'ignoreErrors' });
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  page.removeAllListeners('pageerror'); page.removeAllListeners('dialog');
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('dialog', dialog => dialog.accept());
  let conflict = false;
  const media = '__ASSEMBLY_VIDEO_FIXTURE__';
  let state = { assembly: { id: '100', row_version: '1', aspect: '16:9', resolution: '720p', current_media_id: null }, source_hash: 'a'.repeat(64), context_hash: 'b'.repeat(64), changes: [], jobs: [], clips: [
    { id: '1', shot_id: '11', media_id: '101', position: 1, shot_position: 1, included: true, muted: false, trim_in_ms: 0, trim_out_ms: null, duration_ms: 3000, script: '林晚推开窗，清晨的光落在桌面。镜头由窗边缓缓移向她的侧脸。', poster: null, url: media, is_stale: false, issue: null, archived: false },
    { id: '2', shot_id: '12', media_id: '102', position: 2, shot_position: 2, included: true, muted: false, trim_in_ms: 0, trim_out_ms: null, duration_ms: 3000, script: '她拿起桌上的旧信，停顿片刻。特写信封上的名字，随后抬眼看向门口。', poster: null, url: media, is_stale: false, issue: null, archived: false },
    { id: '3', shot_id: '13', media_id: '103', position: 3, shot_position: 3, included: true, muted: true, trim_in_ms: 0, trim_out_ms: null, duration_ms: 3000, script: '门外传来脚步声。林晚把信收进衣袋，转身走向门口。镜头跟随，停在握住门把的瞬间。', poster: null, url: media, is_stale: false, issue: null, archived: false },
  ] };
  const check = (value, text) => { if (!value) throw new Error(text); };
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), path = req.url().split('?')[0].replace(base, ''), method = req.method();
    const body = req.postDataJSON(); requests.push({ path, method, body });
    const reply = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === root && method === 'GET') return reply(state);
    if (path === root && method === 'PATCH') {
      if (conflict) { conflict = false; return reply({ error: { code: 'assembly_version_conflict' } }, 409); }
      check(body.row_version === state.assembly.row_version, 'save used stale row version');
      state.clips = body.clips.map((edit, index) => ({ ...state.clips.find(c => c.id === edit.id), ...edit, position: index + 1 }));
      state.assembly = { ...state.assembly, row_version: String(Number(state.assembly.row_version) + 1), resolution: body.resolution };
      return reply(state);
    }
    if (path === `${root}/exports` && method === 'GET') return reply({ items: state.jobs, has_more: false });
    if (path === `${root}/exports` && method === 'POST') {
      check(body.row_version === state.assembly.row_version && body.source_hash === state.source_hash, 'export skipped save barrier');
      check(req.headers()['idempotency-key'], 'missing export idempotency');
      const job = { id: '901', kind: 'export', status: 'succeeded', stage: 'complete', progress: 100, cancel_requested: false, error: null, created_at: '2026-09-28T08:00:00', finished_at: '2026-09-28T08:01:00', context_hash: state.context_hash, media_id: '801', url: media, duration_ms: 6000, is_stale: false };
      state.jobs = [job]; return reply(job, 202);
    }
    if (path === `${root}/exports/901/apply`) { state.assembly.current_media_id = '801'; state.assembly.row_version = String(Number(state.assembly.row_version)+1); return reply(state.jobs[0]); }
    if (path === `${root}/sync`) { state.changes = []; state.assembly.row_version = String(Number(state.assembly.row_version)+1); return reply(state); }
    return reply({ error: { code: 'unexpected_fixture_request' } }, 501);
  });
  let harness = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><script type="module">import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;</script></head><body><div id="root"></div><script type="module">
  import React from '/node_modules/.vite/deps/react.js'; import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js'; import '/node_modules/.vite/deps/@ant-design_v5-patch-for-react-19.js'; import { ConfigProvider } from '/node_modules/.vite/deps/antd.js';
  import { AssemblyStage } from '/src/pages/projects/episode/AssemblyStage.tsx'; import { StageNav } from '/src/pages/projects/episode/StageNav.tsx'; import { studioTheme } from '/src/app/theme.ts'; import '/src/app/styles.css'; import '/src/app/studio.css'; import '/src/app/web.css'; import '/src/app/production.css';
  ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(ConfigProvider, { theme: studioTheme, button: { autoInsertSpace: false } }, React.createElement('main', { className: 'episode-page web-episode', style: { maxWidth: 1440, margin: '0 auto', padding: 24 } }, React.createElement('header', { className: 'episode-top' }, React.createElement('div', null, React.createElement('span', null, '验收演示项目 / 第 1 集'), React.createElement('h1', null, '清晨来信（合成测试素材）'))), React.createElement('div', { className: 'episode-layout' }, React.createElement('aside', { className: 'episode-sidebar' }, React.createElement('div', { className: 'episode-sidebar-inner' }, React.createElement('p', { className: 'episode-nav-title' }, '创作流程'), React.createElement(StageNav, { active: 'assembly', onSelect: () => {} }))), React.createElement('div', { className: 'episode-content' }, React.createElement(AssemblyStage, { projectId: '10', episodeId: '20', readOnly: false, registerBarrier: barrier => window.assemblyBarrier = barrier, onStoryboard: () => {} }))))));
  </script></body></html>`;
  const entry = await (await page.request.get(`${base}/src/main.tsx`)).text();
  for (const match of entry.matchAll(/"([^"\n]*\/node_modules\/\.vite\/deps\/[^"\n]+)"/g)) harness = harness.replaceAll(match[1].split('?')[0], match[1]);
  await page.route(`${base}/.runtime/assembly-acceptance.html`, route => route.fulfill({ contentType: 'text/html', body: harness }));
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.goto(`${base}/.runtime/assembly-acceptance.html`);
  await page.getByRole('heading', { name: '成片合成与导出', exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2);
  await page.evaluate(async () => { const video = document.querySelector('video'); video.currentTime = .2; await new Promise(resolve => video.addEventListener('seeked', resolve, { once: true })); });
  await page.waitForTimeout(300);
  await page.screenshot({ path: '../.impeccable/review/desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: '../.impeccable/review/mobile.png', fullPage: true });
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'mobile horizontal overflow');
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.getByRole('button', { name: '下移镜头 1', exact: true }).click();
  await page.getByRole('spinbutton', { name: '裁剪起点（秒）' }).fill('0.5');
  await page.getByRole('switch', { name: '片段静音' }).click();
  check(await page.evaluate(() => window.assemblyBarrier.flush()), 'save barrier failed');
  check(state.clips[1].id === '1' && state.clips[0].trim_in_ms === 500 && state.clips[0].muted, 'editing changes not persisted');
  await page.getByRole('button', { name: '导出成片', exact: true }).click();
  await page.getByRole('button', { name: '开始合成', exact: true }).click();
  await page.getByRole('button', { name: '导出记录', exact: true }).click();
  await page.getByRole('button', { name: '设为当前成片', exact: true }).click();
  await page.getByRole('button', { name: '确认采用', exact: true }).click();
  await page.getByRole('dialog', { name: '导出记录' }).getByRole('button', { name: '关闭弹窗' }).click();
  await page.getByRole('button', { name: '播放成片', exact: true }).waitFor();
  check(state.assembly.current_media_id === '801', 'adoption missing');
  conflict = true;
  await page.getByRole('switch', { name: '片段静音' }).click();
  check(!await page.evaluate(() => window.assemblyBarrier.flush()), 'conflict allowed navigation');
  check(await page.getByText('成片草稿已在其他窗口修改。当前编辑已保留，请下载草稿后重新载入。').isVisible(), 'conflict message missing');
  await page.getByRole('button', { name: '重新载入', exact: true }).click();
  await page.getByText('成片草稿已保存', { exact: true }).waitFor();
  check(errors.length === 0, errors.join('\n'));
  await page.evaluate(result => { window.assemblyAcceptance = result; }, { passed: true, requests: requests.length, errors });
  console.log(JSON.stringify({ passed: ['desktop', 'mobile', 'reorder', 'trim', 'mute', 'save barrier', 'export', 'history', 'adopt', 'conflict preserves edits'], requests: requests.length, errors }));
}
