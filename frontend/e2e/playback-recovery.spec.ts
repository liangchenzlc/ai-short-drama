import { test, expect, type Page } from '@playwright/test';

const job = { id: '1', media_id: '2', status: 'succeeded', kind: 'preview',
  url: '/.runtime/timeline-fixture.mp4?fresh=1', duration_ms: 3000, context_hash: 'test', is_stale: false,
  stage: 'done', progress: 100, cancel_requested: false, created_at: '', finished_at: '', error: null };
const player = (page: Page) => page.getByRole('region', { name: '实际成片播放' });
async function open(page: Page) {
  await page.goto('/e2e/recovery-fixture.html');
  await expect(player(page)).toHaveAttribute('data-playback-state', 'ready');
  await page.locator('video').evaluate(v => { v.currentTime = 1.2; v.volume = .4; v.muted = true; v.playbackRate = 1.5; });
  await expect(page.getByLabel('当前帧')).toHaveText('36');
}
async function damage(page: Page) {
  // A real failing request resets currentTime, unlike a synthetic error alone.
  await page.locator('video').evaluate(v => { v.src = '/missing-recovery-video.mp4'; v.load(); });
  await expect(player(page)).toHaveAttribute('data-playback-state', 'error');
}

test('real failure, stale canplay and rapid retry retain position and controls', async ({ page }) => {
  let calls = 0;
  await page.route('**/recovery/job', async route => { calls++; await route.fulfill({ json: job }); });
  await open(page);
  for (let index = 0; index < 10; index++) {
    await damage(page);
    await page.locator('video').evaluate(v => { v.dispatchEvent(new Event('canplay')); v.dispatchEvent(new Event('playing')); });
    await expect(player(page)).toHaveAttribute('data-playback-state', 'error');
    await page.getByRole('button', { name: '刷新成片并重试', exact: true }).evaluate(button => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click(); });
    await expect(player(page)).toHaveAttribute('data-playback-state', 'ready');
    const state = await page.locator('video').evaluate(v => ({ time: v.currentTime, volume: v.volume, muted: v.muted, rate: v.playbackRate, paused: v.paused }));
    expect(Math.abs(state.time - 1.2)).toBeLessThanOrEqual(1 / 30);
    expect(state).toMatchObject({ volume: .4, muted: true, rate: 1.5, paused: true });
    expect(calls).toBe(index + 1);
    await expect(page.locator('video')).toHaveCount(1);
  }
});

test('refresh failure and mismatched media cannot replace the original result', async ({ page }) => {
  let calls = 0;
  await page.route('**/recovery/job', route => {
    calls++;
    return calls === 1 ? route.fulfill({ status: 503 }) : route.fulfill({ json: calls === 2 ? { ...job, media_id: '99' } : job });
  });
  await open(page); await damage(page);
  await page.getByRole('button', { name: '刷新成片并重试', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('刷新成片失败');
  await page.getByRole('button', { name: '刷新成片并重试', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('原成片暂不可用');
  await page.getByRole('button', { name: '刷新成片并重试', exact: true }).click();
  await expect(player(page)).toHaveAttribute('data-playback-state', 'ready');
  await expect(page.getByLabel('当前帧')).toHaveText('36');
});

test('a stalled refresh times out and leaving aborts its request', async ({ page }) => {
  await page.route('**/recovery/job', () => {});
  await open(page); await damage(page);
  await page.clock.install();
  await page.getByRole('button', { name: '刷新成片并重试', exact: true }).click();
  await page.clock.fastForward(21000);
  await expect(page.getByRole('alert')).toContainText('成片加载超时');
  await page.getByRole('button', { name: '刷新成片并重试', exact: true }).click();
  await page.getByRole('button', { name: '离开播放器' }).click();
  await expect(page.locator('video')).toHaveCount(0);
});

test('narrow screen exposes the error, retry and download without overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page); await damage(page);
  await expect(page.getByRole('button', { name: '刷新成片并重试', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '下载 MP4' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '.runtime/playback-recovery-mobile.png', fullPage: true });
});
