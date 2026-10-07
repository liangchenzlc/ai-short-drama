import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';

const time = '2026-10-06T12:00:00Z';
async function assistantFixture(page: Page) {
  const base = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: '9007199254740993', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true } } }));
  const conversation = (id: string) => ({ id, project_id: '10', episode_id: null, title: id === '301' ? '故事讨论' : '新对话', scope_version: 2, stage: null, subject_type: null, subject_id: null, task_type: null, row_version: '1', archived: false, last_run_status: null, created_at: time, updated_at: time });
  const conversations = [conversation('301')];
  const messages: Record<string, any[]> = { '301': [] };
  const attachments: any[] = [];
  const calls: { path: string; method: string; body: any; key: string }[] = [];
  const receipts = new Map<string, any>();
  let abortSend = false;
  const skills = [{ id: 'builtin:continuity', name: '人物连续性', builtin: true, content_version: '1', row_version: null, enabled: true, instructions: '核对人物动机', filename: null, checksum_sha256: 'a'.repeat(64) }];
  await page.route('**/api/v1/agent/**', route => {
    const path = new URL(route.request().url()).pathname.slice('/api/v1/agent'.length);
    if (path === '/status') return route.fulfill({ json: { enabled: true, schema_ready: true } });
    if (path === '/models') return route.fulfill({ json: { items: [{ id: '71', name: '对话模型', row_version: '1', model_key: 'fixture', protocol: 'chat', verified: false, preferred: true }], preferred_id: '71' } });
    if (path === '/skills') return route.fulfill({ json: { items: skills, offset: 0, limit: 50, total: skills.length } });
    return route.fallback();
  });
  await page.route('**/api/v1/assistant/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname.slice('/api/v1/assistant'.length);
    const method = request.method(); const body = ['POST', 'PATCH'].includes(method) && request.headers()['content-type']?.includes('application/json') ? request.postDataJSON() : null;
    const key = request.headers()['idempotency-key']; calls.push({ path, method, body, key });
    const reply = (json: any, status = 200) => route.fulfill({ json, status });
    if (path === '/conversations/resolve') return reply(conversations[0]);
    if (path === '/conversations' && method === 'POST') { const next = conversation(String(301 + conversations.length)); conversations.push(next); messages[next.id] = []; return reply(next, 201); }
    if (path === '/conversations') { const items = conversations.filter(item => (url.searchParams.get('include_archived') === 'true' || !item.archived) && (!url.searchParams.get('query') || item.title.includes(url.searchParams.get('query')!))); return reply({ items, total: items.length, offset: 0, limit: 20 }); }
    if (path === '/legacy-conversations') return reply({ items: [], total: 0, offset: 0, limit: 20 });
    const id = path.split('/')[2];
    if (/^\/conversations\/\d+$/.test(path)) {
      const item = conversations.find(item => item.id === id)!;
      if (method === 'PATCH') Object.assign(item, body, { row_version: String(BigInt(item.row_version) + 1n) });
      return reply(item);
    }
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (path.endsWith('/state')) return reply({ conversation_id: id, cursor: 0, resume_cursor: 0, active_run: null, queued_runs: [] });
    if (path.endsWith('/runs')) return reply({ items: [], total: 0, offset: 0, limit: 1 });
    if (path.endsWith('/attachments') && method === 'GET') return reply({ items: attachments.filter(item => item.pending), total: attachments.filter(item => item.pending).length, offset: 0, limit: 50 });
    if (path.endsWith('/attachments/references')) {
      const item = { id: '7001', name: '林晚', kind: 'text', mime_type: 'text/plain', byte_size: 20, media_id: null, url: null, text_preview: '角色参考', checksum_sha256: 'b'.repeat(64), pending: true, metadata: { source_id: body.source_id } }; attachments.push(item); return reply(item, 201);
    }
    if (path.endsWith('/messages') && method === 'GET') return reply({ items: messages[id] ?? [], total: messages[id]?.length ?? 0, offset: 0, limit: 50 });
    if (path.endsWith('/messages') && method === 'POST') {
      if (receipts.has(key)) return reply(receipts.get(key), 201);
      const message = { id: '1001', seq: messages[id].length + 1, role: 'user', content: body.content, references: [], artifacts: [], created_at: time };
      const replyMessage = { ...message, id: '1002', seq: message.seq + 1, role: 'assistant', content: '## 创作建议\n\n保留人物的 **克制**。\n\n<script>window.__unsafe = true</script>', references: [{ type: 'source', name: '当前作品', truncated: ['source.novel.content'] }], artifacts: [] };
      messages[id].push(message, replyMessage); attachments.forEach(item => { item.pending = false; });
      const run = { id: '901', conversation_id: id, status: 'succeeded', phase: 'finished', row_version: '1', mode: 'discuss', model_config_id: '71', model_name: '对话模型', error: null, usage: {}, budget: {}, review: null, awaiting_artifact_ids: [], created_at: time, updated_at: time, finished_at: time, queue_position: 0, waiting_reason: null };
      const result = { message, run, cursor: 0 }; receipts.set(key, result);
      if (abortSend) { abortSend = false; return route.abort('failed'); }
      return reply(result, 201);
    }
    base.unexpected.push(`${method} assistant${path}`); return reply({ error: { code: 'UNEXPECTED' } }, 501);
  });
  return { ...base, calls, conversations, messages, attachments, abortNextSend: () => { abortSend = true; } };
}

