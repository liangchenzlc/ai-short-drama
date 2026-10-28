import { test, expect, type Page } from '@playwright/test';

const root = '/projects/10/assembly';
const soundRoot = '/projects/10/sound';
const hash = 'a'.repeat(64);
async function feedbackTrigger(page: Page) {
  const dialog = page.locator('dialog[open] .studio-feedback-indicator').last();
  if (await dialog.isVisible()) return dialog;
  const drawer = page.locator('.sound-drawer .studio-feedback-indicator').last();
  if (await drawer.isVisible()) return drawer;
  const rail = page.locator('.sound-rail .studio-feedback-indicator').last();
  if (await rail.isVisible()) return rail;
  return page.getByRole('button', { name: /^保存状态：/ });
}
async function openFeedback(page: Page) {
  const trigger = await feedbackTrigger(page);
  await expect(trigger).toBeVisible();
  if (await trigger.getAttribute('aria-expanded') !== 'true') await trigger.click();
  const details = page.locator('.studio-feedback-details:visible').last();
  await expect(details).toBeVisible();
  return details;
}
async function closeFeedback(page: Page) {
  const trigger = await feedbackTrigger(page);
  if (await trigger.isVisible() && await trigger.getAttribute('aria-expanded') === 'true') await trigger.click();
}
const source = {
  id: '1001', shot_id: '101', media_id: '9101', position: 1, shot_position: 1,
  included: true, muted: false, trim_in_ms: 0, trim_out_ms: 3000, duration_ms: 3000,
  script: '合成测试画面', url: '/.runtime/timeline-fixture.mp4', poster: '/.runtime/timeline-poster.jpg',
  filmstrip: { url: '/.runtime/timeline-filmstrip.jpg', count: 3, interval_ms: 1000 },
  is_stale: false, archived: false, issue: null,
};
function job(id: string, kind = 'export', status = 'running') {
  return { id, kind, status, stage: status === 'succeeded' ? 'complete' : 'rendering', progress: status === 'succeeded' ? 100 : 30,
    cancel_requested: false, error: null as null | { message: string }, created_at: '2026-10-01T00:00:00Z', finished_at: null,
    context_hash: hash, url: status === 'succeeded' ? source.url : null as string | null,
    media_id: status === 'succeeded' ? '9901' : null as string | null, duration_ms: 3000, is_stale: false,
    timeline: [{ clip_id: source.id, shot_id: source.shot_id, media_id: source.media_id, trim_in_ms: 0, trim_out_ms: 3000, muted: false }],
  };
}
async function fixture(page: Page, soundEnabled = false) {
  const state = { assembly: { id: '111', row_version: '1', aspect: '16:9', resolution: '720p', current_media_id: null as string | null },
    source_hash: hash, context_hash: hash, clips: [{ ...source }], sources: [{ ...source }], changes: [], jobs: [] as ReturnType<typeof job>[] };
  const sound = { mode: 'native', row_version: '1', timeline_hash: hash, duration_ms: 3000, needs_review: false, stale_lines: [],
    document: { dialogue: [], subtitles: [] as { start_ms: number; end_ms: number; text: string }[], native_ducking: [], music: null, original_volume: 1, dialogue_volume: 1, burn_subtitles: true, font_size: 24 },
    media: {}, uploads: [], voice_defaults: { row_version: '1', voices: {} } };
  const controls = { online: true, previewFailures: 0, previewFinish: true, saveFails: false, capabilityFailures: 0, abortAcceptedRender: false, rejectRender: false };
  const receipts = new Map<string, ReturnType<typeof job>>();
  const calls: { path: string; method: string; body: any; key: string | undefined }[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname.slice('/api/v1'.length).replace(/\/assembly\/editor$/, '/assembly'), method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    const key = request.headers()['idempotency-key'];
    calls.push({ path, method, body, key });
    const reply = (json: any, status = 200) => route.fulfill({ json, status });
    if (path === `${soundRoot}/capabilities`) return controls.capabilityFailures-- > 0 ? reply({ error: { code: 'unavailable', message: '声音读取失败' } }, 503) : reply({ enabled: soundEnabled });
    if (path === soundRoot) {
      if (method === 'PUT') { sound.document = body.document; sound.row_version = String(BigInt(sound.row_version) + 1n); state.assembly.row_version = String(Number(state.assembly.row_version) + 1); }
      return reply(sound);
    }
    if (path === root) {
      if (!controls.online) return reply({ error: { code: 'unavailable', message: '工作台连接中断' } }, 503);
      if (method === 'PATCH') {
        if (controls.saveFails) return reply({ error: { code: 'conflict', message: '测试保存冲突，草稿保留' } }, 409);
        state.clips = body.clips.map((clip: any) => ({ ...(state.clips.find(c => c.id === clip.id) ?? source), ...clip }));
        state.assembly.row_version = String(Number(state.assembly.row_version) + 1);
        state.assembly.resolution = body.resolution;
      }
      return reply(state);
    }
    if (path === `${root}/exports` && method === 'GET') return reply({ items: state.jobs.filter(j => j.kind === 'export'), has_more: false });
    if ((path === `${root}/exports` || path === `${root}/previews` || path === `${root}/exports/301/retry`) && method === 'POST') {
      if (key && receipts.has(key)) return reply(receipts.get(key), 202);
      if (controls.rejectRender) return reply({ error: { code: 'assembly_not_ready', message: '请先核对裁剪范围' } }, 422);
      const created = job(path.endsWith('previews') ? '201' : path.endsWith('retry') ? '401' : '301', path.endsWith('previews') ? 'preview' : 'export');
      state.jobs.unshift(created); if (key) receipts.set(key, created);
      if (controls.abortAcceptedRender) { controls.abortAcceptedRender = false; Object.assign(created, job(created.id, created.kind, 'succeeded')); return route.abort('failed'); }
      return reply(created, 202);
    }
    if (path === `${root}/exports/201`) {
      if (controls.previewFailures-- > 0) return reply({ error: { code: 'unavailable', message: '测试暂时断线' } }, 503);
      if (controls.previewFinish) Object.assign(state.jobs.find(j => j.id === '201')!, job('201', 'preview', 'succeeded'));
      return reply(state.jobs.find(j => j.id === '201'));
    }
    if (path === `${root}/exports/301/apply`) { state.assembly.current_media_id = '9901'; state.assembly.row_version = String(Number(state.assembly.row_version) + 1); return reply(state.jobs.find(j => j.id === '301')); }
    if (path === `${root}/exports/301`) return reply(state.jobs.find(j => j.id === '301'));
    if (path.endsWith('/cancel')) { const target = state.jobs.find(j => path.includes(`/${j.id}/`))!; target.status = 'cancelled'; target.stage = 'cancelled'; return reply(target); }
    return reply({ error: { code: 'unexpected_fixture_request', message: path } }, 404);
  });
  return { state, sound, controls, calls, errors };
}

