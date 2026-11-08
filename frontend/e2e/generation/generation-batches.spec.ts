import { test, expect } from '@playwright/test';

test('compact 101项分批预检，受理不明后恢复并查看各批和重试失败项', async ({ page }, info) => {
  const preflights: string[][] = [], submissions: { body: any; key: string }[] = [], retries: any[] = [];
  const job = (id: string, status: string) => ({
    id, scene: 'shot_image', config_id: '9', scope: { scope: { library: 'project', project_id: '1' } },
    status, can_control: true, can_cancel: true, counts: { [status]: 1 }, total: 1, created_at: '',
  });
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.endsWith('/capabilities')) return route.fulfill({ json: { enabled: true } });
    if (url.pathname.endsWith('/ai-model-configs')) return route.fulfill({ json: { items: [
      { id: '9', name: '图片验收模型', model_key: 'fixture', provider: 'ark', service_type: 'image', enabled: 1, is_default: 1, row_version: '1' },
    ], total: 1 } });
    if (url.pathname.endsWith('/preflight')) {
      const ids = request.postDataJSON().source_ids as string[];
      preflights.push(ids);
      if (ids.length > 100) return route.fulfill({ status: 422, json: { error: { code: 'batch_limit', message: '单批最多100项' } } });
      return route.fulfill({ json: { preflight_hash: ids[0].padStart(64, '0'), task_count: ids.length, output_count: ids.length, concurrency: 2,
        items: ids.map(id => ({ source_id: id, name: `镜头 ${id}`, state: 'ready', reason: '', task_id: null, parameters: {} })) } });
    }
    if (url.pathname.endsWith('/generation-batches') && request.method() === 'POST') {
      submissions.push({ body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
      if (submissions.length === 2) return route.abort('failed');
      return route.fulfill({ json: job(submissions.length === 1 ? '900' : '901', submissions.length === 1 ? 'failed' : 'needs_review') });
    }
    if (url.pathname.endsWith('/900/retry-failed')) {
      retries.push({ body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
      if (retries.length === 1) return route.abort('failed');
      return route.fulfill({ json: job('902', 'running') });
    }
    const id = url.pathname.match(/\/generation-batches\/(900|901|902)$/)?.[1];
    if (id) {
      const status = id === '900' ? 'failed' : id === '901' ? 'needs_review' : 'running';
      return route.fulfill({ json: { ...job(id, status), offset: 0, limit: 20, items: [{
        id: `item-${id}`, name: `镜头 ${id === '900' ? 1 : 101}`, source_id: id === '900' ? '1' : '101', task_id: `task-${id}`,
        status, error: null, task: { status, can_retry: id === '900', can_resume: id === '901' },
      }] } });
    }
    throw new Error(`Unexpected endpoint: ${request.method()} ${url.pathname}`);
  });
  await page.goto('/e2e/generation/fixtures/batch-fixture.html');
  await page.getByRole('button', { name: '选择101项' }).click();
  await page.getByRole('button', { name: '批量生成分镜图' }).click();
  await page.getByRole('combobox', { name: '模型配置' }).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '图片验收模型' }).click();
  await page.getByRole('button', { name: '检查可执行项' }).click();
  await expect(page.getByRole('button', { name: '确认并启动 101 项生成' })).toBeVisible();
  expect(preflights.map(ids => ids.length)).toEqual([100, 1]);
  expect(preflights[0]).toEqual(Array.from({ length: 100 }, (_, index) => String(index + 1)));
  expect(preflights[1]).toEqual(['101']);
  await page.getByRole('button', { name: '确认并启动 101 项生成' }).click();
  await expect(page.getByText(/第 2 批.*受理.*待核对/)).toBeVisible();
  await page.screenshot({ path: info.outputPath('batch-submission-1440.png'), animations: 'disabled' });
  expect(submissions.map(call => call.body.accepted_ids.length)).toEqual([100, 1]);
  expect(submissions[0].key).not.toBe(submissions[1].key);
  await page.getByRole('button', { name: '查看第 1 批' }).click();
  await expect(page.getByRole('heading', { name: '分镜图片 · 失败' })).toBeVisible();
  await page.getByRole('button', { name: '返回提交' }).click();
  await page.reload();
  await expect(page.getByRole('button', { name: '批量生成分镜图' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '继续核对批次' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('batch-recovery-entry-1440.png'), animations: 'disabled' });
  await page.getByRole('checkbox', { name: '镜头 1', exact: true }).check();
  await expect(page.getByRole('button', { name: '批量生成分镜图' })).toBeDisabled();
  await page.getByRole('checkbox', { name: '镜头 1', exact: true }).uncheck();
  await page.getByRole('button', { name: '继续核对批次' }).click();
  await expect(page.getByText(/第 2 批.*受理.*待核对/)).toBeVisible();
  await page.getByRole('button', { name: '继续提交' }).click();
  await expect(page.getByRole('heading', { name: '分镜图片 · 失败' })).toBeVisible();
  expect(submissions).toHaveLength(3);
  expect(submissions[2]).toEqual(submissions[1]);
  await page.getByRole('combobox', { name: '查看批次' }).locator('xpath=../..').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '第 2 批' }).click();
  await expect(page.getByRole('heading', { name: '分镜图片 · 受理待核对' })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('batch-groups-1280.png'), animations: 'disabled' });
  await page.getByRole('combobox', { name: '查看批次' }).locator('xpath=../..').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '第 1 批' }).click();
  await page.locator('.ant-table-row').getByRole('checkbox').check();
  await page.getByRole('button', { name: '重新生成所选失败项' }).click();
  await page.getByRole('button', { name: '确认继续' }).click();
  await expect(page.getByText('重试请求受理待核对，请使用原请求继续核对。')).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '继续核对批次' }).click();
  await expect(page.getByText('重试请求受理待核对，请使用原请求继续核对。')).toBeVisible();
  await page.getByRole('button', { name: '继续核对重试' }).click();
  await page.getByRole('button', { name: '确认继续' }).click();
  await expect(page.getByRole('heading', { name: '分镜图片 · 执行中' })).toBeVisible();
  expect(retries[0].body).toEqual({ item_ids: ['item-900'] });
  expect(retries[0].key).toBeTruthy();
  expect(retries[1]).toEqual(retries[0]);
  await page.getByRole('combobox', { name: '查看批次' }).locator('xpath=../..').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '第 1 批' }).click();
  await expect(page.getByRole('heading', { name: '分镜图片 · 失败' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: '批量生成分镜图' })).toBeDisabled();
  await page.getByRole('button', { name: '查看上次批次' }).click();
  await expect(page.getByRole('heading', { name: '分镜图片 · 失败' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: /^批量生成进度/ })).toBeHidden();
  await page.getByRole('checkbox', { name: '镜头 1', exact: true }).check();
  await expect(page.getByRole('button', { name: '查看上次批次' })).toBeVisible();
  await page.getByRole('button', { name: '批量生成分镜图' }).click();
  await expect(page.getByRole('dialog', { name: /^批量生成预检/ })).toContainText('已选择 1 项');
  expect(submissions).toHaveLength(3);
  await page.getByRole('dialog').getByRole('button', { name: 'Close' }).click();
  await page.getByRole('button', { name: '查看上次批次' }).click();
  await expect(page.getByRole('heading', { name: '分镜图片 · 失败' })).toBeVisible();
  await page.getByRole('button', { name: '新建批量生成' }).click();
  await expect(page.getByRole('dialog', { name: /^批量生成预检/ })).toContainText('已选择 1 项');
  expect(submissions).toHaveLength(3);
});

