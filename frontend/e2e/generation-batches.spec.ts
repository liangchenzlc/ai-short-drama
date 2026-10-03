import { test, expect } from '@playwright/test';

test('selection spans pages; preflight excludes blocked items and lost receipt reuses its key', async ({ page }, info) => {
  const bodies: Record<string, unknown>[] = [], keys: string[] = [], preflights: Record<string, unknown>[] = [];
  const job = { id: '900', scene: 'shot_image', config_id: '9', scope: { scope: { library: 'episode', project_id: '1', episode_id: '2' } }, status: 'running', counts: { waiting: 1 }, total: 1, created_at: '' };
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.endsWith('/capabilities')) return route.fulfill({ json: { enabled: true } });
    if (url.pathname.endsWith('/ai-model-configs')) return route.fulfill({ json: { items: [{ id: '9', name: '验收模型', model_key: 'fixture', provider: 'ark', service_type: 'image', enabled: 1, is_default: 1, row_version: '1' }], total: 1 } });
    if (url.pathname.endsWith('/preflight')) {
      preflights.push(request.postDataJSON());
      return route.fulfill({ json: { preflight_hash: 'a'.repeat(64), task_count: 1, output_count: 1, concurrency: 2, items: [
        { source_id: '1', name: '镜头1', state: 'ready', reason: '', parameters: { resolution: '2K' } },
        { source_id: '3', name: '镜头3', state: 'blocked', reason: '请先采用分镜图片' },
      ] } });
    }
    if (url.pathname.endsWith('/generation-batches') && request.method() === 'POST') {
      bodies.push(request.postDataJSON()); keys.push(request.headers()['idempotency-key']);
      return keys.length === 1 ? route.abort('failed') : route.fulfill({ json: job });
    }
    if (url.pathname.endsWith('/generation-batches/900')) return route.fulfill({ json: { ...job, items: [{ id: '901', name: '镜头1', source_id: '1', task_id: '902', status: 'waiting', error: null, task: { status: 'queued', can_retry: false, can_resume: false } }], offset: 0, limit: 20 } });
    throw new Error(`Unexpected endpoint: ${request.method()} ${url.pathname}`);
  });
  await page.goto('/e2e/batch-fixture.html');
  await page.getByRole('checkbox', { name: '镜头 1', exact: true }).check();
  await page.getByRole('button', { name: '切换列表页' }).click();
  await page.getByRole('checkbox', { name: '镜头 3', exact: true }).check();
  await page.getByRole('button', { name: '批量生成所选' }).click();
  await page.getByRole('combobox', { name: '模型配置' }).click();
  await page.getByText('验收模型 · fixture（默认）', { exact: true }).click();
  await page.getByRole('button', { name: '检查可执行项' }).click();
  await expect(page.getByText('请先采用分镜图片', { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('batch-preflight.png'), animations: 'disabled' });
  expect(preflights[0].source_ids).toEqual(['1', '3']);
  await expect(page.getByLabel('保存次数')).toHaveText('1');
  await page.getByRole('button', { name: /确认并启动 1 项生成/ }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await page.getByRole('button', { name: /确认并启动 1 项生成/ }).click();
  await expect(page.getByRole('heading', { name: '分镜图片 · 执行中' })).toBeVisible();
  expect(bodies[0].accepted_ids).toEqual(['1']);
  expect(bodies[0]).toEqual(bodies[1]);
  expect(keys[0]).toBeTruthy(); expect(keys[0]).toBe(keys[1]);
  await expect.poll(async () => Math.round((await page.getByRole('dialog').boundingBox())!.x)).toBe(560);
  await page.screenshot({ path: info.outputPath('batch-progress-desktop.png'), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect.poll(async () => (await page.getByRole('dialog').boundingBox())!.x).toBeGreaterThanOrEqual(-1);
  await expect.poll(async () => Math.round((await page.getByRole('dialog').boundingBox())!.width)).toBeLessThanOrEqual(390);
  const refresh = await page.getByRole('button', { name: '刷新进度' }).boundingBox();
  expect(refresh!.x + refresh!.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: info.outputPath('batch-progress-mobile.png'), fullPage: true, animations: 'disabled' });
});
