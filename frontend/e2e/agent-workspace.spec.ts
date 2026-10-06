import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';

const time = '2026-10-04T00:00:00Z';
const sourceScope = { stage: 'source', subject_type: 'episode', subject_id: '20', task_type: 'writing' };
const deferred = () => { let resolve!: () => void; const promise = new Promise<void>(done => { resolve = done; }); return { promise, resolve }; };
const conversation = (id: string, title: string, scope = sourceScope) => ({ id, title, project_id: '10', episode_id: '20', ...scope, scope_version: 1, archived: false, row_version: '1', created_at: time, updated_at: time, last_run_status: null, last_message_preview: '保留人物动机与情绪。' });

async function agentFixture(page: Page, enabled = true) {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const conversations: any[] = [];
  const messages: Record<string, any[]> = {};
  const calls: { method: string; path: string; query: URLSearchParams; body: any; key?: string }[] = [];
  const mutations: typeof calls = [];
  let nextId = 301;
  await page.route('**/api/v1/agent/**', async route => {
    const request = route.request(); const url = new URL(request.url());
    const path = url.pathname.slice('/api/v1/agent'.length); const method = request.method();
    const body = ['POST', 'PATCH'].includes(method) ? request.postDataJSON() : null;
    const call = { method, path, query: url.searchParams, body, key: request.headers()['idempotency-key'] };
    calls.push(call);
    const reply = (data: any, status = 200) => route.fulfill({ status, json: data });
    if (path === '/status') return reply({ enabled, schema_ready: enabled });
    if (path === '/models') return reply({ items: [{ id: '71', name: '创作协作模型', model_key: 'fixture', row_version: '1', protocol: 'chat', verified: false, preferred: true }], preferred_id: '71' });
    if (path === '/skills') return reply({ items: [], total: 0, offset: 0, limit: 50 });
    const matches = (item: any, scope: any) => ['stage', 'subject_type', 'subject_id', 'task_type'].every(key => !scope[key] || item[key] === scope[key]);
    if (path === '/conversations' && method === 'GET') {
      const filters = Object.fromEntries(url.searchParams);
      const items = conversations.filter(item => matches(item, filters) && (filters.include_archived === 'true' || !item.archived) && (!filters.query || item.title.includes(filters.query)));
      const offset = Number(filters.offset || 0);
      return reply({ items: items.slice(offset, offset + 20), total: items.length, offset, limit: 20 });
    }
    if ((path === '/conversations' || path === '/conversations/resolve') && method === 'POST') {
      mutations.push(call);
      const existing = path.endsWith('/resolve') && conversations.filter(item => matches(item, body) && !item.archived).at(-1);
      if (existing) return reply(existing);
      while (conversations.some(item => item.id === String(nextId))) nextId++;
      const item = conversation(String(nextId++), body.title || '新对话', body);
      conversations.push(item); return reply(item, path.endsWith('/resolve') ? 200 : 201);
    }
    const id = path.split('/')[2]; const item = conversations.find(item => item.id === id);
    if (/^\/conversations\/\d+$/.test(path)) {
      if (!item) return reply({ error: { code: 'NOT_FOUND' } }, 404);
      if (method === 'GET' && !matches(item, Object.fromEntries(url.searchParams))) return reply({ error: { code: 'SCOPE_CONFLICT', message: '对话不属于当前对象。' } }, 409);
      if (method === 'PATCH') { mutations.push(call); Object.assign(item, body, { row_version: String(BigInt(item.row_version) + 1n) }); }
      return reply(item);
    }
    if (path.endsWith('/messages') && method === 'GET') { const items = messages[id] || []; return reply({ items, total: items.length, offset: 0, limit: 50 }); }
    if (path.endsWith('/attachments') && method === 'GET') return reply({ items: [], total: 0, offset: 0, limit: 50 });
    if (path.endsWith('/state')) return reply({ conversation_id: id, cursor: 0, active_run: null, queued_runs: [] });
    if (path.endsWith('/events')) return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    state.unexpected.push(`${method} agent${path}`);
    return reply({ error: { code: 'UNEXPECTED' } }, 501);
  });
  return { ...state, mutations, calls, conversations, messages };
}