test('素材筛选重置后仍能恢复原批次及幂等标识', async ({ page }) => {
  const submissions: { body: any; key: string }[] = [];
  const job = { id: '900', scene: 'asset_image', config_id: '9', scope: { scope: { library: 'project', project_id: '1', asset_kind: 'character', search: '角色' } },
    status: 'running', counts: { waiting: 1 }, total: 1, created_at: '' };
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.endsWith('/capabilities')) return route.fulfill({ json: { enabled: true } });
    if (url.pathname.endsWith('/ai-model-configs')) return route.fulfill({ json: { items: [
      { id: '9', name: '图片验收模型', model_key: 'fixture', provider: 'ark', service_type: 'image', enabled: 1, is_default: 1, row_version: '1' },
    ], total: 1 } });
    if (url.pathname.endsWith('/preflight')) return route.fulfill({ json: { preflight_hash: 'a'.repeat(64), task_count: 1, output_count: 1, concurrency: 2,
      items: [{ source_id: '1', name: '角色 1', state: 'ready', reason: '', task_id: null, parameters: {} }] } });
    if (url.pathname.endsWith('/generation-batches') && request.method() === 'POST') {
      submissions.push({ body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
      return submissions.length === 1 ? route.abort('failed') : route.fulfill({ json: job });
    }
    if (url.pathname.endsWith('/generation-batches/900')) return route.fulfill({ json: { ...job, items: [], offset: 0, limit: 20 } });
    throw new Error(`Unexpected endpoint: ${request.method()} ${url.pathname}`);
  });
  await page.goto('/e2e/generation/fixtures/batch-fixture.html');
  await page.getByRole('button', { name: '筛选素材' }).click();
  await page.getByRole('checkbox', { name: '镜头 1', exact: true }).check();
  await page.getByRole('button', { name: '批量生成图片' }).click();
  await page.getByRole('combobox', { name: '模型配置' }).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '图片验收模型' }).click();
  await page.getByRole('button', { name: '检查可执行项' }).click();
  await page.getByRole('button', { name: '确认并启动 1 项生成' }).click();
  await expect(page.getByText(/第 1 批.*受理.*待核对/)).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: 'Close' }).click();
  await page.getByRole('button', { name: '切换作品' }).click();
  await expect(page.getByRole('button', { name: '继续核对批次' })).toHaveCount(0);
  await page.getByRole('button', { name: '切换作品' }).click();
  await expect(page.getByRole('button', { name: '继续核对批次' })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '继续核对批次' }).click({ timeout: 5000 });
  await page.getByRole('button', { name: '继续提交' }).click();
  await expect(page.getByRole('heading', { name: '素材图片 · 执行中' })).toBeVisible();
  expect(submissions).toHaveLength(2);
  expect(submissions[0].body.scope.search).toBe('角色');
  expect(submissions[1]).toEqual(submissions[0]);
});

