import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';

const time = '2026-10-06T00:00:00Z';
const conversation = (id: string) => ({ id, title: '项目对话', project_id: '10', episode_id: null, stage: null, subject_type: null, subject_id: null, task_type: null, scope_version: 2, archived: false, row_version: '1', created_at: time, updated_at: time, last_run_status: null });
const legacy = { ...conversation('301'), title: '原人物创作记录', episode_id: '20', scope_version: 1, stage: 'assets', subject_type: 'asset', subject_id: '501', task_type: 'creation' };
const deferred = () => { let resolve!: () => void; const promise = new Promise<void>(done => { resolve = done; }); return { promise, resolve }; };
const input = (page: Page) => page.getByRole('textbox', { name: '给助手的消息', exact: true });
const stageButton = (page: Page, name: RegExp) => page.getByRole('navigation', { name: '分集制作流程' }).getByRole('button', { name });

async function assistantFixture(page: Page) {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: '9007199254740993', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true } } }));
  const calls: { method: string; path: string; body: any }[] = [];
  const conversations = [conversation('801')]; const messages: Record<string, any[]> = {}; let currentRun: any = null;
  await page.route('**/api/v1/agent/**', route => {
    const path = new URL(route.request().url()).pathname.slice('/api/v1/agent'.length);
    const reply = (json: any) => route.fulfill({ json });
    if (path === '/status') return reply({ enabled: true, schema_ready: true });
    if (path === '/models') return reply({ items: [{ id: '71', name: '文本模型', model_key: 'fixture', row_version: '1', protocol: 'chat', verified: false, preferred: true, tool_calling: false }], preferred_id: '71' });
    if (path === '/conversations/301') return reply(legacy);
    if (path === '/conversations/301/messages') return reply({ items: [{ id: '401', seq: 1, role: 'assistant', content: '原人物创作建议仍然保留。', references: [], artifacts: [], created_at: time }], total: 1, offset: 0, limit: 50 });
    if (path === '/conversations/301/state') return reply({ conversation_id: '301', cursor: 0, resume_cursor: 0, active_run: currentRun, queued_runs: [] });
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (path === '/runs/701/stop') { currentRun = { ...currentRun, status: 'cancelled' }; return reply(currentRun); }
    return route.fallback();
  });
  await page.route('**/api/v1/assistant/**', route => {
    const request = route.request(); const path = new URL(request.url()).pathname.slice('/api/v1/assistant'.length); const method = request.method();
    const body = ['POST', 'PATCH'].includes(method) ? request.postDataJSON() : null; calls.push({ method, path, body });
    const reply = (json: any, status = 200) => route.fulfill({ json, status });
    if (path === '/conversations/resolve') return reply(conversations[0]);
    if (path === '/conversations' && method === 'POST') { const item = conversation(String(801 + conversations.length)); conversations.push(item); return reply(item, 201); }
    if (path === '/legacy-conversations') return reply({ items: [legacy], total: 1, offset: 0, limit: 20 });
    if (path === '/conversations') return reply({ items: conversations, total: conversations.length, offset: 0, limit: 20 });
    const id = path.split('/')[2];
    if (/^\/conversations\/\d+$/.test(path)) return reply(conversations.find(item => item.id === id));
    if (path.endsWith('/attachments')) return reply({ items: [], total: 0, offset: 0, limit: 50 });
    if (path.endsWith('/runs')) return reply({ items: [], total: 0, offset: 0, limit: 1 });
    if (path.endsWith('/state')) return reply({ conversation_id: id, cursor: 0, resume_cursor: 0, active_run: null, queued_runs: [] });
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (path.endsWith('/messages') && method === 'GET') return reply({ items: messages[id] ?? [], total: messages[id]?.length ?? 0, offset: 0, limit: 50 });
    if (path.endsWith('/messages') && method === 'POST') {
      const message = { id: String(1001 + calls.length), seq: (messages[id]?.length ?? 0) + 1, role: 'user', content: body.content, references: [], artifacts: [], created_at: time }; (messages[id] ??= []).push(message);
      return reply({ message, run: { id: '901', conversation_id: id, status: 'succeeded', phase: 'complete', mode: 'discuss', model_config_id: '71', model_name: '文本模型', row_version: '1', error: null, usage: {}, budget: {}, review: null, awaiting_artifact_ids: [], created_at: time, updated_at: time, finished_at: time, queue_position: 0, waiting_reason: null }, cursor: 0 }, 201);
    }
    state.unexpected.push(`${method} assistant${path}`); return reply({ error: { code: 'unexpected' } }, 501);
  });
  return { ...state, calls, conversations, setLegacyRun(run: any) { currentRun = run; } };
}
const sends = (state: Awaited<ReturnType<typeof assistantFixture>>) => state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'));

