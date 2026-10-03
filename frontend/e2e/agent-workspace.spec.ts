import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';

async function agentFixture(page: Page, enabled = true) {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const conversations: any[] = [];
  const mutations: { method: string; body: any; key?: string }[] = [];
  await page.route('**/api/v1/agent/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.slice('/api/v1/agent'.length);
    const method = request.method();
    const reply = (data: any, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path === '/status') return reply({ enabled, schema_ready: enabled });
    if (path === '/models') return reply({ items: [], preferred_id: null });
    if (/^\/conversations\/\d+\/messages$/.test(path) && method === 'GET') return reply({ items: [], total: 0, offset: 0, limit: 50 });
    if (/^\/conversations\/\d+\/runs$/.test(path)) {
      const item = conversations.find(conversation => path === `/conversations/${conversation.id}/runs`);
      const items = item?.last_run_status ? [{ id: String(400 + Number(item.id)), conversation_id: item.id, status: item.last_run_status, phase: 'finished', row_version: 1, mode: 'discuss', model_config_id: '1', model_name: '文本模型', error: null, usage: {}, budget: {}, review: null, created_at: item.created_at, updated_at: item.updated_at, finished_at: item.updated_at }] : [];
      return reply({ items, total: items.length, offset: 0, limit: 1 });
    }
    if (/^\/conversations\/\d+\/events$/.test(path)) return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (path === '/conversations' && method === 'GET') {
      const items = conversations.filter(item => url.searchParams.get('include_archived') === 'true' || !item.archived);
      const offset = Number(url.searchParams.get('offset') || 0);
      return reply({ items: items.slice(offset, offset + 20), total: items.length, offset, limit: 20 });
    }
    const body = method === 'POST' || method === 'PATCH' ? request.postDataJSON() : undefined;
    if (path === '/conversations' && method === 'POST') {
      mutations.push({ method, body, key: request.headers()['idempotency-key'] });
      const item = { id: String(301 + conversations.length), project_id: '10', episode_id: '20', title: body.title || '新对话', archived: false, row_version: 1, created_at: '2026-10-02T00:00:00Z', updated_at: '2026-10-02T00:00:00Z', last_run_status: null };
      conversations.push(item); return reply(item, 201);
    }
    if (/^\/conversations\/\d+$/.test(path)) {
      const item = conversations.find(conversation => path === `/conversations/${conversation.id}`);
      if (!item) return reply({ error: { code: 'NOT_FOUND' } }, 404);
      if (method === 'PATCH') { mutations.push({ method, body }); expect(typeof body.row_version).toBe('number'); Object.assign(item, body, { row_version: item.row_version + 1 }); }
      return reply(item);
    }
    state.unexpected.push(`${method} agent${path}`);
    return reply({ error: { code: 'UNEXPECTED' } }, 501);
  });
  return { ...state, mutations, conversations };
}