test('预检冲突只重新检查未提交的批次', async ({ page }) => {
  const preflights: string[][] = [], submissions: { body: any; key: string }[] = [];
  const job = (id: string) => ({ id, scene: 'shot_image', config_id: '9', scope: { scope: { library: 'project', project_id: '1' } },
    status: 'running', counts: { waiting: 1 }, total: 1, created_at: '' });
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.endsWith('/capabilities')) return route.fulfill({ json: { enabled: true } });
    if (url.pathname.endsWith('/ai-model-configs')) return route.fulfill({ json: { items: [
      { id: '9', name: '图片验收模型', model_key: 'fixture', provider: 'ark', service_type: 'image', enabled: 1, is_default: 1, row_version: '1' },
    ], total: 1 } });
    if (url.pathname.endsWith('/preflight')) {
      const ids = request.postDataJSON().source_ids as string[];
      preflights.push(ids);
      return route.fulfill({ json: { preflight_hash: String(preflights.length).repeat(64), task_count: ids.length, output_count: ids.length, concurrency: 2,
        items: ids.map(id => ({ source_id: id, name: `镜头 ${id}`, state: 'ready', reason: '', task_id: null, parameters: {} })) } });
    }
    if (url.pathname.endsWith('/generation-batches') && request.method() === 'POST') {
      submissions.push({ body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
      if (submissions.length === 2) return route.fulfill({ status: 409, json: { error: { code: 'batch_preflight_changed', message: '预检后来源已变化，请重新预检' } } });
      return route.fulfill({ json: job(submissions.length === 1 ? '900' : '901') });
    }
    if (url.pathname.endsWith('/generation-batches/900')) return route.fulfill({ json: { ...job('900'), items: [], offset: 0, limit: 20 } });
    throw new Error(`Unexpected endpoint: ${request.method()} ${url.pathname}`);
  });
  await page.goto('/e2e/generation/fixtures/batch-fixture.html');
  await page.getByRole('button', { name: '选择101项' }).click();
  await page.getByRole('button', { name: '批量生成分镜图' }).click();
  await page.getByRole('combobox', { name: '模型配置' }).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '图片验收模型' }).click();
  await page.getByRole('button', { name: '检查可执行项' }).click();
  await page.getByRole('button', { name: '确认并启动 101 项生成' }).click();
  await page.getByRole('button', { name: '重新预检未提交项' }).click();
  await expect(page.getByRole('button', { name: '确认并继续提交 1 项' })).toBeVisible();
  expect(preflights.map(ids => ids.length)).toEqual([100, 1, 1]);
  await page.getByRole('button', { name: '确认并继续提交 1 项' }).click();
  await expect(page.getByRole('heading', { name: '分镜图片 · 执行中' })).toBeVisible();
  expect(submissions.map(call => call.body.accepted_ids.length)).toEqual([100, 1, 1]);
  expect(submissions[2].body.accepted_ids).toEqual(['101']);
  expect(submissions[2].body.preflight_hash).not.toBe(submissions[1].body.preflight_hash);
  expect(submissions[2].key).not.toBe(submissions[1].key);
});