for (const width of [1440, 768, 390]) {
  test(`assistant opens beside saved status and preserves editor inputs at ${width}px`, async ({ page }) => {
    const state = await assistantFixture(page); await page.setViewportSize({ width, height: 1000 }); await page.goto(`${root}/source`);
    if (width <= 1024) await page.getByRole('tab', { name: 'AI 创作', exact: true }).click();
    const requirements = page.getByPlaceholder('例如：突出主角冲突，保留关键对白，结尾设置悬念。'); await requirements.fill('保留车站对白');
    const entry = page.getByRole('button', { name: 'AI 创作助手', exact: true }); expect(await entry.evaluate(node => node.nextElementSibling?.classList.contains('episode-save-state'))).toBe(true);
    await entry.click(); await expect(input(page)).toBeEditable(); await input(page).fill('助手草稿');
    await expect(page.getByRole('radio', { name: '提示词创作', exact: true })).toHaveCount(0); await expect(page.getByRole('radio', { name: 'Agent 创作', exact: true })).toHaveCount(0);
    if (width < 1440) await expect(page.getByRole('dialog', { name: 'AI 创作助手', exact: true })).toBeVisible();
    await page.screenshot({ path: test.info().outputPath(`assistant-${width}.png`), animations: 'disabled' });
    await page.getByRole('button', { name: '关闭助手', exact: true }).click(); await expect(requirements).toHaveValue('保留车站对白');
    await entry.click(); await expect(input(page)).toHaveValue('助手草稿'); expect(state.calls.filter(call => call.path === '/conversations/resolve')).toHaveLength(1);
    expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
  });
}