async function openHistory(page: Page) {
  const dialog = page.getByRole('dialog', { name: /的对话记录$/ });
  if (!await dialog.isVisible()) await page.getByRole('button', { name: '对话记录', exact: true }).click();
  await expect(dialog).toBeVisible(); return dialog;
}
async function expectTitle(page: Page, title: string) {
  const dialog = await openHistory(page);
  await expect(dialog.locator('.agent-history-entry[aria-current="true"] strong')).toHaveText(title);
  await page.keyboard.press('Escape'); await expect(dialog).toHaveCount(0);
}
const draftInput = (page: Page) => page.getByRole('textbox', { name: '创作要求', exact: true });
const stageButton = (page: Page, name: RegExp) => page.getByRole('navigation', { name: '分集制作流程' }).getByRole('button', { name });

for (const width of [1440, 768, 390]) {
  test(`creation modes and scoped history stay inside the AI region at ${width}px`, async ({ page }) => {
    const state = await agentFixture(page); await page.setViewportSize({ width, height: 1000 });
    await page.goto(`${root}/source`);
    if (width <= 1024) await page.getByRole('tab', { name: 'AI 创作', exact: true }).click();
    const panel = page.getByRole('region', { name: 'AI 创作区域', exact: true });
    await expect(panel.locator('.creation-mode-toolbar .agent-mode-switch')).toBeVisible();
    await expect(page.locator('.episode-top .agent-mode-switch')).toHaveCount(0);
    await panel.getByText('Agent 创作', { exact: true }).click();
    await expect(draftInput(page)).toBeEditable(); await expect(page).toHaveURL(url => url.searchParams.get('conversation') === '301');
    expect(state.mutations[0].body).toMatchObject(sourceScope);
    expect(state.mutations[0].body).not.toHaveProperty('scope_version');
    const history = await openHistory(page);
    await expect(history.locator('.agent-history-entry')).toHaveCount(1);
    const inset = await history.locator('.agent-history-body').evaluate(node => ({ left: parseFloat(getComputedStyle(node).paddingLeft), right: parseFloat(getComputedStyle(node).paddingRight) }));
    expect(inset.left).toBeGreaterThanOrEqual(16); expect(inset.right).toBeGreaterThanOrEqual(16);
    const searchBounds = await history.locator('.ant-input-search').evaluate(node => {
      const input = node.querySelector('.ant-input-affix-wrapper')!.getBoundingClientRect();
      const button = node.querySelector('.ant-input-search-button')!.getBoundingClientRect();
      return { input: { y: input.y, height: input.height }, button: { y: button.y, height: button.height } };
    });
    expect(Math.abs(searchBounds.input.y - searchBounds.button.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(searchBounds.input.height - searchBounds.button.height)).toBeLessThanOrEqual(1);
    if (width < 768) expect(Math.round(searchBounds.input.height)).toBeGreaterThanOrEqual(44);
    await page.screenshot({ path: test.info().outputPath(`scoped-history-${width}.png`), animations: 'disabled' });
    await history.getByRole('button', { name: '关闭', exact: true }).focus();
    await page.keyboard.press('Tab'); await expect(history.getByRole('button', { name: '关闭弹窗', exact: true })).toBeFocused();
    await page.keyboard.press('Escape'); await expect(history).toHaveCount(0);
    await expect(page.getByRole('button', { name: '对话记录', exact: true })).toBeFocused();
    expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
  });

  test(`assembly Agent links expose only assembly at ${width}px`, async ({ page }) => {
    const state = await agentFixture(page); await page.setViewportSize({ width, height: 1000 });
    await page.goto(`${root}/assembly?mode=agent`);
    await expect(page.getByRole('region', { name: '视频时间轴', exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: 'AI 创作区域', exact: true })).toHaveCount(0);
    await expect(page.getByRole('tab', { name: 'AI 创作', exact: true })).toHaveCount(0);
    await expect(page.getByRole('complementary', { name: 'Agent 创作对话', exact: true })).toHaveCount(0);
    expect(state.mutations).toEqual([]); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
  });
}

test('stage navigation preserves prompt drafts, private renamed history, archive and refresh', async ({ page }) => {
  const state = await agentFixture(page); await page.goto(`${root}/source`);
  const prompt = page.getByPlaceholder('例如：突出主角冲突，保留关键对白，结尾设置悬念。');
  await prompt.fill('保留车站对白'); await page.getByText('Agent 创作', { exact: true }).click();
  await expect(draftInput(page)).toBeEditable(); await expectTitle(page, '本集作品：归来的旅人');
  let history = await openHistory(page);
  await history.getByRole('button', { name: '本集作品：归来的旅人的对话操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '重命名' }).click(); await history.getByLabel('对话名称').fill('雨夜结尾');
  await history.getByRole('button', { name: '保存名称' }).click(); await expectTitle(page, '雨夜结尾');
  await stageButton(page, /素材准备/).click(); await expect(page).toHaveURL(url => url.pathname.endsWith('/assets') && url.searchParams.get('conversation') === '302');
  expect(state.conversations[1]).toMatchObject({ stage: 'assets', subject_type: 'episode', task_type: 'extraction' });
  await stageButton(page, /小说改编/).click(); await expectTitle(page, '雨夜结尾');
  await page.getByText('提示词创作', { exact: true }).click(); await expect(prompt).toHaveValue('保留车站对白');
  await page.getByText('Agent 创作', { exact: true }).click(); history = await openHistory(page);
  await history.getByRole('button', { name: '雨夜结尾的对话操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '归档对话' }).click(); await page.keyboard.press('Escape');
  await expect(page.getByRole('heading', { name: '这段对话已归档' })).toBeVisible();
  await page.getByRole('button', { name: '恢复对话', exact: true }).click(); await expect(draftInput(page)).toBeEditable();
  await draftInput(page).fill('回到小说阶段继续整理这段对话草稿'); await page.reload();
  await expect(draftInput(page)).toHaveValue('回到小说阶段继续整理这段对话草稿'); await expectTitle(page, '雨夜结尾');
  expect(state.conversations).toHaveLength(2);
  for (const width of [1280, 1920]) { await page.setViewportSize({ width, height: 1000 }); await expect(page.getByRole('separator', { name: '调整对话区域宽度' })).toBeVisible(); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true); }
  await page.getByRole('separator', { name: '调整对话区域宽度' }).focus(); await page.keyboard.press('Home');
  await expect(page.getByRole('separator', { name: '调整对话区域宽度' })).toHaveAttribute('aria-valuenow', '360');
  await page.setViewportSize({ width: 390, height: 900 }); await page.getByRole('tab', { name: 'AI 创作', exact: true }).focus(); await page.keyboard.press('Home');
  await expect(page.getByRole('tab', { name: '作品', exact: true })).toBeFocused();
  await page.getByRole('tab', { name: 'AI 创作', exact: true }).click(); await expect(draftInput(page)).toHaveValue('回到小说阶段继续整理这段对话草稿');
  await page.setViewportSize({ width: 1440, height: 1000 }); await stageButton(page, /成片合成与导出/).click();
  await expect(page.getByRole('region', { name: 'AI 创作区域', exact: true })).toHaveCount(0);
  await stageButton(page, /小说改编/).click(); await expect(draftInput(page)).toHaveValue('回到小说阶段继续整理这段对话草稿');
  expect(state.requests.filter(request => request.method === 'POST' && request.path.startsWith('/ai/generations'))).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('disabled Agent keeps prompt creation usable', async ({ page }) => {
  const state = await agentFixture(page, false); await page.goto(`${root}/source`);
  await expect(page.getByRole('radio', { name: 'Agent 创作' })).toBeDisabled();
  await page.setViewportSize({ width: 760, height: 1000 }); await page.getByRole('tab', { name: 'AI 创作', exact: true }).click();
  await expect(page.getByRole('button', { name: '生成剧本', exact: true })).toBeVisible();
  expect(state.mutations).toEqual([]); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('scope resolution failure requires explicit retry and does not block another stage', async ({ page }) => {
  const state = await agentFixture(page); let sourceAttempts = 0; let failSource = true;
  await page.route('**/api/v1/agent/conversations/resolve', async route => {
    if (route.request().postDataJSON().stage === 'source') { sourceAttempts++; if (failSource) return route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: '当前对象会话暂时无法载入。' } } }); }
    await route.fallback();
  });
  await page.goto(`${root}/source?mode=agent`); await expect(page.getByRole('alert').filter({ hasText: '服务暂时不可用' })).toBeVisible();
  const attempts = sourceAttempts; const history = await openHistory(page); await expect(history.locator('.agent-history-list')).toBeVisible(); await page.keyboard.press('Escape');
  expect(sourceAttempts).toBe(attempts); failSource = false;
  await page.getByRole('button', { name: '重新载入', exact: true }).click(); await expect(draftInput(page)).toBeEditable(); expect(sourceAttempts).toBe(attempts + 1);
  await stageButton(page, /素材准备/).click(); await expect(page).toHaveURL(url => url.pathname.endsWith('/assets') && url.searchParams.get('conversation') === '302');
  expect(state.conversations).toHaveLength(2); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('explicit conversation creation failure preserves its idempotency key', async ({ page }) => {
  const state = await agentFixture(page); const keys: string[] = [];
  await page.route('**/api/v1/agent/conversations', async route => {
    if (route.request().method() !== 'POST') return route.fallback();
    keys.push(route.request().headers()['idempotency-key']);
    if (keys.length === 1) return route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: '创建暂时失败。' } } });
    await route.fallback();
  });
  await page.goto(`${root}/source?mode=agent`); await expect(draftInput(page)).toBeEditable(); const history = await openHistory(page);
  await history.getByRole('button', { name: '新建对话', exact: true }).click(); await expect(history.getByRole('alert')).toContainText('创建请求已保留');
  await history.getByRole('button', { name: /刷新记录/ }).click(); expect(keys).toHaveLength(1);
  await history.getByRole('button', { name: '新建对话', exact: true }).click(); await expect(page).toHaveURL(url => url.searchParams.get('conversation') === '302');
  expect(keys[0]).toBeTruthy(); expect(keys[1]).toBe(keys[0]); expect(state.conversations).toHaveLength(2);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const mutation of ['rename', 'create'] as const) {
  test(`late ${mutation} completion preserves browser history selection`, async ({ page }) => {
    const state = await agentFixture(page); state.conversations.push(conversation('301', '剧情讨论'), conversation('302', '场景探索'));
    const started = deferred(); const release = deferred(); const method = mutation === 'rename' ? 'PATCH' : 'POST';
    const endpoint = mutation === 'rename' ? '/conversations/301' : '/conversations';
    await page.route(`**/api/v1/agent${endpoint}`, async route => { if (route.request().method() === method) { started.resolve(); await release.promise; } await route.fallback(); });
    await page.goto(`${root}/source?mode=agent&conversation=302`); let history = await openHistory(page);
    await history.locator('.agent-history-entry').filter({ hasText: '剧情讨论' }).click(); await expect(page).toHaveURL(url => url.searchParams.get('conversation') === '301');
    history = await openHistory(page);
    if (mutation === 'rename') { await history.getByRole('button', { name: '剧情讨论的对话操作' }).click(); await page.getByRole('menuitem', { name: '重命名' }).click(); await history.getByLabel('对话名称').fill('迟到的剧情改写'); await history.getByRole('button', { name: '保存名称' }).click(); }
    else await history.getByRole('button', { name: '新建对话', exact: true }).click();
    await started.promise; await page.keyboard.press('Escape'); await page.goBack();
    await expect(page).toHaveURL(url => url.searchParams.get('conversation') === '302'); await expect(draftInput(page)).toBeEditable();
    const response = page.waitForResponse(result => new URL(result.url()).pathname.endsWith(`/api/v1/agent${endpoint}`) && result.request().method() === method);
    release.resolve(); await response; await expectTitle(page, '场景探索');
    await expect(page).toHaveURL(url => url.searchParams.get('conversation') === '302');
    expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
  });
}

for (const destination of ['prompt', 'assembly'] as const) {
  test(`late creation respects ${destination} navigation`, async ({ page }) => {
    const state = await agentFixture(page); state.conversations.push(conversation('301', '原有对话'));
    const started = deferred(); const release = deferred();
    await page.route('**/api/v1/agent/conversations', async route => { if (route.request().method() === 'POST') { started.resolve(); await release.promise; } await route.fallback(); });
    await page.goto(`${root}/source?mode=agent&conversation=301`); const history = await openHistory(page);
    await history.getByRole('button', { name: '新建对话', exact: true }).click(); await started.promise; await page.keyboard.press('Escape');
    if (destination === 'prompt') await page.getByText('提示词创作', { exact: true }).click(); else await stageButton(page, /成片合成与导出/).click();
    const destinationUrl = page.url(); const response = page.waitForResponse(result => new URL(result.url()).pathname.endsWith('/api/v1/agent/conversations') && result.request().method() === 'POST');
    release.resolve(); await response; await expect(page).toHaveURL(destinationUrl);
    if (destination === 'assembly') await expect(page.getByRole('region', { name: 'AI 创作区域', exact: true })).toHaveCount(0);
    else await expect(page.getByRole('radio', { name: '提示词创作' })).toBeChecked();
    expect(state.conversations).toHaveLength(2); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
  });
}

test('late default resolution stays out of assembly and resumes the original scope', async ({ page }) => {
  const state = await agentFixture(page); const started = deferred(); const release = deferred();
  await page.route('**/api/v1/agent/conversations/resolve', async route => { started.resolve(); await release.promise; await route.fallback(); });
  await page.goto(`${root}/source?mode=agent`); await started.promise; await stageButton(page, /成片合成与导出/).click();
  const url = page.url(); const response = page.waitForResponse(result => result.url().endsWith('/api/v1/agent/conversations/resolve'));
  release.resolve(); await response; await expect(page).toHaveURL(url); await stageButton(page, /小说改编/).click();
  await expect(draftInput(page)).toBeEditable(); expect(state.conversations).toHaveLength(1);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('prompt collapsing preserves drafts, width and keyboard focus', async ({ page }) => {
  const state = await agentFixture(page); await page.goto(`${root}/source`);
  const novel = page.getByRole('textbox', { name: '本集小说正文' }); const requirements = page.getByPlaceholder('例如：突出主角冲突，保留关键对白，结尾设置悬念。');
  await requirements.fill('收起后仍保留车站对白'); const initialWidth = (await novel.boundingBox())!.width;
  await page.getByRole('button', { name: '收起 AI 创作区域', exact: true }).focus(); await page.keyboard.press('Enter');
  const expand = page.getByRole('button', { name: '展开 AI 创作区域', exact: true }); await expect(expand).toBeFocused();
  await expect(page.getByRole('region', { name: 'AI 创作区域', exact: true })).toHaveCount(0);
  await expect.poll(async () => (await novel.boundingBox())?.width ?? 0).toBeGreaterThan(initialWidth + 200);
  await page.keyboard.press('Space'); await expect(page.getByRole('button', { name: '收起 AI 创作区域', exact: true })).toBeFocused(); await expect(requirements).toHaveValue('收起后仍保留车站对白');
  expect(state.mutations).toEqual([]); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('private scoped detail loads before input and attachments and keeps the first draft', async ({ page }) => {
  const state = await agentFixture(page); const started = deferred(); const release = deferred(); let reads = 0;
  await page.route('**/api/v1/agent/conversations/301/attachments?*', async route => { reads++; await route.fallback(); });
  await page.route('**/api/v1/agent/conversations/301?*', async route => { started.resolve(); await release.promise; await route.fallback(); });
  await page.goto(`${root}/source?mode=agent`); await started.promise;
  try { await expect(draftInput(page)).toHaveCount(0); expect(reads).toBe(0); } finally { release.resolve(); }
  await expect(draftInput(page)).toBeEditable(); await draftInput(page).fill('详情载入后的第一份草稿');
  await expect.poll(() => reads).toBe(1); await page.getByRole('button', { name: '收起 AI 创作区域', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 1000 }); await page.getByRole('tab', { name: 'AI 创作', exact: true }).click();
  await expect(draftInput(page)).toHaveValue('详情载入后的第一份草稿'); expect(reads).toBe(1);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('collapsed Agent preserves scrolling toolbar and drafts across responsive views', async ({ page }) => {
  const state = await agentFixture(page);
  state.messages['301'] = Array.from({ length: 16 }, (_, index) => ({ id: String(2001 + index), seq: index + 1, role: 'assistant', content: `站台故事讨论 ${index + 1}。${'保留人物动机与情绪的连续性。'.repeat(12)}`, references: [], artifacts: [], created_at: time }));
  await page.goto(`${root}/source?mode=agent`); await draftInput(page).fill('保留尚未发送的 Agent 草稿');
  await page.setViewportSize({ width: 1440, height: 800 });
  for (const position of [70, 270]) {
    await page.locator('.agent-transcript').evaluate((node, top) => { node.scrollTop = top; }, position);
    await expect.poll(() => page.locator('.creation-mode-toolbar').evaluate(node => { const rect = node.getBoundingClientRect(); const pane = document.getElementById('agent-conversation-pane')!.getBoundingClientRect(); return rect.top >= pane.top && rect.top < pane.top + 5; })).toBe(true);
  }
  await page.getByRole('button', { name: '收起 AI 创作区域', exact: true }).click(); await expect(page.getByRole('complementary', { name: 'Agent 创作对话', exact: true })).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 1000 }); await page.getByRole('tab', { name: 'AI 创作', exact: true }).click(); await expect(draftInput(page)).toHaveValue('保留尚未发送的 Agent 草稿');
  await page.setViewportSize({ width: 1440, height: 1000 }); await page.getByRole('button', { name: '展开 AI 创作区域', exact: true }).click(); await expect(draftInput(page)).toHaveValue('保留尚未发送的 Agent 草稿');
  await stageButton(page, /成片合成与导出/).click(); await expect(page.getByRole('region', { name: 'AI 创作区域', exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('asset A and B histories, drafts, search and explicit editor stay isolated', async ({ page }) => {
  const state = await agentFixture(page);
  const second = { ...state.asset, id: '504', name: '沈舟' };
  await page.route(`**/api/v1${root}/assets?*`, route => route.fulfill({ json: { items: [state.asset, second], total: 2, offset: 0, limit: 20 } }));
  await page.goto(`${root}/assets?mode=agent`); await expect(draftInput(page)).toBeEditable();
  await page.getByRole('button', { name: '林晚', exact: true }).click(); await expect(page.getByRole('heading', { name: '角色 · 林晚', exact: true })).toBeVisible();
  await draftInput(page).fill('林晚应更加冷峻'); await expect(page.getByRole('region', { name: '编辑 林晚', exact: true })).toHaveCount(0);
  const aId = new URL(page.url()).searchParams.get('conversation');
  await page.getByRole('button', { name: '沈舟', exact: true }).click(); await expect(page.getByRole('heading', { name: '角色 · 沈舟', exact: true })).toBeVisible();
  await expect(draftInput(page)).toHaveValue(''); await draftInput(page).fill('沈舟保留温和语气');
  let history = await openHistory(page); await expect(history.locator('.agent-history-entry')).toHaveCount(1); await expect(history).toHaveAccessibleName('角色 · 沈舟的对话记录');
  await history.getByRole('searchbox', { name: '搜索当前对象对话' }).fill('温柔方向'); await expect(history.getByText('没有匹配的对话，试试其他名称。')).toBeVisible();
  const query = state.calls.filter(call => call.path === '/conversations' && call.method === 'GET').at(-1)!.query;
  expect(Object.fromEntries(query)).toMatchObject({ stage: 'assets', subject_type: 'asset', subject_id: '504', task_type: 'creation', query: '温柔方向' });
  await page.keyboard.press('Escape'); await page.getByRole('button', { name: '林晚', exact: true }).click();
  await expect(draftInput(page)).toHaveValue('林晚应更加冷峻'); await expect(page).toHaveURL(url => url.searchParams.get('conversation') === aId);
  await page.reload(); await expect(draftInput(page)).toHaveValue('林晚应更加冷峻');
  await page.getByRole('button', { name: '林晚更多操作' }).click(); await page.getByRole('menuitem', { name: '编辑素材' }).click();
  await expect(page.getByRole('region', { name: '编辑 林晚', exact: true })).toBeVisible();
  const description = page.getByRole('region', { name: '编辑 林晚', exact: true }).getByRole('textbox', { name: '描述', exact: true });
  await description.fill('尚未保存的人物外观修改'); await page.getByRole('button', { name: '沈舟', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '未保存的修改', exact: true });
  await expect(confirmation).toContainText('切换素材会放弃当前未保存的修改'); await confirmation.getByRole('button', { name: '取消', exact: true }).click();
  await expect(description).toHaveValue('尚未保存的人物外观修改'); await expect(page).toHaveURL(url => url.searchParams.get('conversation') === aId);
  await description.fill(state.asset.description);
  await page.getByRole('button', { name: '返回素材列表', exact: true }).click(); await expect(draftInput(page)).toHaveValue('林晚应更加冷峻');
  await page.getByRole('button', { name: '返回本集素材提取对话' }).click(); await expect(page).toHaveURL(url => !url.searchParams.has('agent_subject_assets'));
  expect(state.conversations.map(item => [item.subject_type, item.subject_id])).toEqual([['episode', '20'], ['asset', '501'], ['asset', '504']]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('shot selection, browser back and aggregate planning restore their scoped drafts', async ({ page }) => {
  const state = await agentFixture(page); await page.goto(`${root}/storyboard?mode=agent`); await expect(draftInput(page)).toBeEditable();
  await page.getByRole('button', { name: '选择分镜 01', exact: true }).click(); await expect(page.getByRole('heading', { name: '分镜 01', exact: true })).toBeVisible(); await draftInput(page).fill('第一镜头缓慢推进');
  const firstUrl = page.url();
  await page.getByRole('button', { name: '选择分镜 02', exact: true }).click(); await expect(page.getByRole('heading', { name: '分镜 02', exact: true })).toBeVisible(); await draftInput(page).fill('第二镜头保持近景');
  // Use browser history with an explicit same-page object URL to cover popstate restoration.
  await page.evaluate(url => { history.pushState({}, '', url); window.dispatchEvent(new PopStateEvent('popstate')); }, firstUrl);
  await expect(page.getByRole('heading', { name: '分镜 01', exact: true })).toBeVisible(); await expect(draftInput(page)).toHaveValue('第一镜头缓慢推进');
  await page.goBack(); await expect(page.getByRole('heading', { name: '分镜 02', exact: true })).toBeVisible(); await expect(draftInput(page)).toHaveValue('第二镜头保持近景');
  await page.getByRole('button', { name: '镜头信息', exact: true }).click(); await expect(page.getByRole('dialog', { name: '分镜 02 · 镜头信息' })).toBeVisible();
  await page.getByRole('button', { name: '完成', exact: true }).click(); await page.getByRole('button', { name: '返回本集分镜规划对话' }).click();
  await expect(page).toHaveURL(url => !url.searchParams.has('agent_subject_storyboard')); await expectTitle(page, '本集作品：归来的旅人');
  expect(state.conversations.map(item => [item.subject_type, item.subject_id])).toEqual([['episode', '20'], ['shot', '101'], ['shot', '102']]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('a mismatched conversation link never loads the other scope messages', async ({ page }) => {
  const state = await agentFixture(page); state.conversations.push(conversation('301', '人物私人对话', { stage: 'assets', subject_type: 'asset', subject_id: '501', task_type: 'creation' }));
  state.messages['301'] = [{ id: '999', seq: 1, role: 'assistant', content: '不得显示的另一对象消息', references: [], artifacts: [], created_at: time }];
  await page.goto(`${root}/source?mode=agent&conversation=301`); await expect(draftInput(page)).toBeEditable();
  await expect(page).toHaveURL(url => url.searchParams.get('conversation') === '302'); await expect(page.getByText('不得显示的另一对象消息')).toHaveCount(0);
  expect(state.calls.filter(call => call.path === '/conversations/301/messages')).toHaveLength(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