async function captureLayouts(page: Page, name: string) {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 1366, height: 768 }, { width: 1280, height: 720 }]) {
    await page.setViewportSize(viewport);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath(`${name}-${viewport.width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
}

test('feedback details and background render progress preserve the editor geometry', async ({ page }) => {
  await page.clock.install();
  const data = await fixture(page);
  const geometry = () => page.locator('.assembly-editing').evaluate(node => {
    const box = node.getBoundingClientRect();
    return { x: box.x + scrollX, y: box.y + scrollY, width: box.width, height: box.height };
  });
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 1280, height: 720 }]) {
    data.controls.online = true; data.state.jobs = [];
    await page.setViewportSize(viewport);
    await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
    await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const original = await geometry();
    data.controls.online = false;
    await page.clock.fastForward(4000);
    await expect(page.getByText('成片进度暂时无法同步', { exact: true })).toBeVisible();
    expect(await geometry()).toEqual(original);
    await expect(await openFeedback(page)).toContainText('当前显示上次同步的进度');
    expect(await geometry()).toEqual(original);
    await page.screenshot({ path: test.info().outputPath(`feedback-workspace-${viewport.width}.png`), fullPage: true, animations: 'disabled' });
    data.controls.online = true;
    await page.getByRole('button', { name: '重新连接', exact: true }).click();
    await closeFeedback(page);
    data.state.jobs = [job('301')];
    await page.clock.fastForward(15000);
    await expect(page.getByRole('button', { name: '导出成片', exact: true })).toContainText('30%');
    expect(await geometry()).toEqual(original);
    await expect(page.locator('.assembly-workspace > .ant-alert, .assembly-render-status')).toHaveCount(0);
  }
  expect(data.errors).toEqual([]);
});

test('shared adopted work plays and refreshes without the collaborator private render history', async ({ page }) => {
  const data = await fixture(page);
  data.state.assembly.current_media_id = '9901';
  const current = { media_id: '9901', url: `${source.url}?current=1`, width: 640, height: 360, duration_ms: 3000, is_stale: false };
  Object.assign(data.state, { current_work: current });
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await expect(page.getByRole('button', { name: '成片回看', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '成片回看', exact: true }).click();
  const playback = page.getByRole('region', { name: '实际成片播放', exact: true });
  await expect(playback).toHaveAttribute('data-playback-state', 'ready');
  await expect(playback.getByRole('button', { name: '成片版本信息', exact: true })).toBeVisible();
  await expect(playback.getByRole('link', { name: '下载 MP4', exact: true })).toHaveAttribute('href', `/api/v1${root}/current/download`);
  const video = playback.locator('video');
  await video.evaluate(async node => { node.muted = true; await node.play(); });
  await expect(playback).toHaveAttribute('data-playback-state', 'playing');
  await video.evaluate(node => node.pause());
  current.url = `${source.url}?current=2`;
  const reads = data.calls.filter(call => call.path === root && call.method === 'GET').length;
  await playback.getByRole('button', { name: '刷新成片地址', exact: true }).click();
  await expect.poll(() => data.calls.filter(call => call.path === root && call.method === 'GET').length).toBe(reads + 1);
  await expect(video).toHaveAttribute('src', current.url);
  await expect(playback).toHaveAttribute('data-playback-state', 'ready');
  expect(data.state.jobs).toEqual([]);
  expect(data.calls.filter(call => call.path.includes('/exports/') || call.method === 'POST')).toEqual([]);
  expect(data.errors).toEqual([]);
});

test('lost export response survives refresh and checks the same receipt without a second job', async ({ page }) => {
  const data = await fixture(page); data.controls.abortAcceptedRender = true;
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '导出成片', exact: true }).click();
  await page.getByRole('button', { name: '开始导出', exact: true }).click();
  await expect(page.getByText('上次合成请求尚未核对', { exact: true }).first()).toBeVisible();
  await page.reload();
  await openFeedback(page);
  await page.getByRole('button', { name: '核对原合成请求', exact: true }).click();
  await expect(page.locator('.studio-feedback-details').getByText('上次合成请求尚未核对', { exact: true })).toHaveCount(0);
  const requests = data.calls.filter(c => c.path === `${root}/exports` && c.method === 'POST');
  expect(requests).toHaveLength(2); expect(requests[0].key).toBeTruthy(); expect(requests[1].key).toBe(requests[0].key);
  expect(requests[1].body).toEqual(requests[0].body); expect(data.state.jobs).toHaveLength(1);
  expect(data.errors).toEqual([]);
});

test('damaged render recovery downloads its raw record and only clears after explicit confirmation', async ({ page }) => {
  const data = await fixture(page);
  const scope = 'creation-recovery:user:anonymous:assembly:10:render';
  const otherScope = 'creation-recovery:user:anonymous:assembly:99:render';
  const raw = '  {damaged-render\n原请求核对依据';
  await page.addInitScript(({ scope, otherScope, raw }) => { localStorage.setItem(scope, raw); localStorage.setItem(otherScope, 'other record'); }, { scope, otherScope, raw });
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await expect(page.getByText('合成请求恢复记录无法读取', { exact: true }).first()).toBeVisible();
  const details = await openFeedback(page);
  const explanation = details.locator('.studio-feedback-body p');
  await expect(explanation).toBeVisible();
  expect(await explanation.evaluate(element => element.clientWidth)).toBeGreaterThan(160);
  expect(await explanation.evaluate(element => element.clientHeight)).toBeLessThan(400);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: test.info().outputPath('damaged-render-alert.png'), fullPage: true });
  await expect(page.getByRole('button', { name: '导出成片', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '合成预览', exact: true })).toBeDisabled();
  expect(data.calls.filter(call => call.method === 'POST')).toHaveLength(0);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载完整合成请求记录', exact: true }).click();
  const file = await download; expect(file.suggestedFilename()).toBe('assembly-10-render-recovery.json');
  const stream = await file.createReadStream(); const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks).toString('utf8')).toBe(raw);
  await page.getByRole('button', { name: '清除损坏的本机记录', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '清除损坏的合成请求记录', exact: true });
  await expect(confirmation).toContainText('失去原请求编号的核对依据');
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: test.info().outputPath('damaged-render-confirmation.png'), fullPage: true });
  await confirmation.getByRole('button', { name: '取消', exact: true }).click();
  expect(await page.evaluate(key => localStorage.getItem(key), scope)).toBe(raw);
  await expect(page.getByRole('button', { name: '导出成片', exact: true })).toBeDisabled();
  expect(data.calls.filter(call => call.method === 'POST')).toHaveLength(0);
  await openFeedback(page);
  await page.getByRole('button', { name: '清除损坏的本机记录', exact: true }).click();
  await confirmation.getByRole('button', { name: '已核对，清除记录', exact: true }).click();
  expect(await page.evaluate(key => localStorage.getItem(key), scope)).toBeNull();
  expect(await page.evaluate(key => localStorage.getItem(key), otherScope)).toBe('other record');
  expect(data.calls.filter(call => call.method === 'POST')).toHaveLength(0);
  await expect(page.getByRole('button', { name: '导出成片', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '导出成片', exact: true }).click();
  await page.getByRole('dialog', { name: '导出本集成片', exact: true }).getByRole('button', { name: '开始导出', exact: true }).click();
  await expect.poll(() => data.calls.filter(call => call.path === `${root}/exports` && call.method === 'POST').length).toBe(1);
  expect(data.state.jobs).toHaveLength(1);
  expect(data.errors).toEqual([]);
});

test('valid unknown render recovery only reconciles the original key and has no damaged-record clear action', async ({ page }) => {
  const data = await fixture(page); const scope = 'creation-recovery:user:anonymous:assembly:10:render';
  const original = { format: 1, kind: 'preview', key: 'original-unknown-key', body: { row_version: '1', source_hash: hash, acknowledge_stale_source: true } };
  await page.addInitScript(({ scope, original }) => localStorage.setItem(scope, JSON.stringify(original)), { scope, original });
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await expect(page.getByText('上次合成请求尚未核对', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: '清除损坏的本机记录', exact: true })).toHaveCount(0);
  expect(data.calls.filter(call => call.method === 'POST')).toHaveLength(0);
  await openFeedback(page);
  await page.getByRole('button', { name: '核对原合成请求', exact: true }).click();
  await expect(page.locator('.studio-feedback-details').getByText('上次合成请求尚未核对', { exact: true })).toHaveCount(0);
  const requests = data.calls.filter(call => call.path === `${root}/previews` && call.method === 'POST');
  expect(requests).toHaveLength(1); expect(requests[0].key).toBe(original.key); expect(requests[0].body).toEqual(original.body);
  expect(data.state.jobs).toHaveLength(1); expect(data.errors).toEqual([]);
});

test('a definite render rejection permits a corrected request with a new key', async ({ page }) => {
  const data = await fixture(page); data.controls.rejectRender = true;
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '导出成片', exact: true }).click();
  await page.getByRole('button', { name: '开始导出', exact: true }).click();
  await expect(page.getByText('请补齐已勾选的视频、等待检测完成，并检查裁剪范围。', { exact: true })).toBeVisible();
  await expect(page.locator('.studio-feedback-details').getByText('上次合成请求尚未核对', { exact: true })).toHaveCount(0);
  data.controls.rejectRender = false;
  await page.getByRole('button', { name: '开始导出', exact: true }).click();
  await expect.poll(() => data.state.jobs.length).toBe(1);
  const requests = data.calls.filter(c => c.path === `${root}/exports` && c.method === 'POST');
  expect(requests).toHaveLength(2); expect(requests[1].key).not.toBe(requests[0].key);
  expect(data.errors).toEqual([]);
});

for (const kind of ['preview', 'retry'] as const) {
  test(`lost ${kind} response checks the original request after refresh`, async ({ page }) => {
    const data = await fixture(page); data.controls.abortAcceptedRender = true;
    if (kind === 'retry') data.state.jobs = [job('301', 'export', 'failed')];
    await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
    if (kind === 'retry') await openFeedback(page);
    await page.getByRole('button', { name: kind === 'preview' ? '合成预览' : '重试这次导出', exact: true }).click();
    await expect(page.getByText('上次合成请求尚未核对', { exact: true }).first()).toBeVisible();
    await page.reload();
    await openFeedback(page);
  await page.getByRole('button', { name: '核对原合成请求', exact: true }).click();
    await expect(page.locator('.studio-feedback-details').getByText('上次合成请求尚未核对', { exact: true })).toHaveCount(0);
    const path = kind === 'preview' ? `${root}/previews` : `${root}/exports/301/retry`;
    const requests = data.calls.filter(c => c.path === path && c.method === 'POST');
    expect(requests).toHaveLength(2); expect(requests[0].key).toBeTruthy();
    expect(requests[1].key).toBe(requests[0].key); expect(requests[1].body).toEqual(requests[0].body);
    expect(data.state.jobs).toHaveLength(kind === 'preview' ? 1 : 2);
    expect(data.errors).toEqual([]);
  });
}

test('failed sound capability read exposes retry and keeps the existing sound entry', async ({ page }) => {
  const data = await fixture(page, true); data.controls.capabilityFailures = 100;
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await expect(page.getByText('声音功能读取失败', { exact: true })).toBeVisible();
  await expect(page.getByRole('group', { name: '成片设置与同步' }).getByRole('button', { name: '同步分镜视频', exact: true })).toBeEnabled();
  data.controls.capabilityFailures = 0;
  await openFeedback(page);
  await page.getByRole('button', { name: '重新读取声音功能', exact: true }).click();
  await expect(page.getByRole('button', { name: '声音、字幕和配乐', exact: true })).toBeVisible();
  expect(data.errors).toEqual([]);
});

test('standard assembly keeps the preview and timeline stable while the sound rail edits, collapses and changes viewport', async ({ page }) => {
  const data = await fixture(page, true);
  data.sound.document.subtitles = [{ start_ms: 0, end_ms: 1000, text: '原字幕' }];
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html?inline=1');
  const toolbar = page.getByRole('group', { name: '成片设置与同步' });
  const sync = toolbar.getByRole('button', { name: '同步分镜视频', exact: true });
  await expect(page.locator('.assembly-screen')).toBeVisible();
  const before = await page.locator('.assembly-screen').boundingBox();
  const trackBefore = await page.locator('.assembly-timeline').boundingBox();
  await toolbar.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  const sound = page.locator('.sound-rail');
  await expect(sound).toBeVisible();
  const after = await page.locator('.assembly-screen').boundingBox();
  const trackAfter = await page.locator('.assembly-timeline').boundingBox();
  expect(Math.abs(after!.y - before!.y)).toBeLessThanOrEqual(1);
  expect(Math.abs(after!.height - before!.height)).toBeLessThanOrEqual(1);
  expect(Math.abs(trackAfter!.y - trackBefore!.y)).toBeLessThanOrEqual(1);
  expect((await sound.boundingBox())!.x).toBeGreaterThan(after!.x + after!.width);
  await sound.getByRole('tab', { name: /字幕/ }).click();
  await sound.getByLabel('字幕文本').fill('收起后仍保留的字幕');
  const toggle = toolbar.getByRole('button', { name: '收起声音编辑', exact: true });
  await toggle.focus(); await page.keyboard.press('Tab'); await expect(sync).toBeFocused();
  await toggle.click();
  await expect(sound).toBeHidden();
  await expect.poll(() => data.sound.document.subtitles[0].text).toBe('收起后仍保留的字幕');
  await expect(toolbar.getByRole('button', { name: '声音、字幕和配乐', exact: true })).toBeFocused();
  await toolbar.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await sound.getByRole('tab', { name: /字幕/ }).click();
  await expect(sound.getByLabel('字幕文本')).toHaveValue('收起后仍保留的字幕');
  await sound.getByRole('button', { name: '字幕操作', exact: true }).click();
  await expect(page.getByRole('menuitem', { name: '导入 UTF-8 SRT', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '专注剪辑', exact: true }).click();
  await expect(sound).toBeVisible(); await expect(sync).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('standard-inline-sound-desktop.png'), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 1280, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await expect(sound).toHaveAttribute('role', 'dialog');
  expect((await sound.getByRole('button', { name: '收起声音编辑', exact: true }).boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await sound.getByRole('button', { name: '字幕操作', exact: true }).click();
  await expect(page.getByRole('menuitem', { name: '导入 UTF-8 SRT', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(sound).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('standard-inline-sound-desktop-1280.png'), fullPage: true, animations: 'disabled' });
  await sound.getByRole('button', { name: '收起声音编辑', exact: true }).focus();
  await page.keyboard.press('Escape');
  await expect(sound).toBeHidden();
  await expect(toolbar.getByRole('button', { name: '声音、字幕和配乐', exact: true })).toBeFocused();
  const exportButton = page.getByRole('button', { name: '导出成片', exact: true });
  await exportButton.scrollIntoViewIfNeeded(); await expect(exportButton).toBeInViewport();
  await expect.poll(() => page.locator('.episode-work-stage').evaluate(node => getComputedStyle(node).overflowY)).toBe('hidden');
  await page.screenshot({ path: test.info().outputPath('standard-inline-sound-desktop-1280-export.png'), fullPage: true, animations: 'disabled' });
  await page.getByRole('button', { name: '退出专注剪辑', exact: true }).click();
  await toolbar.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await sound.getByRole('tab', { name: /字幕/ }).click();
  await expect(sound.getByLabel('字幕文本')).toHaveValue('收起后仍保留的字幕');
  expect(data.errors).toEqual([]);
});

test('export settings save the selected resolution before freezing the render request', async ({ page }) => {
  const data = await fixture(page);
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html?inline=1');
  await expect(page.locator('.assembly-export-bar')).toHaveCount(0);
  await expect(page.getByLabel('导出清晰度')).toHaveCount(0);
  await page.getByRole('button', { name: '导出成片', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '导出本集成片', exact: true });
  await dialog.getByRole('combobox', { name: '导出清晰度', exact: true }).press('ArrowDown');
  await page.getByText('1080p', { exact: true }).filter({ visible: true }).click();
  await dialog.getByRole('button', { name: '开始导出', exact: true }).click();
  await expect(dialog).toBeHidden();
  const saved = data.calls.findLast(call => call.method === 'PATCH');
  const render = data.calls.find(call => call.path === `${root}/exports` && call.method === 'POST');
  expect(saved?.body.resolution).toBe('1080p');
  expect(render?.body.row_version).toBe(data.state.assembly.row_version);
  expect(data.calls.indexOf(saved!)).toBeLessThan(data.calls.indexOf(render!));
  expect(data.errors).toEqual([]);
});

test('export history keeps its accessible name while loading', async ({ page }) => {
  const data = await fixture(page);
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { finish = resolve; });
  await page.route(url => url.pathname === `/api/v1${root}/exports`, async route => {
    if (route.request().method() !== 'GET') return route.fallback();
    await pending;
    return route.fulfill({ json: { items: [], has_more: false } });
  });
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  const history = page.getByRole('button', { name: '导出记录', exact: true });
  await history.click();
  await expect(history).toHaveAttribute('aria-busy', 'true');
  finish();
  await expect(page.getByRole('dialog', { name: '导出记录', exact: true })).toBeVisible();
  await expect(history).toHaveAttribute('aria-busy', 'false');
  expect(data.errors).toEqual([]);
});

test('assembly polling failure reports stale progress and reconnects without clearing edits', async ({ page }) => {
  await page.clock.install();
  const data = await fixture(page);
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await expect(page.getByRole('button', { name: '片段静音', exact: true })).toBeVisible();
  data.controls.online = false;
  await page.clock.fastForward(4000);
  await expect(page.getByText('成片进度暂时无法同步', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '片段静音', exact: true }).click();
  data.controls.online = true;
  await openFeedback(page);
  await page.getByRole('button', { name: '重新连接', exact: true }).click();
  await expect(page.locator('.studio-feedback-details').getByText('成片进度暂时无法同步', { exact: true })).toHaveCount(0);
  await closeFeedback(page);
  await expect(page.getByRole('button', { name: '恢复原声', exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(data.errors).toEqual([]);
});

test('assembly polling slows when idle and stays frequent for an active render', async ({ page }) => {
  await page.clock.install();
  const data = await fixture(page);
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await expect(page.getByRole('button', { name: '片段静音', exact: true })).toBeVisible();
  const reads = () => data.calls.filter(c => c.path === root && c.method === 'GET').length;
  const initial = reads();
  await page.clock.runFor(4000);
  await expect.poll(reads).toBe(initial + 1);
  await page.clock.runFor(4000);
  expect(reads()).toBe(initial + 1);
  data.state.jobs = [job('301')];
  await page.clock.runFor(11000);
  await expect.poll(reads).toBe(initial + 2);
  await page.clock.runFor(4000);
  await expect.poll(reads).toBe(initial + 3);
  expect(data.errors).toEqual([]);
});

test('audio address refresh keeps unsaved subtitle text and music edits', async ({ page }) => {
  await page.clock.install();
  const data = await fixture(page, true);
  const music = { media_id: '901', start_ms: 0, trim_in_ms: 0, trim_out_ms: 2000, volume: 0.2, loop: false, fade_in_ms: 0, fade_out_ms: 0, ducking: false };
  Object.assign(data.sound, { media: { '901': { url: '/expired-audio.wav', duration_ms: 2000 } }, document: { ...data.sound.document, music, subtitles: [{ start_ms: 0, end_ms: 1000, text: '服务端字幕' }] } });
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await page.getByRole('tab', { name: /字幕/ }).click();
  await expect(page.locator('.sound-subtitle textarea')).toBeVisible();
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  await page.locator('.sound-subtitle textarea').fill('尚未保存的字幕');
  await page.getByRole('tab', { name: '配乐与混音', exact: true }).click();
  await page.getByLabel('配乐音量', { exact: true }).fill('0.35');
  const fields = page.locator('.sound-fields');
  const fieldsGeometry = await fields.boundingBox();
  await page.locator('.sound-fields audio').dispatchEvent('error');
  Object.assign(data.sound.media, { '901': { url: '/refreshed-audio.wav', duration_ms: 2000 } });
  await openFeedback(page);
  expect(await fields.boundingBox()).toEqual(fieldsGeometry);
  await page.getByRole('button', { name: '刷新试听地址', exact: true }).click();
  await closeFeedback(page);
  await expect(page.locator('.sound-fields audio')).toHaveAttribute('src', '/refreshed-audio.wav');
  await page.getByRole('tab', { name: /字幕/ }).click();
  await expect(page.locator('.sound-subtitle textarea')).toHaveValue('尚未保存的字幕');
  await page.getByRole('tab', { name: '配乐与混音', exact: true }).click();
  await expect(page.getByLabel('配乐音量', { exact: true })).toHaveValue('0.35');
  expect(data.calls.filter(c => c.path === soundRoot && c.method === 'PUT')).toHaveLength(0);
  expect(data.errors).toEqual([]);
});

test('unsaved clip edits silently recover and resume saving after refresh', async ({ page }) => {
  await page.clock.install(); const data = await fixture(page);
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '片段静音', exact: true }).click();
  await page.reload();
  await expect(page.getByText('发现本机未保存的剪辑恢复稿', { exact: true })).toHaveCount(0);
  expect(data.calls.filter(c => c.method === 'PATCH')).toHaveLength(0);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '恢复原声', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.clock.fastForward(650);
  await expect.poll(() => data.calls.filter(c => c.method === 'PATCH').length).toBe(1);
  expect(data.calls.find(c => c.method === 'PATCH')!.body.clips[0].muted).toBe(true);
  expect(data.errors).toEqual([]);
});

test('retrying a failed initial assembly read silently restores and saves recovery input', async ({ page }) => {
  await page.clock.install(); const data = await fixture(page);
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  await page.getByRole('button', { name: '片段静音', exact: true }).click();
  data.controls.online = false;
  await page.reload();
  await expect(await openFeedback(page)).toContainText('成片工作台未能载入');
  data.controls.online = true;
  await page.getByRole('button', { name: '重新加载', exact: true }).click();
  await closeFeedback(page);
  await expect(page.getByRole('button', { name: '恢复原声', exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(data.calls.filter(c => c.method === 'PATCH')).toHaveLength(0);
  expect(await page.evaluate(() => localStorage.getItem('creation-recovery:user:anonymous:assembly:10:draft'))).toContain('"muted":true');
  await page.clock.fastForward(650);
  await expect.poll(() => data.calls.filter(c => c.method === 'PATCH').length).toBe(1);
  expect(data.errors).toEqual([]);
});

test('unsaved sound edits silently recover and save without implicit review or adoption', async ({ page }) => {
  await page.clock.install(); const data = await fixture(page, true);
  data.sound.document.subtitles = [{ start_ms: 0, end_ms: 1000, text: '原字幕' }];
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await page.getByRole('tab', { name: /字幕/ }).click();
  await page.locator('.sound-subtitle textarea').fill('恢复字幕');
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).some(key => key.includes('sound:10:draft')))).toBe(true);
  await page.reload();
  await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await expect(page.getByText('发现本机未保存的声音恢复稿', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('dialog', { name: '确认操作', exact: true })).toHaveCount(0);
  await page.getByRole('tab', { name: /字幕/ }).click();
  await expect(page.locator('.sound-subtitle textarea')).toHaveValue('恢复字幕');
  expect(data.calls.filter(c => c.path === soundRoot && c.method === 'PUT')).toHaveLength(0);
  await page.clock.fastForward(1000);
  await expect.poll(() => data.calls.filter(c => c.path === soundRoot && c.method === 'PUT').length).toBe(1);
  expect(data.calls.find(c => c.path === soundRoot && c.method === 'PUT')!.body.reviewed).toBe(false);
  expect(data.calls.filter(c => c.path.endsWith('/adopt'))).toHaveLength(0);
  expect(data.errors).toEqual([]);
});

for (const backupAvailable of [true, false]) test(`subtitle replacement ${backupAvailable ? 'keeps a durable backup without confirmation' : 'keeps existing input when backup storage fails'}`, async ({ page }) => {
  await page.clock.install(); const data = await fixture(page, true);
  data.sound.document.subtitles = [{ start_ms: 0, end_ms: 1000, text: '替换前的字幕' }];
  await page.route(`**/api/v1${soundRoot}/native-subtitles`, route => route.fulfill({ json: { timeline_hash: hash, notice: '', subtitles: [{ start_ms: 0, end_ms: 1000, text: '采用视频的台词' }] } }));
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  if (!backupAvailable) await page.evaluate(() => {
    const write = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) { if (key.endsWith(':previous')) throw new DOMException('quota exceeded', 'QuotaExceededError'); return write.call(this, key, value); };
  });
  await page.getByRole('button', { name: '从采用视频的台词创建字幕草稿', exact: true }).click();
  await page.getByRole('tab', { name: /字幕/ }).click();
  await expect(page.getByLabel('字幕文本')).toHaveValue(backupAvailable ? '采用视频的台词' : '替换前的字幕');
  await expect(page.getByRole('dialog', { name: '确认操作', exact: true })).toHaveCount(0);
  if (backupAvailable) {
    expect(await page.evaluate(() => localStorage.getItem('creation-recovery:user:anonymous:sound:10:draft:previous'))).toContain('替换前的字幕');
    await page.reload();
    await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
    await expect(page.getByRole('button', { name: '下载本机备份', exact: true })).toBeVisible();
  } else {
    await expect(page.getByRole('alert').filter({ hasText: '无法保存本机恢复记录' })).toBeVisible();
  }
  expect(data.calls.filter(c => c.path === soundRoot && c.method === 'PUT')).toHaveLength(0);
  expect(data.errors).toEqual([]);
});

for (const kind of ['assembly', 'sound'] as const) {
  test(`corrupt ${kind} recovery stays available until explicit discard`, async ({ page }) => {
    const data = await fixture(page, true);
    const scope = `creation-recovery:user:anonymous:${kind}:10:draft`;
    await page.addInitScript(({ key }) => localStorage.setItem(key, '{damaged-record'), { key: scope });
    await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
    if (kind === 'sound') await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
    await expect(page.getByText('本机恢复记录无法读取，原记录已保留。请下载后核对。', { exact: true }).first()).toBeVisible();
    await openFeedback(page);
    expect(await page.evaluate(key => localStorage.getItem(key), scope)).toBe('{damaged-record');
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: '下载完整恢复稿', exact: true }).click();
    expect((await download).suggestedFilename()).toContain('recovery');
    if (kind === 'assembly') await expect(page.getByRole('button', { name: '片段静音', exact: true })).toBeEnabled();
    else {
      const save = page.getByRole('button', { name: '保存声音草稿', exact: true });
      await expect(save).toHaveAttribute('aria-busy', 'false');
      await expect(save).toBeEnabled();
    }
    expect(data.calls.filter(c => ['PUT', 'PATCH', 'POST'].includes(c.method))).toHaveLength(0);
    await page.getByRole('button', { name: '使用服务端版本', exact: true }).click();
    await closeFeedback(page);
    await expect(page.getByRole('dialog', { name: '未保存的修改', exact: true })).toHaveCount(0);
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), scope)).toBeNull();
    expect(await page.evaluate(key => localStorage.getItem(`${key}:previous`), scope)).toContain('{damaged-record');
    if (kind === 'assembly') {
      await page.getByRole('button', { name: '更多成片操作', exact: true }).click();
      const backupDownload = page.waitForEvent('download');
      await page.getByRole('menuitem', { name: '下载本机备份', exact: true }).click();
      expect((await backupDownload).suggestedFilename()).toContain('recovery');
    } else await expect(page.getByRole('button', { name: '下载本机备份', exact: true })).toBeVisible();
    if (kind === 'assembly') await expect(page.getByRole('button', { name: '片段静音', exact: true })).toBeEnabled();
    else {
      const save = page.getByRole('button', { name: '保存声音草稿', exact: true });
      await expect(save).toHaveAttribute('aria-busy', 'false');
      await expect(save).toBeEnabled();
    }
    expect(data.errors).toEqual([]);
  });
}

test('failed initial load retries the GET and restores the editor', async ({ page }) => {
  const data = await fixture(page); data.controls.online = false;
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await expect(await openFeedback(page)).toContainText('成片工作台未能载入');
  data.controls.online = true;
  await page.getByRole('button', { name: '重新加载', exact: true }).click();
  await closeFeedback(page);
  await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
  await expect(page.locator('.assembly-save')).toHaveCount(0);
  expect(data.calls.filter(c => c.path === root && c.method === 'PATCH')).toHaveLength(0);
  expect(data.errors).toEqual([]);
});

test('reserved shots are kept without blocking preview or export or requiring extra actions', async ({ page }) => {
  const data = await fixture(page);
  const reserved = { ...source, id: '1002', shot_id: '102', media_id: null, url: null, duration_ms: null, trim_out_ms: null, issue: 'missing', is_stale: true } as any;
  data.state.clips = [reserved, { ...source }];
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await expect(page.getByRole('button', { name: '导出成片', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: '合成预览', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: '排除缺失视频', exact: true })).toHaveCount(0);
  await expect(page.getByLabel('无法显示在时间轴的片段')).toHaveCount(0);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.locator('.assembly-track-clip')).toHaveCount(1);
  await expect(page.locator('.assembly-creation-meta')).toContainText('1 段');
  await captureLayouts(page, 'reserved-shots');
  await page.getByRole('button', { name: '合成预览', exact: true }).click();
  await expect(page.getByRole('region', { name: '实际成片播放' })).toBeVisible();
  await page.getByRole('button', { name: '剪辑预览', exact: true }).click();
  await page.getByRole('button', { name: '导出成片', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '导出本集成片', exact: true });
  await expect(dialog.locator('.assembly-export-summary')).toContainText('1 段');
  await dialog.getByRole('button', { name: '开始导出', exact: true }).click();
  await expect.poll(() => data.calls.filter(c => c.path === `${root}/exports` && c.method === 'POST').length).toBe(1);
  expect(data.calls.filter(c => c.path === root && c.method === 'PATCH')).toHaveLength(0);
  expect(data.state.clips).toHaveLength(2);
  expect(data.state.clips[0]).toEqual(reserved);
  expect(data.state.clips[1]).toMatchObject({ trim_in_ms: 0, trim_out_ms: 3000, included: true });
  expect(data.errors).toEqual([]);
});

test('preview survives a temporary polling failure and automatically shows the result', async ({ page }) => {
  const data = await fixture(page); data.controls.previewFailures = 1;
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '合成预览', exact: true }).click();
  await expect(await openFeedback(page)).toContainText('正在自动重新连接');
  await closeFeedback(page);
  await expect(page.getByRole('region', { name: '实际成片播放' })).toBeVisible();
  await page.getByRole('button', { name: '成片版本信息', exact: true }).hover();
  await expect(page.getByRole('tooltip')).toHaveText('合成预览：当前保存版本');
  expect(data.calls.filter(c => c.path === `${root}/previews` && c.method === 'POST')).toHaveLength(1);
  expect(data.errors).toEqual([]);
});

test('export failure stays visible even while draft saves are paused', async ({ page }) => {
  const data = await fixture(page); data.state.jobs = [job('301')]; data.controls.saveFails = true;
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '片段静音', exact: true }).click();
  await expect(await openFeedback(page)).toContainText('当前输入已保留，可继续切换');
  Object.assign(data.state.jobs[0], { status: 'failed', stage: 'failed', error: { message: '测试导出失败：磁盘空间不足' } });
  await expect(page.locator('.studio-feedback-details').getByText('最近一次导出失败', { exact: true })).toBeVisible({ timeout: 10000 });
  await expect(page.locator('.studio-feedback-details').getByText('测试导出失败：磁盘空间不足')).toBeVisible();
  await closeFeedback(page);
  await expect(page.getByRole('button', { name: '恢复原声', exact: true })).toBeVisible();
  expect(data.errors).toEqual([]);
});

test('export history refreshes and a completed result is available for playback, download and adoption', async ({ page }) => {
  const data = await fixture(page); data.state.jobs = [job('301')];
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '导出记录', exact: true }).click();
  const history = page.getByRole('dialog', { name: '导出记录', exact: true });
  await expect(history.getByText('合成片段', { exact: true })).toBeVisible();
  Object.assign(data.state.jobs[0], job('301', 'export', 'succeeded'));
  await expect(history.getByRole('button', { name: '播放', exact: true })).toBeVisible({ timeout: 10000 });
  await history.getByRole('button', { name: '关闭弹窗', exact: true }).click();
  const result = await openFeedback(page);
  await expect(result.getByRole('link', { name: '下载导出 MP4' })).toHaveAttribute('href', `/api/v1${root}/exports/301/download`);
  await closeFeedback(page);
  await captureLayouts(page, 'completed-export');
  await openFeedback(page);
  await result.getByRole('button', { name: '播放导出结果', exact: true }).click();
  await closeFeedback(page);
  await expect(page.getByRole('region', { name: '实际成片播放' })).toBeVisible();
  await openFeedback(page);
  await result.getByRole('button', { name: '设为当前成片', exact: true }).click();
  await page.getByRole('dialog', { name: '设为当前成片', exact: true }).getByRole('button', { name: '确认采用', exact: true }).click();
  await expect(await openFeedback(page)).toContainText('当前成片');
  expect(data.state.assembly.current_media_id).toBe('9901');
  expect(data.errors).toEqual([]);
});

test('render submission saves the sound draft before freezing an export while controls are locked', async ({ page }) => {
  const data = await fixture(page, true);
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await page.getByRole('tab', { name: '字幕 · 0' }).click();
  await page.getByRole('button', { name: '添加字幕', exact: true }).click();
  await page.getByLabel('字幕文本').fill('测试字幕应进入导出快照');
  // Exercise the render boundary with an open sound draft; action() locks its UI
  // before flushing, so a persistence check against disabled would lose this save.
  await page.getByRole('button', { name: '导出成片', exact: true }).evaluate(button => (button as HTMLButtonElement).click());
  const dialog = page.getByRole('dialog', { name: '导出本集成片', exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: '开始导出', exact: true }).click();
  await expect.poll(() => data.calls.filter(c => c.path === `${root}/exports` && c.method === 'POST').length).toBe(1);
  expect(data.sound.document.subtitles[0].text).toBe('测试字幕应进入导出快照');
  const soundSave = data.calls.findIndex(c => c.path === soundRoot && c.method === 'PUT');
  const exportSubmit = data.calls.findIndex(c => c.path === `${root}/exports` && c.method === 'POST');
  expect(soundSave).toBeGreaterThanOrEqual(0); expect(exportSubmit).toBeGreaterThan(soundSave);
  expect(data.errors).toEqual([]);
});

test('a stale poll cannot hide the assembly version loaded after saving sound', async ({ page }) => {
  const data = await fixture(page, true);
  await page.clock.install();
  await page.goto('/e2e/assembly/fixtures/assembly-flow-fixture.html');
  await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
  await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  const deferred = [0, 1].map(() => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    return { gate, release, version: '', received: false, delivered: false };
  });
  let heldGets = 0;
  await page.route(`**/api/v1${root}/editor`, async route => {
    if (route.request().method() !== 'GET' || heldGets >= deferred.length) return route.fallback();
    const held = deferred[heldGets++];
    const snapshot = structuredClone(data.state);
    held.version = snapshot.assembly.row_version;
    held.received = true;
    await held.gate;
    await route.fulfill({ json: snapshot });
    held.delivered = true;
  });
  // The interval starts an old GET before the sound PUT advances row_version.
  await page.clock.fastForward(4000);
  await expect.poll(() => deferred[0].received).toBe(true);
  expect(deferred[0].version).toBe('1');
  await page.getByRole('tab', { name: '字幕 · 0' }).click();
  await page.getByRole('button', { name: '添加字幕', exact: true }).click();
  await page.getByLabel('字幕文本').fill('声音保存会更新成片版本');
  await page.getByRole('checkbox', { name: '已核对当前剪辑的声音与字幕时间' }).check();
  await page.getByRole('button', { name: '保存声音草稿', exact: true }).click();
  await expect.poll(() => deferred[1].received).toBe(true);
  expect(deferred[1].version).toBe('2');

  // Deliver the old poll first and let its React update run while the new GET
  // is still held. Identity-only guards used to drop the new version here.
  deferred[0].release();
  await expect.poll(() => deferred[0].delivered).toBe(true);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  deferred[1].release();
  await expect.poll(() => deferred[1].delivered).toBe(true);
  await expect(page.getByRole('button', { name: '保存声音草稿', exact: true })).not.toHaveClass(/ant-btn-loading/);
  await page.locator('.sound-drawer .ant-drawer-close').click();

  await page.getByRole('button', { name: '片段静音', exact: true }).click();
  await expect.poll(() => data.calls.filter(c => c.path === root && c.method === 'PATCH').length).toBe(1);
  const edit = data.calls.find(c => c.path === root && c.method === 'PATCH')!;
  expect(edit.body.row_version).toBe('2');
  expect(edit.body.clips[0]).toMatchObject({ muted: true, trim_in_ms: 0, trim_out_ms: 3000 });
  expect(data.errors).toEqual([]);
});