test('one project conversation persists through all four stages and refresh', async ({ page }) => {
  const state = await assistantFixture(page); await page.goto(`${root}/source?assistant=open`); await input(page).fill('项目讨论草稿');
  for (const stage of [/素材准备/, /分镜制作/, /成片合成与导出/, /小说改编/]) {
    await stageButton(page, stage).click(); await expect(input(page)).toHaveValue('项目讨论草稿'); await expect(page.getByRole('button', { name: 'AI 创作助手', exact: true })).toBeVisible();
    await expect(page).toHaveURL(url => url.searchParams.get('conversation') === '801' && !url.searchParams.has('mode'));
  }
  await page.reload(); await expect(input(page)).toHaveValue('项目讨论草稿'); expect(state.conversations).toHaveLength(1); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('asset and shot selections restore editing without changing conversation', async ({ page }) => {
  const state = await assistantFixture(page); await page.goto(`${root}/assets?assistant=open`); await input(page).fill('同一段对话');
  await page.getByRole('button', { name: '林晚', exact: true }).click(); await expect(page.getByRole('region', { name: '编辑 林晚', exact: true })).toBeVisible();
  await expect(page).toHaveURL(url => url.searchParams.get('conversation') === '801' && !url.searchParams.has('assistant'));
  await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click(); await expect(input(page)).toHaveValue('同一段对话');
  await page.getByRole('button', { name: '关闭助手', exact: true }).click(); await page.getByRole('button', { name: '返回素材列表', exact: true }).click(); await stageButton(page, /分镜制作/).click();
  await expect(page).toHaveURL(url => url.pathname.endsWith('/storyboard'));
  await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click(); await page.getByRole('button', { name: '选择分镜 01', exact: true }).click(); await expect(page.getByRole('textbox', { name: '分镜 1 脚本', exact: true })).toBeEditable();
  await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click(); await expect(input(page)).toHaveValue('同一段对话'); expect(state.conversations).toHaveLength(1); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('send waits for source save and removing work context permits plain chat', async ({ page }) => {
  const state = await assistantFixture(page); const started = deferred(); const release = deferred();
  await page.route(`**/api/v1${root}/novel`, async route => { started.resolve(); await release.promise; await route.fallback(); });
  await page.goto(`${root}/source?assistant=open`); await input(page).fill('分析正文'); await page.getByRole('textbox', { name: '本集小说正文', exact: true }).fill('待保存的新正文');
  await page.getByRole('button', { name: '发送', exact: true }).click(); await started.promise;
  try { expect(sends(state)).toHaveLength(0); await expect(input(page)).toHaveValue('分析正文'); } finally { release.resolve(); }
  await expect.poll(() => sends(state).length).toBe(1); expect(sends(state)[0].body.context).toMatchObject({ kind: 'episode', id: '20', revision: '2', stage: 'source', include_document: true });
  expect(sends(state)[0].body).not.toHaveProperty('mode'); expect(sends(state)[0].body).not.toHaveProperty('task');
  await page.getByRole('button', { name: '这条消息不带当前作品', exact: true }).click(); await input(page).fill('一般建议'); await page.getByRole('button', { name: '发送', exact: true }).click(); await expect.poll(() => sends(state).length).toBe(2);
  expect(sends(state)[1].body.context).toBeNull(); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('source conflict retains editor and assistant drafts without submitting', async ({ page }) => {
  const state = await assistantFixture(page); await page.route(`**/api/v1${root}/novel`, route => route.fulfill({ status: 409, json: { error: { code: 'version_conflict', message: '正文已在其他窗口修改。' } } }));
  await page.goto(`${root}/source`); await page.getByRole('textbox', { name: '本集小说正文', exact: true }).fill('冲突正文'); await expect(page.getByText('版本冲突，保存已暂停', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click(); await input(page).fill('冲突问题'); await page.getByRole('button', { name: '发送', exact: true }).click(); await expect(page.getByText('当前正文尚未保存成功，请处理保存提示后再发送。')).toBeVisible();
  await expect(input(page)).toHaveValue('冲突问题'); await expect(page.getByRole('textbox', { name: '本集小说正文', exact: true })).toHaveValue('冲突正文'); expect(sends(state)).toHaveLength(0); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const action of ['close', 'stage'] as const) {
  test(`late legacy detail does not reopen or redirect after assistant ${action}`, async ({ page }) => {
    const state = await assistantFixture(page);
    const started = deferred(); const release = deferred();
    await page.route('**/api/v1/agent/conversations/301', async route => {
      started.resolve(); await release.promise; await route.fallback();
    });
    await page.goto(`${root}/source?assistant=open`);
    await expect(input(page)).toBeEditable();
    await page.getByRole('button', { name: '对话记录', exact: true }).click();
    const history = page.getByRole('dialog', { name: '对话记录', exact: true });
    await history.getByText('历史创作记录', { exact: true }).click();
    await history.getByRole('button', { name: /^原人物创作记录/ }).click();
    await started.promise;
    const detailResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/agent/conversations/301');
    try {
      if (action === 'close') await page.getByRole('button', { name: '关闭助手', exact: true }).click();
      else await stageButton(page, /分镜制作/).click();
      await expect(page).toHaveURL(url => url.pathname.endsWith(action === 'close' ? '/source' : '/storyboard')
        && (action !== 'close' || !url.searchParams.has('assistant')));
    } finally { release.resolve(); }
    await (await detailResponse).finished();
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page).toHaveURL(url => url.pathname.endsWith(action === 'close' ? '/source' : '/storyboard')
      && !url.searchParams.has('legacy_conversation') && (action !== 'close' || !url.searchParams.has('assistant')));
    await expect(page.getByRole('complementary', { name: '历史创作记录', exact: true })).toHaveCount(0);
    if (action === 'close') await expect(input(page)).toBeHidden();
    else await expect(input(page)).toBeEditable();
    expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
  });
}

test('legacy link shows historical messages and task controls without creating a new chat', async ({ page }) => {
  const state = await assistantFixture(page); state.setLegacyRun({ id: '701', conversation_id: '301', status: 'running', phase: 'thinking', mode: 'auto', model_config_id: '71', model_name: '历史模型', row_version: '1', error: null, usage: {}, budget: {}, review: null, awaiting_artifact_ids: [], created_at: time, updated_at: time, finished_at: null, queue_position: 0, waiting_reason: null });
  await page.goto(`${root}/assets?mode=agent&conversation=301&agent_subject_assets=501&conversation_assets=301`); await expect(page.getByRole('complementary', { name: '历史创作记录', exact: true })).toContainText('原人物创作建议仍然保留。');
  await expect(input(page)).toHaveCount(0); await expect(page.getByRole('textbox', { name: '创作要求', exact: true })).toHaveCount(0); await expect(page).toHaveURL(url => url.searchParams.get('legacy_conversation') === '301' && !url.searchParams.has('mode') && !url.searchParams.has('agent_subject_assets'));
  expect(state.calls.filter(call => call.path === '/conversations/resolve')).toHaveLength(0); await page.getByRole('button', { name: '停止运行', exact: true }).click(); await page.getByRole('button', { name: '返回 AI 创作助手', exact: true }).click(); await expect(input(page)).toBeEditable(); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
