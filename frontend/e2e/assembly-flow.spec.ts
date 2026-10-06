import { test, expect, type Page } from '@playwright/test';

const root = '/projects/10/episodes/20/assembly';
const soundRoot = '/projects/10/episodes/20/sound';
const hash = 'a'.repeat(64);
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
    const request = route.request(), path = new URL(request.url()).pathname.slice('/api/v1'.length), method = request.method();
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
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 1024, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath(`${name}-${viewport.width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
}

test('shared adopted work plays and refreshes without the collaborator private render history', async ({ page }) => {
  const data = await fixture(page);
  data.state.assembly.current_media_id = '9901';
  const current = { media_id: '9901', url: `${source.url}?current=1`, width: 640, height: 360, duration_ms: 3000, is_stale: false };
  Object.assign(data.state, { current_work: current });
  await page.goto('/e2e/assembly-flow-fixture.html');
  await expect(page.getByRole('button', { name: '成片回看', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '成片回看', exact: true }).click();
  const playback = page.getByRole('region', { name: '实际成片播放', exact: true });
  await expect(playback).toHaveAttribute('data-playback-state', 'ready');
  await expect(playback).toContainText('当前采用成片');
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
  await page.goto('/e2e/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '导出成片', exact: true }).click();
  await page.getByRole('button', { name: '开始合成', exact: true }).click();
  await expect(page.getByText('上次合成请求尚未核对', { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '核对原合成请求', exact: true }).click();
  await expect(page.getByText('上次合成请求尚未核对', { exact: true })).toBeHidden();
  const requests = data.calls.filter(c => c.path === `${root}/exports` && c.method === 'POST');
  expect(requests).toHaveLength(2); expect(requests[0].key).toBeTruthy(); expect(requests[1].key).toBe(requests[0].key);
  expect(requests[1].body).toEqual(requests[0].body); expect(data.state.jobs).toHaveLength(1);
  expect(data.errors).toEqual([]);
});

test('damaged render recovery downloads its raw record and only clears after explicit confirmation', async ({ page }) => {
  const data = await fixture(page);
  const scope = 'creation-recovery:user:anonymous:assembly:10:20:render';
  const otherScope = 'creation-recovery:user:anonymous:assembly:10:99:render';
  const raw = '  {damaged-render\n原请求核对依据';
  await page.addInitScript(({ scope, otherScope, raw }) => { localStorage.setItem(scope, raw); localStorage.setItem(otherScope, 'other record'); }, { scope, otherScope, raw });
  await page.goto('/e2e/assembly-flow-fixture.html');
  await expect(page.getByText('合成请求恢复记录无法读取', { exact: true })).toBeVisible();
  const explanation = page.getByRole('alert').filter({ hasText: '合成请求恢复记录无法读取' }).locator('.ant-alert-description p');
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
  const file = await download; expect(file.suggestedFilename()).toBe('assembly-20-render-recovery.json');
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
  await page.getByRole('button', { name: '清除损坏的本机记录', exact: true }).click();
  await confirmation.getByRole('button', { name: '已核对，清除记录', exact: true }).click();
  expect(await page.evaluate(key => localStorage.getItem(key), scope)).toBeNull();
  expect(await page.evaluate(key => localStorage.getItem(key), otherScope)).toBe('other record');
  expect(data.calls.filter(call => call.method === 'POST')).toHaveLength(0);
  await expect(page.getByRole('button', { name: '导出成片', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '导出成片', exact: true }).click();
  await page.getByRole('dialog', { name: '导出本集成片', exact: true }).getByRole('button', { name: '开始合成', exact: true }).click();
  await expect.poll(() => data.calls.filter(call => call.path === `${root}/exports` && call.method === 'POST').length).toBe(1);
  expect(data.state.jobs).toHaveLength(1);
  expect(data.errors).toEqual([]);
});

test('valid unknown render recovery only reconciles the original key and has no damaged-record clear action', async ({ page }) => {
  const data = await fixture(page); const scope = 'creation-recovery:user:anonymous:assembly:10:20:render';
  const original = { format: 1, kind: 'preview', key: 'original-unknown-key', body: { row_version: '1', source_hash: hash, acknowledge_stale_source: true } };
  await page.addInitScript(({ scope, original }) => localStorage.setItem(scope, JSON.stringify(original)), { scope, original });
  await page.goto('/e2e/assembly-flow-fixture.html');
  await expect(page.getByText('上次合成请求尚未核对', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '清除损坏的本机记录', exact: true })).toHaveCount(0);
  expect(data.calls.filter(call => call.method === 'POST')).toHaveLength(0);
  await page.getByRole('button', { name: '核对原合成请求', exact: true }).click();
  await expect(page.getByText('上次合成请求尚未核对', { exact: true })).toBeHidden();
  const requests = data.calls.filter(call => call.path === `${root}/previews` && call.method === 'POST');
  expect(requests).toHaveLength(1); expect(requests[0].key).toBe(original.key); expect(requests[0].body).toEqual(original.body);
  expect(data.state.jobs).toHaveLength(1); expect(data.errors).toEqual([]);
});

test('a definite render rejection permits a corrected request with a new key', async ({ page }) => {
  const data = await fixture(page); data.controls.rejectRender = true;
  await page.goto('/e2e/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '导出成片', exact: true }).click();
  await page.getByRole('button', { name: '开始合成', exact: true }).click();
  await expect(page.getByText('请补齐已勾选的视频、等待检测完成，并检查裁剪范围。', { exact: true })).toBeVisible();
  await expect(page.getByText('上次合成请求尚未核对', { exact: true })).toBeHidden();
  data.controls.rejectRender = false;
  await page.getByRole('button', { name: '开始合成', exact: true }).click();
  await expect.poll(() => data.state.jobs.length).toBe(1);
  const requests = data.calls.filter(c => c.path === `${root}/exports` && c.method === 'POST');
  expect(requests).toHaveLength(2); expect(requests[1].key).not.toBe(requests[0].key);
  expect(data.errors).toEqual([]);
});

for (const kind of ['preview', 'retry'] as const) {
  test(`lost ${kind} response checks the original request after refresh`, async ({ page }) => {
    const data = await fixture(page); data.controls.abortAcceptedRender = true;
    if (kind === 'retry') data.state.jobs = [job('301', 'export', 'failed')];
    await page.goto('/e2e/assembly-flow-fixture.html');
    await page.getByRole('button', { name: kind === 'preview' ? '合成预览' : '重试这次导出', exact: true }).click();
    await expect(page.getByText('上次合成请求尚未核对', { exact: true })).toBeVisible();
    await page.reload();
    await page.getByRole('button', { name: '核对原合成请求', exact: true }).click();
    await expect(page.getByText('上次合成请求尚未核对', { exact: true })).toBeHidden();
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
  await page.goto('/e2e/assembly-flow-fixture.html');
  await expect(page.getByText('声音功能读取失败', { exact: true })).toBeVisible();
  data.controls.capabilityFailures = 0;
  await page.getByRole('button', { name: '重新读取声音功能', exact: true }).click();
  await expect(page.getByRole('button', { name: '声音、字幕和配乐', exact: true })).toBeVisible();
  expect(data.errors).toEqual([]);
});

test('assembly polling failure reports stale progress and reconnects without clearing edits', async ({ page }) => {
  await page.clock.install();
  const data = await fixture(page);
  await page.goto('/e2e/assembly-flow-fixture.html');
  await expect(page.getByRole('button', { name: '片段静音', exact: true })).toBeVisible();
  data.controls.online = false;
  await page.clock.fastForward(4000);
  await expect(page.getByText('成片进度暂时无法同步', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '片段静音', exact: true }).click();
  data.controls.online = true;
  await page.getByRole('button', { name: '重新连接', exact: true }).click();
  await expect(page.getByText('成片进度暂时无法同步', { exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: '恢复原声', exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(data.errors).toEqual([]);
});

test('assembly polling slows when idle and stays frequent for an active render', async ({ page }) => {
  await page.clock.install();
  const data = await fixture(page);
  await page.goto('/e2e/assembly-flow-fixture.html');
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
  const data = await fixture(page, true);
  const music = { media_id: '901', start_ms: 0, trim_in_ms: 0, trim_out_ms: 2000, volume: 0.2, loop: false, fade_in_ms: 0, fade_out_ms: 0, ducking: false };
  Object.assign(data.sound, { media: { '901': { url: '/expired-audio.wav', duration_ms: 2000 } }, document: { ...data.sound.document, music, subtitles: [{ start_ms: 0, end_ms: 1000, text: '服务端字幕' }] } });
  await page.goto('/e2e/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await page.getByRole('tab', { name: /字幕/ }).click();
  await page.locator('.sound-subtitle textarea').fill('尚未保存的字幕');
  await page.getByRole('tab', { name: '配乐与混音', exact: true }).click();
  await page.getByLabel('配乐音量', { exact: true }).fill('0.35');
  await page.locator('.sound-fields audio').dispatchEvent('error');
  Object.assign(data.sound.media, { '901': { url: '/refreshed-audio.wav', duration_ms: 2000 } });
  await page.getByRole('button', { name: '刷新试听地址', exact: true }).click();
  await expect(page.locator('.sound-fields audio')).toHaveAttribute('src', '/refreshed-audio.wav');
  await page.getByRole('tab', { name: /字幕/ }).click();
  await expect(page.locator('.sound-subtitle textarea')).toHaveValue('尚未保存的字幕');
  await page.getByRole('tab', { name: '配乐与混音', exact: true }).click();
  await expect(page.getByLabel('配乐音量', { exact: true })).toHaveValue('0.35');
  expect(data.calls.filter(c => c.path === soundRoot && c.method === 'PUT')).toHaveLength(0);
  expect(data.errors).toEqual([]);
});

test('unsaved clip edits survive refresh and require explicit recovery', async ({ page }) => {
  await page.clock.install(); const data = await fixture(page);
  await page.goto('/e2e/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '片段静音', exact: true }).click();
  await page.reload();
  await expect(page.getByText('发现本机未保存的剪辑恢复稿', { exact: true })).toBeVisible();
  expect(data.calls.filter(c => c.method === 'PATCH')).toHaveLength(0);
  await page.getByRole('button', { name: '核对并恢复剪辑', exact: true }).click();
  await page.getByRole('dialog', { name: '确认操作', exact: true }).getByRole('button', { name: '确认继续', exact: true }).click();
  await expect(page.getByRole('button', { name: '恢复原声', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.clock.fastForward(650);
  await expect.poll(() => data.calls.filter(c => c.method === 'PATCH').length).toBe(1);
  expect(data.calls.find(c => c.method === 'PATCH')!.body.clips[0].muted).toBe(true);
  expect(data.errors).toEqual([]);
});

test('retrying a failed initial assembly read still offers the saved recovery draft', async ({ page }) => {
  await page.clock.install(); const data = await fixture(page);
  await page.goto('/e2e/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '片段静音', exact: true }).click();
  data.controls.online = false;
  await page.reload();
  await expect(page.getByRole('alert')).toContainText('成片工作台未能载入');
  data.controls.online = true;
  await page.getByRole('button', { name: '重新加载', exact: true }).click();
  await expect(page.getByText('发现本机未保存的剪辑恢复稿', { exact: true })).toBeVisible();
  expect(data.calls.filter(c => c.method === 'PATCH')).toHaveLength(0);
  expect(await page.evaluate(() => localStorage.getItem('creation-recovery:user:anonymous:assembly:10:20:draft'))).toContain('"muted":true');
  expect(data.errors).toEqual([]);
});

test('unsaved sound edits survive refresh without automatic save or adoption', async ({ page }) => {
  const data = await fixture(page, true);
  data.sound.document.subtitles = [{ start_ms: 0, end_ms: 1000, text: '原字幕' }];
  await page.goto('/e2e/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await page.getByRole('tab', { name: /字幕/ }).click();
  await page.locator('.sound-subtitle textarea').fill('恢复字幕');
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).some(key => key.includes('sound:10:20:draft')))).toBe(true);
  await page.reload();
  await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await expect(page.getByText('发现本机未保存的声音恢复稿', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '核对并恢复声音', exact: true }).click();
  await page.getByRole('dialog', { name: '确认操作', exact: true }).getByRole('button', { name: '确认继续', exact: true }).click();
  await page.getByRole('tab', { name: /字幕/ }).click();
  await expect(page.locator('.sound-subtitle textarea')).toHaveValue('恢复字幕');
  expect(data.calls.filter(c => c.path === soundRoot && c.method === 'PUT')).toHaveLength(0);
  expect(data.errors).toEqual([]);
});

for (const kind of ['assembly', 'sound'] as const) {
  test(`corrupt ${kind} recovery stays available until explicit discard`, async ({ page }) => {
    const data = await fixture(page, true);
    const scope = `creation-recovery:user:anonymous:${kind}:10:20:draft`;
    await page.addInitScript(({ key }) => localStorage.setItem(key, '{damaged-record'), { key: scope });
    await page.goto('/e2e/assembly-flow-fixture.html');
    if (kind === 'sound') await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
    await expect(page.getByText(`${kind === 'assembly' ? '剪辑' : '声音'}恢复记录无法读取，请下载完整恢复记录核对，明确放弃后才能继续编辑。`, { exact: true })).toBeVisible();
    expect(await page.evaluate(key => localStorage.getItem(key), scope)).toBe('{damaged-record');
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: '下载完整恢复稿', exact: true }).click();
    expect((await download).suggestedFilename()).toContain('recovery');
    if (kind === 'assembly') await expect(page.getByRole('button', { name: '片段静音', exact: true })).toBeDisabled();
    else await expect(page.getByRole('button', { name: '保存声音草稿', exact: true })).toBeDisabled();
    expect(data.calls.filter(c => ['PUT', 'PATCH', 'POST'].includes(c.method))).toHaveLength(0);
    await page.getByRole('button', { name: '放弃恢复稿', exact: true }).click();
    await page.getByRole('dialog', { name: '未保存的修改', exact: true }).getByRole('button', { name: '放弃修改', exact: true }).click();
    expect(await page.evaluate(key => localStorage.getItem(key), scope)).toBeNull();
    if (kind === 'assembly') await expect(page.getByRole('button', { name: '片段静音', exact: true })).toBeEnabled();
    else await expect(page.getByRole('button', { name: '保存声音草稿', exact: true })).toBeEnabled();
    expect(data.errors).toEqual([]);
  });
}

test('failed initial load retries the GET and restores the editor', async ({ page }) => {
  const data = await fixture(page); data.controls.online = false;
  await page.goto('/e2e/assembly-flow-fixture.html');
  await expect(page.getByRole('alert')).toContainText('成片工作台未能载入');
  data.controls.online = true;
  await page.getByRole('button', { name: '重新加载', exact: true }).click();
  await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
  await expect(page.locator('.assembly-save')).toHaveText('成片草稿已保存');
  expect(data.calls.filter(c => c.path === root && c.method === 'PATCH')).toHaveLength(0);
  expect(data.errors).toEqual([]);
});

test('reserved shots are kept without blocking preview or export or requiring extra actions', async ({ page }) => {
  const data = await fixture(page);
  const reserved = { ...source, id: '1002', shot_id: '102', media_id: null, url: null, duration_ms: null, trim_out_ms: null, issue: 'missing', is_stale: true } as any;
  data.state.clips = [reserved, { ...source }];
  await page.goto('/e2e/assembly-flow-fixture.html');
  await expect(page.getByRole('button', { name: '导出成片', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: '合成预览', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: '排除缺失视频', exact: true })).toHaveCount(0);
  await expect(page.getByLabel('无法显示在时间轴的片段')).toHaveCount(0);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.locator('.assembly-track-clip')).toHaveCount(1);
  await expect(page.locator('.assembly-export-bar strong')).toContainText('1 个片段');
  await captureLayouts(page, 'reserved-shots');
  await page.getByRole('button', { name: '合成预览', exact: true }).click();
  await expect(page.getByRole('region', { name: '实际成片播放' })).toBeVisible();
  await page.getByRole('button', { name: '剪辑预览', exact: true }).click();
  await page.getByRole('button', { name: '导出成片', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '导出本集成片', exact: true });
  await expect(dialog).toContainText('1 个片段');
  await dialog.getByRole('button', { name: '开始合成', exact: true }).click();
  await expect.poll(() => data.calls.filter(c => c.path === `${root}/exports` && c.method === 'POST').length).toBe(1);
  expect(data.calls.filter(c => c.path === root && c.method === 'PATCH')).toHaveLength(0);
  expect(data.state.clips).toHaveLength(2);
  expect(data.state.clips[0]).toEqual(reserved);
  expect(data.state.clips[1]).toMatchObject({ trim_in_ms: 0, trim_out_ms: 3000, included: true });
  expect(data.errors).toEqual([]);
});

test('preview survives a temporary polling failure and automatically shows the result', async ({ page }) => {
  const data = await fixture(page); data.controls.previewFailures = 1;
  await page.goto('/e2e/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '合成预览', exact: true }).click();
  await expect(page.getByText(/正在自动重新连接/)).toBeVisible();
  await expect(page.getByRole('region', { name: '实际成片播放' })).toBeVisible();
  await expect(page.getByText('已合成预览 · 当前保存版本')).toBeVisible();
  expect(data.calls.filter(c => c.path === `${root}/previews` && c.method === 'POST')).toHaveLength(1);
  expect(data.errors).toEqual([]);
});

test('export failure stays visible even while draft saves are paused', async ({ page }) => {
  const data = await fixture(page); data.state.jobs = [job('301')]; data.controls.saveFails = true;
  await page.goto('/e2e/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '片段静音', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('当前编辑保留在本页');
  Object.assign(data.state.jobs[0], { status: 'failed', stage: 'failed', error: { message: '测试导出失败：磁盘空间不足' } });
  await expect(page.getByText('最近一次导出失败', { exact: true })).toBeVisible({ timeout: 10000 });
  await expect(page.getByText('测试导出失败：磁盘空间不足')).toBeVisible();
  await expect(page.getByRole('button', { name: '恢复原声', exact: true })).toBeVisible();
  expect(data.errors).toEqual([]);
});

test('export history refreshes and a completed result is available for playback, download and adoption', async ({ page }) => {
  const data = await fixture(page); data.state.jobs = [job('301')];
  await page.goto('/e2e/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '导出记录', exact: true }).click();
  const history = page.getByRole('dialog', { name: '导出记录', exact: true });
  await expect(history.getByText('合成片段', { exact: true })).toBeVisible();
  Object.assign(data.state.jobs[0], job('301', 'export', 'succeeded'));
  await expect(history.getByRole('button', { name: '播放', exact: true })).toBeVisible({ timeout: 10000 });
  await history.getByRole('button', { name: '关闭弹窗', exact: true }).click();
  const result = page.getByRole('region', { name: '最近导出结果' });
  await expect(result.getByRole('link', { name: '下载导出 MP4' })).toHaveAttribute('href', `/api/v1${root}/exports/301/download`);
  await captureLayouts(page, 'completed-export');
  await result.getByRole('button', { name: '播放导出结果', exact: true }).click();
  await expect(page.getByRole('region', { name: '实际成片播放' })).toBeVisible();
  await result.getByRole('button', { name: '设为当前成片', exact: true }).click();
  await page.getByRole('dialog', { name: '设为当前成片', exact: true }).getByRole('button', { name: '确认采用', exact: true }).click();
  await expect(page.locator('.assembly-current').filter({ hasText: '当前成片' })).toBeVisible();
  expect(data.state.assembly.current_media_id).toBe('9901');
  expect(data.errors).toEqual([]);
});

test('render submission saves the sound draft before freezing an export while controls are locked', async ({ page }) => {
  const data = await fixture(page, true);
  await page.goto('/e2e/assembly-flow-fixture.html');
  await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await page.getByRole('tab', { name: '字幕 · 0' }).click();
  await page.getByRole('button', { name: '添加字幕', exact: true }).click();
  await page.getByLabel('字幕文本').fill('测试字幕应进入导出快照');
  // Exercise the render boundary with an open sound draft; action() locks its UI
  // before flushing, so a persistence check against disabled would lose this save.
  await page.getByRole('button', { name: '导出成片', exact: true }).evaluate(button => (button as HTMLButtonElement).click());
  const dialog = page.getByRole('dialog', { name: '导出本集成片', exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: '开始合成', exact: true }).click();
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
  await page.goto('/e2e/assembly-flow-fixture.html');
  await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
  await page.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await page.getByRole('tab', { name: '字幕 · 0' }).click();
  await page.getByRole('button', { name: '添加字幕', exact: true }).click();
  await page.getByLabel('字幕文本').fill('声音保存会更新成片版本');
  await page.getByRole('checkbox', { name: '已核对当前剪辑的声音与字幕时间' }).check();

  const deferred = [0, 1].map(() => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    return { gate, release, version: '', received: false, delivered: false };
  });
  let heldGets = 0;
  await page.route(`**/api/v1${root}`, async route => {
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