test('shared canvas-style composer keeps project conversation, attachments, Skills and safe Markdown', async ({ page }, info) => {
  const state = await assistantFixture(page);
  await page.goto(`${root}/source?assistant=open`);
  const input = page.getByRole('textbox', { name: '给助手的消息' }); await expect(input).toBeVisible();
  await input.fill('@林');
  await page.getByRole('option', { name: '林晚', exact: true }).click();
  await expect(page.getByLabel('本次消息附件')).toContainText('林晚');
  await page.getByRole('button', { name: '加载 Skill', exact: true }).click();
  const skillDialog = page.getByRole('dialog', { name: '加载 Skill' });
  await skillDialog.getByRole('checkbox', { name: '人物连续性' }).check();
  await skillDialog.getByRole('button', { name: '完成', exact: true }).click();
  await expect(page.getByLabel('已加载技能')).toContainText('人物连续性');
  await input.fill('一起分析人物动机');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(input).toHaveValue('');
  const sent = state.calls.find(call => call.method === 'POST' && call.path.endsWith('/messages'))!;
  expect(sent.body).toMatchObject({ content: '一起分析人物动机', model_config_id: '71', attachment_ids: ['7001'], skills: [{ id: 'builtin:continuity', content_version: '1' }], context: { kind: 'episode', id: '20', revision: '1', stage: 'source' } });
  expect(sent.body).not.toHaveProperty('mode'); expect(sent.body).not.toHaveProperty('task');
  await expect(page.locator('.ai-assistant-reply h2')).toHaveText('创作建议');
  await expect(page.locator('.ai-assistant-reply strong')).toHaveText('克制');
  await expect(page.getByText('作品上下文已截取')).toBeVisible();
  expect(await page.evaluate(() => (window as any).__unsafe)).toBeUndefined();
  await input.fill('尚未发送的草稿');
  await page.getByRole('button', { name: '关闭助手', exact: true }).click();
  await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
  await expect(input).toHaveValue('尚未发送的草稿');
  await page.screenshot({ path: info.outputPath('shared-assistant-1440.png') });
  await page.setViewportSize({ width: 390, height: 900 });
  await expect(input).toBeVisible(); await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  await page.screenshot({ path: info.outputPath('shared-assistant-390.png') });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('uncertain accepted message reuses its frozen payload after refresh and context removal', async ({ page }) => {
  const state = await assistantFixture(page); state.abortNextSend();
  await page.goto(`${root}/source?assistant=open`);
  const input = page.getByRole('textbox', { name: '给助手的消息' }); await expect(input).toBeVisible();
  await page.getByRole('button', { name: '这条消息不带当前作品' }).click();
  await input.fill('普通问答'); await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText('发送结果尚未确认，草稿已保留。请使用原请求核对，避免重复调用。')).toBeVisible();
  await page.reload(); await expect(input).toHaveValue('普通问答');
  await page.getByRole('button', { name: '使用原请求核对', exact: true }).click();
  await expect(input).toHaveValue('');
  const sends = state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'));
  expect(sends).toHaveLength(2); expect(sends[0].body.context).toBeNull(); expect(sends[1].body).toEqual(sends[0].body); expect(sends[1].key).toEqual(sends[0].key);
  expect(state.messages['301'].filter(item => item.role === 'user')).toHaveLength(1);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('closing the assistant ignores a delayed new conversation response and preserves the current draft', async ({ page }) => {
  const state = await assistantFixture(page);
  let releaseCreate = () => {};
  const heldCreate = new Promise<void>(resolve => { releaseCreate = resolve; });
  await page.route('**/api/v1/assistant/conversations', async route => {
    if (route.request().method() !== 'POST') return route.fallback();
    await heldCreate;
    return route.fallback();
  });
  await page.goto(`${root}/source?assistant=open`);
  const input = page.getByRole('textbox', { name: '给助手的消息' });
  await expect(input).toBeVisible(); await expect(page).toHaveURL(/conversation=301/);
  await input.fill('保留在当前对话里的草稿');
  const newConversation = page.getByRole('button', { name: '新对话', exact: true, includeHidden: true });
  const createdRequest = page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/assistant/conversations');
  await newConversation.click(); await createdRequest;
  await expect(newConversation).toHaveClass(/ant-btn-loading/);
  await page.getByRole('button', { name: '关闭助手', exact: true }).click();
  await expect(input).toBeHidden();
  const createdResponse = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/assistant/conversations');
  releaseCreate(); await createdResponse;
  await expect(newConversation).not.toHaveClass(/ant-btn-loading/);
  await expect(input).toBeHidden();
  await expect(page.getByRole('button', { name: 'AI 创作助手', exact: true })).toHaveAttribute('aria-expanded', 'false');
  await expect(page).toHaveURL(/conversation=301/);
  expect(state.conversations).toHaveLength(2);
  await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
  await expect(input).toHaveValue('保留在当前对话里的草稿');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('history manages project conversations and immediately makes archived current chat read-only', async ({ page }) => {
  const state = await assistantFixture(page);
  await page.goto(`${root}/source?assistant=open`); await expect(page.getByRole('textbox', { name: '给助手的消息' })).toBeVisible();
  await page.getByRole('button', { name: '对话记录', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '对话记录', exact: true });
  await expect(dialog.getByRole('textbox', { name: '搜索对话' })).toHaveAttribute('placeholder', '搜索对话标题');
  await dialog.getByRole('button', { name: '管理对话故事讨论' }).click();
  await page.getByRole('menuitem', { name: '重命名', exact: true }).click();
  const titleInput = dialog.getByRole('textbox', { name: '对话名称' });
  await expect(titleInput).toHaveAttribute('maxlength', '120');
  await titleInput.fill('');
  await titleInput.pressSequentially('a'.repeat(121));
  await expect(titleInput).toHaveValue('a'.repeat(120));
  await titleInput.fill('修改后的讨论'); await dialog.getByRole('button', { name: '保存', exact: true }).click();
  await expect(dialog.locator('.ai-assistant-history-entry strong')).toHaveText('修改后的讨论');
  await dialog.getByRole('button', { name: '管理对话修改后的讨论' }).click(); await page.getByRole('menuitem', { name: '归档', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '归档对话', exact: true }); await confirmation.getByRole('button', { name: '归档', exact: true }).click();
  await expect.poll(() => state.conversations[0].archived).toBe(true);
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '给助手的消息' })).toBeDisabled();
  await page.getByRole('button', { name: '新对话', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '给助手的消息' })).toBeEditable(); await expect(page).toHaveURL(/conversation=302/);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