test('episode Agent entry keeps manual drafts, private conversations and stage navigation', async ({ page }, info) => {
  const state = await agentFixture(page);
  await page.goto(`${root}/source`);
  await expect(page.getByRole('textbox', { name: '本集小说正文' })).toBeVisible();
  await page.getByPlaceholder('例如：突出主角冲突，保留关键对白，结尾设置悬念。').fill('保留车站对白');
  await page.getByText('Agent 创作', { exact: true }).first().click();
  await expect(page).toHaveURL(/mode=agent/);
  await expect(page).toHaveURL(/conversation=301/);
  await expect(page.getByRole('heading', { name: '从本集作品开始' })).toBeVisible();
  expect(state.mutations).toHaveLength(1);
  expect(state.mutations[0].body).toEqual({ project_id: '10', episode_id: '20', title: '小说改编：归来的旅人' });
  expect(state.mutations[0].key).toBeTruthy();

  await page.getByRole('button', { name: '对话操作' }).click();
  await page.getByRole('menuitem', { name: '重命名' }).click();
  await page.getByLabel('对话名称').fill('雨夜结尾');
  await page.getByRole('button', { name: '保存名称' }).click();
  await expect(page.locator('.agent-conversation-toolbar .ant-select-selection-item')).toHaveText('雨夜结尾');
  await page.getByRole('navigation', { name: '分集制作流程' }).getByRole('button', { name: /素材准备/ }).click();
  await expect(page).toHaveURL(url => url.pathname.endsWith('/assets') && url.searchParams.get('conversation') === '302');
  expect(state.mutations).toHaveLength(3); // one auto creation per stage plus the explicit rename
  await expect(page.getByRole('heading', { name: '从本集作品开始' })).toBeVisible();
  await page.getByRole('navigation', { name: '分集制作流程' }).getByRole('button', { name: /小说改编/ }).click();
  await page.getByText('提示词创作', { exact: true }).click();
  await expect(page.getByPlaceholder('例如：突出主角冲突，保留关键对白，结尾设置悬念。')).toHaveValue('保留车站对白');
  await page.getByText('Agent 创作', { exact: true }).first().click();
  await page.getByRole('button', { name: '对话操作' }).click();
  await page.getByRole('menuitem', { name: '归档对话' }).click();
  await expect(page.getByRole('heading', { name: '这段对话已归档' })).toBeVisible();
  await page.getByRole('button', { name: '恢复对话', exact: true }).click();
  await expect(page.getByRole('heading', { name: '从本集作品开始' })).toBeVisible();
  await page.reload();
  await expect(page.locator('.agent-conversation-toolbar .ant-select-selection-item')).toHaveText('雨夜结尾');
  await page.screenshot({ path: info.outputPath('agent-source-1440.png'), fullPage: true });
  for (const width of [1280, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect(page.getByRole('separator', { name: '调整对话区域宽度' })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath(`agent-source-${width}.png`), fullPage: true });
  }
  await page.getByRole('separator', { name: '调整对话区域宽度' }).focus();
  await page.keyboard.press('Home');
  await expect(page.getByRole('separator', { name: '调整对话区域宽度' })).toHaveAttribute('aria-valuenow', '360');
  for (const width of [1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByRole('tab', { name: 'AI 创作', exact: true })).toBeVisible();
    await page.getByRole('tab', { name: 'AI 创作', exact: true }).focus();
    await page.keyboard.press('Home');
    await expect(page.getByRole('tab', { name: '作品', exact: true })).toBeFocused();
    await expect(page.getByRole('textbox', { name: '本集小说正文' })).toBeVisible();
    await page.getByRole('tab', { name: 'AI 创作', exact: true }).click();
    await expect(page.getByRole('heading', { name: '从本集作品开始' })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath(`agent-${width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('navigation', { name: '分集制作流程' }).getByRole('button', { name: /成片合成与导出/ }).click();
  await expect(page).toHaveURL(url => url.pathname.endsWith('/assembly') && url.searchParams.get('conversation') === '303');
  await expect(page.getByRole('complementary', { name: 'Agent 创作对话' })).toBeVisible();
  await page.getByRole('navigation', { name: '分集制作流程' }).getByRole('button', { name: /小说改编/ }).click();
  await expect(page).toHaveURL(url => url.pathname.endsWith('/source') && url.searchParams.get('conversation') === '301');
  expect(state.requests.filter(request => request.path === '/ai/generations/text' || request.path === '/ai/generations/image')).toHaveLength(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('disabled Agent feature keeps prompt creation usable', async ({ page }) => {
  const state = await agentFixture(page, false);
  await page.goto(`${root}/source`);
  await expect(page.getByRole('textbox', { name: '本集小说正文' })).toBeVisible();
  await expect(page.getByRole('radio', { name: 'Agent 创作' })).toBeDisabled();
  await page.setViewportSize({ width: 760, height: 1000 });
  await page.getByRole('tab', { name: 'AI 创作', exact: true }).click();
  await expect(page.getByRole('button', { name: '生成剧本', exact: true })).toBeVisible();
  expect(state.mutations).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}

test('automatic conversation creation failure does not retry until an explicit action and reuses its key', async ({ page }) => {
  const state = await agentFixture(page);
  const keys: string[] = [];
  await page.route('**/api/v1/agent/conversations', async route => {
    if (route.request().method() !== 'POST') return route.fallback();
    keys.push(route.request().headers()['idempotency-key']);
    if (keys.length === 1) return route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: '自动会话创建暂时失败。' } } });
    await route.fallback();
  });
  await page.goto(`${root}/source?mode=agent`);
  await expect(page.getByRole('alert').filter({ hasText: '新对话请求已保留' })).toBeVisible();
  await page.getByRole('button', { name: '核对最新状态', exact: true }).click();
  await expect(page.locator('.agent-conversation-toolbar .ant-select')).not.toHaveClass(/ant-select-loading/);
  expect(keys).toHaveLength(1);
  await page.locator('.agent-conversation-toolbar').getByRole('button', { name: '新建对话', exact: true }).click();
  await expect(page).toHaveURL(url => url.searchParams.get('conversation') === '301');
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
  expect(state.mutations).toHaveLength(1);
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('an automatic conversation failure only blocks the stage that failed', async ({ page }) => {
  const state = await agentFixture(page);
  const keys: string[] = [];
  await page.route('**/api/v1/agent/conversations', async route => {
    if (route.request().method() !== 'POST') return route.fallback();
    keys.push(route.request().headers()['idempotency-key']);
    if (keys.length === 1) return route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: '小说阶段的会话暂时无法创建。' } } });
    await route.fallback();
  });
  await page.goto(`${root}/source?mode=agent`);
  await expect(page.getByRole('alert').filter({ hasText: '新对话请求已保留' })).toBeVisible();
  await page.getByRole('navigation', { name: '分集制作流程' }).getByRole('button', { name: /素材准备/ }).click();
  await expect(page).toHaveURL(url => url.pathname.endsWith('/assets') && url.searchParams.get('conversation') === '301');
  await expect(page.locator('.agent-conversation-toolbar .ant-select-selection-item')).toHaveText('素材准备：归来的旅人');
  expect(keys).toHaveLength(2);
  expect(keys[1]).not.toBe(keys[0]);
  expect(state.mutations).toHaveLength(1);
  await page.getByRole('navigation', { name: '分集制作流程' }).getByRole('button', { name: /小说改编/ }).click();
  await expect(page.getByRole('alert').filter({ hasText: '新对话请求已保留' })).toBeVisible();
  expect(keys).toHaveLength(2);
  await page.locator('.agent-conversation-toolbar').getByRole('button', { name: '新建对话', exact: true }).click();
  await expect(page).toHaveURL(url => url.pathname.endsWith('/source') && url.searchParams.get('conversation') === '302');
  expect(keys).toHaveLength(3);
  expect(keys[2]).toBe(keys[0]);
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

for (const mutation of ['rename', 'create'] as const) {
  test(`late ${mutation} response preserves the conversation selected through browser history`, async ({ page }) => {
    const state = await agentFixture(page);
    for (const [id, title, status] of [['301', '剧情讨论', 'succeeded'], ['302', '场景探索', 'failed']]) {
      state.conversations.push({ id, title, project_id: '10', episode_id: '20', archived: false, row_version: 1,
        created_at: '2026-10-02T00:00:00Z', updated_at: '2026-10-02T00:00:00Z', last_run_status: status });
    }
    const started = deferred(); const release = deferred();
    const method = mutation === 'rename' ? 'PATCH' : 'POST';
    const endpoint = mutation === 'rename' ? '/conversations/301' : '/conversations';
    await page.route(`**/api/v1/agent${endpoint}`, async route => {
      if (route.request().method() === method) { started.resolve(); await release.promise; }
      await route.fallback();
    });
    await page.goto(`${root}/source?mode=agent&conversation=302`);
    const selectedTitle = page.locator('.agent-conversation-toolbar .ant-select-selection-item');
    await expect(selectedTitle).toHaveText('场景探索');
    await selectedTitle.click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '剧情讨论' }).click();
    await expect(selectedTitle).toHaveText('剧情讨论');
    if (mutation === 'rename') {
      await page.getByRole('button', { name: '对话操作' }).click();
      await page.getByRole('menuitem', { name: '重命名' }).click();
      await page.getByLabel('对话名称').fill('迟到的剧情改写');
      await page.getByRole('button', { name: '保存名称' }).click();
    } else await page.getByRole('button', { name: '新建对话', exact: true }).click();
    await started.promise;
    await page.goBack();
    await expect(page).toHaveURL(url => url.searchParams.get('conversation') === '302');
    await expect(selectedTitle).toHaveText('场景探索');
    await expect(page.locator('.agent-conversation-state')).toHaveText('运行失败');
    // Record every visible selection during completion, including brief stale flashes.
    await page.evaluate(() => {
      const toolbar = document.querySelector('.agent-conversation-toolbar')!;
      const titles: string[] = [];
      const read = () => { const title = toolbar.querySelector('.ant-select-selection-item')?.textContent; if (title) titles.push(title); };
      new MutationObserver(read).observe(toolbar, { childList: true, subtree: true, characterData: true });
      read(); (window as any).__agentSelectionHistory = titles;
    });
    const response = page.waitForResponse(result => result.url().endsWith(`/api/v1/agent${endpoint}`) && result.request().method() === method);
    release.resolve(); await response;
    await expect(page.getByRole('button', { name: '对话操作' })).toBeEnabled();
    await expect(page).toHaveURL(url => url.searchParams.get('conversation') === '302');
    await expect(selectedTitle).toHaveText('场景探索');
    await expect(page.locator('.agent-conversation-state')).toHaveText('运行失败');
    expect(await page.evaluate(() => [...new Set((window as any).__agentSelectionHistory)])).toEqual(['场景探索']);
    await expect(page.locator('.agent-inline-notice')).toHaveCount(0);
    expect(state.mutations).toHaveLength(1);
    expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
  });
}

for (const destination of ['prompt', 'assembly'] as const) {
  test(`late create response respects ${destination} navigation while the request persists`, async ({ page }) => {
    const state = await agentFixture(page);
    state.conversations.push({ id: '301', title: '原有对话', project_id: '10', episode_id: '20', archived: false, row_version: 1,
      created_at: '2026-10-02T00:00:00Z', updated_at: '2026-10-02T00:00:00Z', last_run_status: null });
    const started = deferred(); const release = deferred();
    await page.route('**/api/v1/agent/conversations', async route => {
      if (route.request().method() === 'POST') { started.resolve(); await release.promise; }
      await route.fallback();
    });
    await page.goto(`${root}/source?mode=agent&conversation=301`);
    await expect(page.locator('.agent-conversation-toolbar .ant-select-selection-item')).toHaveText('原有对话');
    await page.getByRole('button', { name: '新建对话', exact: true }).click();
    await started.promise;
    if (destination === 'prompt') {
      await page.getByText('提示词创作', { exact: true }).click();
      await expect(page).toHaveURL(url => !url.searchParams.has('mode') && url.searchParams.get('conversation') === '301');
      await expect(page.getByRole('radio', { name: '提示词创作' })).toBeChecked();
    } else {
      await page.getByRole('navigation', { name: '分集制作流程' }).getByRole('button', { name: /成片合成与导出/ }).click();
      await expect(page).toHaveURL(url => url.pathname.endsWith('/assembly') && !url.searchParams.has('conversation'));
      await expect(page.getByRole('complementary', { name: 'Agent 创作对话' })).toBeVisible();
    }
    const response = page.waitForResponse(result => result.url().endsWith('/api/v1/agent/conversations') && result.request().method() === 'POST');
    release.resolve(); await response;
    await expect(page.locator('.agent-conversation-toolbar button[aria-label="新建对话"]')).toBeEnabled();
    if (destination === 'prompt') await expect(page.getByRole('complementary', { name: 'Agent 创作对话' })).not.toBeVisible();
    if (destination === 'prompt') {
      await expect(page).toHaveURL(url => url.pathname.endsWith('/source') && !url.searchParams.has('mode') && url.searchParams.get('conversation') === '301');
      await expect(page.getByRole('radio', { name: '提示词创作' })).toBeChecked();
      await expect(page.getByRole('textbox', { name: '本集小说正文' })).toBeVisible();
    } else await expect(page).toHaveURL(url => url.pathname.endsWith('/assembly') && url.searchParams.get('mode') === 'agent' && url.searchParams.get('conversation') === '303');
    expect(state.conversations).toHaveLength(destination === 'prompt' ? 2 : 3);
    expect(state.mutations).toHaveLength(destination === 'prompt' ? 1 : 2);
    expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
  });
}