for (const scene of ['asset_image', 'shot_video'] as const) test(`${scene}: compact入口按预检结果分批提交`, async ({ page }) => {
  const preflights: any[] = [], submissions: any[] = [];
  const job = (id: string) => ({ id, scene, config_id: '9', scope: { scope: { library: 'project', project_id: '1' } },
    status: 'running', counts: { waiting: 1 }, total: 1, created_at: '' });
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.endsWith('/capabilities')) return route.fulfill({ json: { enabled: true } });
    if (url.pathname.endsWith('/ai-model-configs')) return route.fulfill({ json: { items: [
      { id: '9', name: '验收模型', model_key: 'fixture', provider: 'ark', service_type: scene === 'shot_video' ? 'video' : 'image', enabled: 1, is_default: 1, row_version: '1' },
    ], total: 1 } });
    if (url.pathname.endsWith('/preflight')) {
      const body = request.postDataJSON(), ids = body.source_ids as string[];
      preflights.push(body);
      return route.fulfill({ json: { preflight_hash: ids[0].padStart(64, '0'), task_count: ids.length, output_count: ids.length, concurrency: 2,
        items: ids.map(id => ({ source_id: id, name: `对象 ${id}`, state: id === '100' ? 'blocked' : 'ready', reason: id === '100' ? '不可生成' : '', task_id: null, parameters: {} })) } });
    }
    if (url.pathname.endsWith('/generation-batches') && request.method() === 'POST') {
      submissions.push(request.postDataJSON());
      return route.fulfill({ json: job(String(900 + submissions.length)) });
    }
    if (url.pathname.endsWith('/generation-batches/901')) return route.fulfill({ json: { ...job('901'), items: [], offset: 0, limit: 20 } });
    throw new Error(`Unexpected endpoint: ${request.method()} ${url.pathname}`);
  });
  await page.goto('/e2e/generation/fixtures/batch-fixture.html');
  await page.getByRole('button', { name: '选择101项' }).click();
  await page.getByRole('button', { name: scene === 'asset_image' ? '批量生成图片' : '批量生成视频' }).click();
  if (scene === 'asset_image') await expect(page.getByRole('dialog')).toContainText('生成图片保存为候选，请在来源作品中核对并主动设为主图。');
  await page.getByRole('combobox', { name: '模型配置' }).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '验收模型' }).click();
  await page.getByRole('button', { name: '检查可执行项' }).click();
  await expect(page.getByText('不可生成')).toBeVisible();
  await page.getByRole('button', { name: '确认并启动 100 项生成' }).click();
  await expect(page.getByRole('heading', { name: `${scene === 'asset_image' ? '素材图片' : '分镜视频'} · 执行中` })).toBeVisible();
  if (scene === 'asset_image') await expect(page.getByRole('dialog')).toContainText('生成图片保存为候选，请在来源作品中核对并主动设为主图。');
  expect(preflights.map(body => body.source_ids.length)).toEqual([100, 1]);
  expect(preflights.map(body => body.scene)).toEqual([scene, scene]);
  expect(submissions.map(body => body.accepted_ids.length)).toEqual([99, 1]);
  expect(submissions[1].accepted_ids).toEqual(['101']);
  await expect(page.getByLabel('保存次数')).toHaveText('1');
});

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
  await expect(page.getByRole('dialog')).toContainText(scene === 'shot_video' ? '视频保存为候选，请在来源作品中核对并主动采用。' : '生成图片保存为候选，请在来源作品中核对并主动设为主图。');
  await page.getByRole('combobox', { name: '模型配置' }).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: scene === 'shot_video' ? '视频验收模型' : '图片验收模型' }).click();
  await page.getByRole('button', { name: '检查可执行项' }).click();
  await expect(page.getByText('请先采用分镜图片', { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('batch-preflight.png'), animations: 'disabled' });
  expect(preflights[0].source_ids).toEqual(['1', '3']);
  await expect(page.getByLabel('保存次数')).toHaveText('1');
  await page.getByRole('button', { name: /确认并启动 1 项生成/ }).click();
  const preflightHost = page.locator('.standard-creation-drawer .studio-feedback-host');
  await expect(preflightHost.getByRole('alert')).toBeVisible();
  const formBox = await page.locator('.batch-form').boundingBox();
  await preflightHost.getByRole('button', { name: '关闭提示', exact: true }).click();
  expect(await page.locator('.batch-form').boundingBox()).toEqual(formBox);
  await preflightHost.getByRole('button', { name: /^提示详情：/ }).click();
  await expect(preflightHost.locator('.studio-feedback-details')).toContainText('提交结果不明时保留当前预检直接重试');
  await page.keyboard.press('Escape');
  await expect(preflightHost.locator('.studio-feedback-popup')).toBeHidden();
  await page.getByRole('button', { name: '继续提交' }).click();
  await expect(page.getByRole('heading', { name: `${scene === 'shot_video' ? '分镜视频' : '分镜图片'} · 执行中` })).toBeVisible();
  await expect(page.getByRole('dialog')).toContainText(scene === 'shot_video' ? '视频保存为候选，请在来源作品中核对并主动采用。' : '生成图片保存为候选，请在来源作品中核对并主动设为主图。');
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
  await expect.poll(async () => Math.abs((await confirmationBody.boundingBox())!.width - await confirmationBody.evaluate(node => node.offsetWidth))).toBeLessThan(1);
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
