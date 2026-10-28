import { test, expect } from '@playwright/test';

for (const scene of ['shot_image', 'shot_video'] as const) test(`${scene}: selection, result explanation and original-key recovery`, async ({ page }, info) => {
  const bodies: Record<string, unknown>[] = [], keys: string[] = [], preflights: Record<string, unknown>[] = [];
  const job = { id: '900', scene, config_id: scene === 'shot_video' ? '10' : '9', scope: { scope: { library: 'episode', project_id: '1', episode_id: '2' } }, status: 'running', counts: { waiting: 1 }, total: 1, created_at: '' };
  let resumes = 0;
  const task = { generation_id: '902', service_type: scene === 'shot_video' ? 'video' : 'image', status: 'failed', can_cancel: false, can_retry: false, can_resume: true, created_at: '', config: null, input: {}, parameters: {}, result: { assets: [], text: null, partial: false } };
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.endsWith('/capabilities')) return route.fulfill({ json: { enabled: true } });
    if (url.pathname.endsWith('/ai-model-configs')) {
      const items = ['image', 'video'].map((kind, index) => ({ id: String(9 + index), name: `${kind === 'image' ? '图片' : '视频'}验收模型`, model_key: 'fixture', provider: 'ark', service_type: kind, enabled: 1, is_default: 1, row_version: '1' }))
        .filter(item => !url.searchParams.get('service_type') || item.service_type === url.searchParams.get('service_type'));
      return route.fulfill({ json: { items, total: items.length } });
    }
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
    if (url.pathname.endsWith('/ai/generations/902/records')) return route.fulfill({ json: { items: [] } });
    if (url.pathname.endsWith('/ai/generations/902/resume')) {
      resumes++;
      return resumes === 1 ? route.fulfill({ status: 503, json: { error: { code: 'OFFLINE', message: '恢复服务暂不可用，请重试。' } } }) : route.fulfill({ json: task });
    }
    if (url.pathname.endsWith('/ai/generations/902')) return route.fulfill({ json: task });
    throw new Error(`Unexpected endpoint: ${request.method()} ${url.pathname}`);
  });
  await page.goto('/e2e/generation/fixtures/batch-fixture.html');
  await page.getByRole('checkbox', { name: '镜头 1', exact: true }).check();
  await page.getByRole('button', { name: '切换列表页' }).click();
  await page.getByRole('checkbox', { name: '镜头 3', exact: true }).check();
  await page.getByRole('button', { name: '批量生成所选' }).click();
  if (scene === 'shot_video') {
    await page.getByRole('combobox', { name: '生成类型', exact: true }).locator('xpath=../..').click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '分镜视频' }).click();
  }
  await expect(page.getByRole('dialog')).toContainText(scene === 'shot_video' ? '成功归档后自动设为镜头视频，可播放核对。' : '图片直接保存，第一张默认主图');
  await page.getByRole('combobox', { name: '模型配置' }).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: scene === 'shot_video' ? '视频验收模型' : '图片验收模型' }).click();
  await page.getByRole('button', { name: '检查可执行项' }).click();
  await expect(page.getByText('请先采用分镜图片', { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('batch-preflight.png'), animations: 'disabled' });
  expect(preflights[0].source_ids).toEqual(['1', '3']);
  await expect(page.getByLabel('保存次数')).toHaveText('1');
  const formBox = await page.locator('.batch-form').boundingBox();
  await page.getByRole('button', { name: /确认并启动 1 项生成/ }).click();
  const preflightHost = page.locator('.standard-creation-drawer .studio-feedback-host');
  await expect(preflightHost.getByRole('alert')).toBeVisible();
  expect(await page.locator('.batch-form').boundingBox()).toEqual(formBox);
  await preflightHost.getByRole('button', { name: '关闭提示', exact: true }).click();
  await preflightHost.getByRole('button', { name: /^提示详情：/ }).click();
  await expect(preflightHost.locator('.studio-feedback-details')).toContainText('提交结果不明时保留当前预检直接重试');
  await page.keyboard.press('Escape');
  await expect(preflightHost.locator('.studio-feedback-popup')).toBeHidden();
  await page.getByRole('button', { name: /确认并启动 1 项生成/ }).click();
  await expect(page.getByRole('heading', { name: `${scene === 'shot_video' ? '分镜视频' : '分镜图片'} · 执行中` })).toBeVisible();
  await expect(page.getByRole('dialog')).toContainText(scene === 'shot_video' ? '视频成功归档后自动确定，可播放核对。' : '图片直接保存，请核对主图。');
  await expect(page.getByRole('link', { name: scene === 'shot_video' ? '打开来源作品审核视频' : '打开来源作品核对图片', exact: true })).toBeVisible();
  expect(bodies[0].accepted_ids).toEqual(['1']);
  expect(bodies[0]).toEqual(bodies[1]);
  expect(keys[0]).toBeTruthy(); expect(keys[0]).toBe(keys[1]);
  await expect.poll(async () => Math.round((await page.getByRole('dialog').boundingBox())!.x)).toBe(560);
  await page.screenshot({ path: info.outputPath('batch-progress-desktop.png'), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 1280, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect.poll(async () => (await page.getByRole('dialog').boundingBox())!.x).toBeGreaterThanOrEqual(-1);
  await expect.poll(async () => Math.round((await page.getByRole('dialog').boundingBox())!.width)).toBeLessThanOrEqual(1280);
  const refresh = await page.getByRole('button', { name: '刷新进度' }).boundingBox();
  expect(refresh!.x + refresh!.width).toBeLessThanOrEqual(1280);
  await page.screenshot({ path: info.outputPath('batch-progress-desktop-1280.png'), fullPage: true, animations: 'disabled' });
  job.status = 'needs_review';
  await page.getByRole('button', { name: '刷新进度', exact: true }).click();
  const progress = page.getByRole('dialog', { name: /^批量生成进度/ });
  await progress.getByRole('button', { name: /^提示详情：/ }).click();
  await expect(page.locator('.studio-feedback-details')).toContainText('有任务受理结果不明，后续派发已暂停');
  await page.keyboard.press('Escape');
  await expect(page.locator('.studio-feedback-popup')).toBeHidden();
  await progress.getByRole('button', { name: '任务与结果', exact: true }).click();
  const detail = page.getByRole('dialog', { name: /^任务详情/ });
  await detail.getByRole('button', { name: '安全恢复', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: /^确认安全恢复/ });
  const confirmationBody = confirmation.locator('.ant-modal-body');
  await confirmation.getByRole('button', { name: '恢复任务', exact: true }).hover();
  const confirmationBox = await confirmationBody.boundingBox();
  await confirmation.getByRole('button', { name: '恢复任务', exact: true }).click();
  await expect(confirmation.getByRole('alert')).toHaveText(/服务暂时不可用/);
  expect(await confirmationBody.boundingBox()).toEqual(confirmationBox);
  await confirmation.getByRole('button', { name: '关闭提示', exact: true }).click();
  await confirmation.getByRole('button', { name: /^提示详情：/ }).click();
  await expect(confirmation.locator('.studio-feedback-details')).toContainText('服务暂时不可用');
  await page.keyboard.press('Escape');
  await expect(confirmation.locator('.studio-feedback-popup')).toBeHidden();
  await expect(confirmation).toBeVisible();
  const recover = confirmation.getByRole('button', { name: /恢复任务$/ });
  await expect(recover).not.toHaveClass(/ant-btn-loading/);
  await recover.click();
  await expect(confirmation).toBeHidden();
  await expect(page.locator('.generation-drawer .studio-message')).toContainText('已提交安全恢复请求');
  await expect(detail.locator('.ant-alert')).toHaveCount(0);
  expect(resumes).toBe(2);
});
