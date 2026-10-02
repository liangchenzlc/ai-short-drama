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
  const sound = { mode: 'native', row_version: 1, timeline_hash: hash, duration_ms: 3000, needs_review: false, stale_lines: [],
    document: { dialogue: [], subtitles: [] as { start_ms: number; end_ms: number; text: string }[], native_ducking: [], music: null, original_volume: 1, dialogue_volume: 1, burn_subtitles: true, font_size: 24 },
    media: {}, uploads: [], voice_defaults: { row_version: 1, voices: {} } };
  const controls = { online: true, previewFailures: 0, previewFinish: true, saveFails: false };
  const calls: { path: string; method: string; body: any }[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname.slice('/api/v1'.length), method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ path, method, body });
    const reply = (json: any, status = 200) => route.fulfill({ json, status });
    if (path === `${soundRoot}/capabilities`) return reply({ enabled: soundEnabled });
    if (path === soundRoot) {
      if (method === 'PUT') { sound.document = body.document; sound.row_version++; state.assembly.row_version = String(Number(state.assembly.row_version) + 1); }
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
    if ((path === `${root}/exports` || path === `${root}/previews`) && method === 'POST') {
      const created = job(path.endsWith('previews') ? '201' : '301', path.endsWith('previews') ? 'preview' : 'export');
      state.jobs.unshift(created); return reply(created, 202);
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
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath(`${name}-${viewport.width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
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
