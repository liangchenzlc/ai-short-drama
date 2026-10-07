import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';
const time = '2026-10-03T00:00:00Z';
const deferred = () => { let resolve!: () => void; const promise = new Promise<void>(done => { resolve = done; }); return { promise, resolve }; };

async function runtimeFixture(page: Page, verified = true, history = 0, legacyOnly = false) {
  const base = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: '9007199254740993', username: 'creator', display_name: 'Creator', email: 'creator@example.test', email_verified: true } } }));
  const calls: { path: string; method: string; body: any; key?: string }[] = [];
  const cursors: number[] = [];
  const servedEvents: number[] = [];
  const models = [{ id: '71', name: '创作协作模型', model_key: 'fixture-agent', row_version: '1', protocol: 'chat', verified, tool_calling: verified, tool_result_continuation: verified, streaming: 'verified', preferred: true }];
  const conversations = ['301', '302'].map((id, i) => ({ id, project_id: '10', episode_id: legacyOnly ? '20' : null, title: i ? '备选方向' : '主线讨论', row_version: '1', archived: false, last_run_status: null, created_at: time, updated_at: time, stage: legacyOnly ? 'source' : null, subject_type: legacyOnly ? 'episode' : null, subject_id: legacyOnly ? '20' : null, task_type: legacyOnly ? 'writing' : null, scope_version: legacyOnly ? 1 : 2 }));
  const messages: Record<string, any[]> = { '301': Array.from({ length: history }, (_, i) => ({ id: String(1001 + i), seq: i + 1, role: i % 2 ? 'assistant' : 'user', content: `历史消息 ${i + 1}：雨夜中的车站。${'保持故事与角色之间的张力。'.repeat(8)}`, references: [], artifacts: [], created_at: time })), '302': [] };
  const runs: Record<string, any | null> = { '301': null, '302': null };
  const queues: Record<string, any[]> = { '301': [], '302': [] };
  const events: any[] = [];
  let seq = 0;
  let postGate: ReturnType<typeof deferred> | null = null;
  let postStarted: ReturnType<typeof deferred> | null = null;
  let controlGate: ReturnType<typeof deferred> | null = null;
  let controlStarted: ReturnType<typeof deferred> | null = null;
  let abortNextPost = false;
  let accessEnded = false;
  const emit = (event_type: string, run_id: string | null, payload: any = {}) => events.push({ seq: ++seq, event_type, run_id, payload, created_at: time });
  const makeRun = (id: string, mode = 'discuss') => ({ id: '901', conversation_id: id, status: 'running', phase: 'model', row_version: '1', mode: mode === 'generate' ? 'workflow' : 'discuss', model_config_id: '71', model_name: '创作协作模型', error: null, usage: { decision_calls: 1, output_tokens: 20 }, budget: { decision_calls: 8 }, review: null, created_at: time, updated_at: time, finished_at: null });
  await page.route(/\/api\/v1\/(?:agent|assistant)\//, async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname.replace(/^\/api\/v1\/(?:agent|assistant)/, ''); const method = request.method();
    const body = method === 'POST' || method === 'PATCH' ? request.postDataJSON() : null;
    calls.push({ path, method, body, key: request.headers()['idempotency-key'] });
    const reply = (data: any, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path === '/status') return reply({ enabled: true, schema_ready: true });
    if (path === '/models') return reply({ items: models, preferred_id: '71' });
    if (path === '/models/71/verify') { Object.assign(models[0], { verified: true, tool_calling: true, tool_result_continuation: true }); return reply(models[0]); }
    if (path === '/conversations/resolve') return reply(conversations[0]);
    if (path === '/conversations') return reply({ items: conversations, total: 2, offset: 0, limit: 20 });
    if (/^\/conversations\/\d+\/attachments$/.test(path) && method === 'GET') return reply({ items: [], total: 0, offset: 0, limit: 50 });
    const conversationId = path.split('/')[2];
    if (/^\/conversations\/\d+$/.test(path)) return reply(conversations.find(item => item.id === conversationId));
    if (path.endsWith('/events')) {
      const cursor = Number(url.searchParams.get('cursor') || 0); cursors.push(cursor);
      const replay = events.filter(event => event.seq > cursor); servedEvents.push(replay.length);
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: accessEnded ? 'event: access-ended\ndata: {"code":"agent_access_ended"}\n\n' : replay.map(event => `id: ${event.seq}\nevent: agent\ndata: ${JSON.stringify(event)}\n\n`).join('') + ': heartbeat\n\n' });
    }
    if (path.endsWith('/messages')) {
      if (method === 'POST') {
        if (abortNextPost) { abortNextPost = false; return route.abort('failed'); }
        postStarted?.resolve(); if (postGate) await postGate.promise;
        const items = messages[conversationId]; const message = { id: String(2001 + items.length), seq: items.length + 1, role: 'user', content: body.content, references: [], artifacts: [], created_at: time };
        items.push(message);
        const active = runs[conversationId];
        const queued = active && ['running', 'waiting_review', 'waiting_generation'].includes(active.status);
        const run = queued ? { ...makeRun(conversationId), id: String(902 + queues[conversationId].length), status: 'queued', phase: 'queue', queue_position: queues[conversationId].length + 1, waiting_reason: '等待当前运行完成' } : makeRun(conversationId, body.mode);
        if (queued) queues[conversationId].push(run); else runs[conversationId] = run;
        emit('message.created', run.id, { role: 'user' }); emit(queued ? 'run.queued' : 'run.started', run.id);
        return reply({ message, run, cursor: seq }, 201);
      }
      const offset = Number(url.searchParams.get('offset') || 0); const items = messages[conversationId];
      return reply({ items: items.slice(Math.max(0, items.length - offset - 50), Math.max(0, items.length - offset)), total: items.length, offset, limit: 50 });
    }
    if (path.endsWith('/state')) {
      const activeIds = [runs[conversationId], ...queues[conversationId]].filter(run => run && ['queued', 'running', 'waiting_generation', 'waiting_review'].includes(run.status)).map(run => run.id);
      const first = events.find(event => activeIds.includes(event.run_id));
      return reply({ conversation_id: conversationId, cursor: seq, resume_cursor: first ? first.seq - 1 : seq, active_run: runs[conversationId] && ['queued', 'running', 'waiting_review', 'waiting_generation'].includes(runs[conversationId].status) ? runs[conversationId] : null, queued_runs: queues[conversationId] });
    }
    if (path.endsWith('/runs')) return reply({ items: runs[conversationId] ? [runs[conversationId]] : [], total: runs[conversationId] ? 1 : 0, offset: 0, limit: 1 });
    if (path.startsWith('/runs/901')) {
      const run = Object.values(runs).find(item => item?.id === '901')!;
      if (path.endsWith('/stop') || path.includes('/reviews/')) { controlStarted?.resolve(); if (controlGate) await controlGate.promise; }
      if (path.endsWith('/stop')) { Object.assign(run, { status: 'cancelled', finished_at: time, review: null }); emit('run.finished', run.id); }
      if (path.includes('/reviews/')) { Object.assign(run, { status: body.decision === 'approved' ? 'waiting_generation' : 'cancelled', phase: 'wait', review: null }); emit('run.waiting', run.id); }
      return reply(run);
    }
    base.unexpected.push(`${method} agent${path}`); return reply({ error: { code: 'UNEXPECTED' } }, 501);
  });
  return { ...base, calls, cursors, servedEvents, messages, runs, queues, events, emit, makeRun, abortNextSend: () => { abortNextPost = true; }, endAccess: () => { accessEnded = true; },
    gateControl: () => { controlGate = deferred(); controlStarted = deferred(); return { started: controlStarted.promise, release: controlGate.resolve }; },
    gatePost: () => { postGate = deferred(); postStarted = deferred(); return { started: postStarted.promise, release: postGate.resolve }; } };
}

test('unverified models send pure chat without capability probes or creation tools', async ({ page }) => {
  const state = await runtimeFixture(page, false); await page.goto(`${root}/source?assistant=open&conversation=301`);
  const composer = page.getByRole('textbox', { name: '给助手的消息' }); await expect(composer).toBeVisible();
  await composer.fill('分析结尾的悬念'); await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  expect(state.calls.filter(call => call.method === 'POST')).toEqual([]);
  await page.getByRole('button', { name: '发送', exact: true }).click(); await expect(composer).toHaveValue('');
  const send = state.calls.find(call => call.path.endsWith('/messages') && call.method === 'POST')!;
  expect(send.key).toBeTruthy(); expect(send.body.context).toMatchObject({ kind: 'episode', id: '20', revision: '1', stage: 'source' });
  expect(send.body).not.toHaveProperty('mode'); expect(send.body).not.toHaveProperty('expected_scope'); expect(send.body).not.toHaveProperty('task');
  expect(state.calls.filter(call => call.path.endsWith('/verify'))).toEqual([]);
  await page.getByRole('button', { name: '停止', exact: true }).click(); await expect(page.getByRole('button', { name: '停止', exact: true })).toHaveCount(0);
  expect(state.calls.filter(call => call.path.endsWith('/stop'))).toHaveLength(1); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('running project chats queue supplements and restore drafts after refresh', async ({ page }) => {
  const state = await runtimeFixture(page, false); state.runs['301'] = state.makeRun('301');
  await page.goto(`${root}/source?assistant=open&conversation=301`);
  const composer = page.getByRole('textbox', { name: '给助手的消息', exact: true }); await composer.fill('补充人物动机');
  await page.getByRole('button', { name: '发送', exact: true }).click(); await expect(composer).toHaveValue('');
  await expect(page.getByLabel('排队消息')).toContainText('排队位置 1'); expect(state.runs['301'].id).toBe('901'); expect(state.queues['301'][0].id).toBe('902');
  await composer.fill('尚未发送的草稿'); await page.reload(); await expect(composer).toHaveValue('尚未发送的草稿'); await expect(page.getByLabel('排队消息')).toContainText('排队位置 1');
  expect(state.calls.filter(call => call.path.includes('/reviews/'))).toEqual([]); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('historical plans remain explicitly approved and stoppable without a message composer', async ({ page }, info) => {
  const state = await runtimeFixture(page, false, 0, true);
  state.runs['301'] = { ...state.makeRun('301'), status: 'waiting_review', phase: 'wait', review: {
    tool_call_id: '951', review_version: '2', review_hash: 'a'.repeat(64), title: '雨夜结尾制作计划', summary: '保留车站对白。',
    steps: [{ id: '1', kind: 'script', target_id: null, instructions: '保留人物克制对白。', count: 1, model_config_id: '71', model_name: '创作协作模型', source: { secret: 'DO_NOT_RENDER' }, parameters: { secret: 'DO_NOT_RENDER' } }],
  } };
  await page.goto(`${root}/source?mode=agent&conversation=301`); await expect(page.getByRole('region', { name: '待确认创作计划' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '创作要求' })).toHaveCount(0); expect(state.calls.filter(call => call.method === 'POST')).toEqual([]); await expect(page.getByText('DO_NOT_RENDER')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('historical-plan-1440.png'), fullPage: true });
  await page.getByRole('button', { name: '批准并生成' }).click(); await expect(page.getByRole('region', { name: '待确认创作计划' })).toHaveCount(0);
  expect(state.calls.find(call => call.path.includes('/reviews/'))?.body).toEqual({ review_version: '2', review_hash: 'a'.repeat(64), decision: 'approved' });
  await page.setViewportSize({ width: 390, height: 900 }); await expect(page.getByRole('dialog', { name: 'AI 创作助手', exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('historical-running-390.png'), fullPage: true });
  await page.getByRole('button', { name: '停止运行', exact: true }).click(); await expect(page.getByRole('button', { name: '停止运行', exact: true })).toHaveCount(0);
  expect(state.requests.filter(call => call.path.startsWith('/ai/generations/') && call.method === 'POST')).toEqual([]); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('previously queued historical supplements keep the old plan approval locked', async ({ page }) => {
  const state = await runtimeFixture(page, false, 0, true);
  state.runs['301'] = { ...state.makeRun('301'), status: 'waiting_review', review: { tool_call_id: '951', review_version: '2', review_hash: 'a'.repeat(64), title: '待修订计划', summary: '保留人物姓名。', steps: [] } };
  state.queues['301'].push({ ...state.makeRun('301'), id: '902', status: 'queued', queue_position: 1 });
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await expect(page.getByRole('button', { name: '批准并生成' })).toBeDisabled(); await expect(page.getByRole('button', { name: '拒绝计划' })).toBeDisabled(); await expect(page.getByRole('textbox', { name: '创作要求' })).toHaveCount(0);
  expect(state.calls.filter(call => call.path.includes('/reviews/'))).toEqual([]); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const action of ['stop', 'review'] as const) test(`late ${action} response cannot restore a private run after access-ended`, async ({ page }) => {
  const state = await runtimeFixture(page, true, 2, true); const gate = state.gateControl();
  state.runs['301'] = { ...state.makeRun('301'), ...(action === 'review' ? { status: 'waiting_review', review: {
    tool_call_id: '951', review_version: '1', review_hash: 'c'.repeat(64), title: '图片制作计划', summary: '为本镜生成画面。',
    steps: [{ id: '1', kind: 'image', target_kind: 'shot', target_id: '41', target_label: '第 2 镜头 · 雨夜站台', instructions: '雨夜中的车站', count: 2, model_config_id: '71', model_name: '图片创作模型', source: {}, parameters: { resolution: '480p', duration_ms: 5000, aspect: '16:9', layout: 'grid4', reference_media_ids: ['61', '62'], secret: 'DO_NOT_RENDER' } }],
  } } : {}) };
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  if (action === 'review') {
    await expect(page.getByText('图片创作模型 · 第 2 镜头 · 雨夜站台')).toBeVisible();
    await expect(page.getByText('清晰度 480p · 画幅 16:9 · 时长 5 秒 · 布局 四宫格 · 参考图片 2 张')).toBeVisible();
    await expect(page.getByText('DO_NOT_RENDER')).toHaveCount(0);
  }
  await page.getByRole('button', { name: action === 'stop' ? '停止运行' : '批准并生成', exact: true }).click(); await gate.started;
  state.endAccess(); await expect(page.getByText('对话访问已结束，请核对登录与项目权限。')).toBeVisible();
  const response = page.waitForResponse(result => result.request().method() === 'POST' && result.url().includes(action === 'stop' ? '/stop' : '/reviews/'));
  gate.release(); await response;
  await expect(page.locator('.agent-runtime-status')).toContainText('可以开始对话');
  await expect(page.locator('[data-message-id]')).toHaveCount(0); await expect(page.locator('.agent-usage')).toHaveCount(0);
  await expect(page.getByRole('region', { name: '待确认创作计划' })).toHaveCount(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('save failure retains the composer and sends no assistant message', async ({ page }) => {
  const state = await runtimeFixture(page);
  await page.route(`**/api/v1${root}/novel`, route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'SAVE_UNAVAILABLE' } }) }));
  await page.goto(`${root}/source?assistant=open&conversation=301`);
  const composer = page.getByRole('textbox', { name: '给助手的消息' }); await expect(composer).toBeVisible();
  await page.getByRole('textbox', { name: '本集小说正文' }).fill('尚未保存的雨夜故事。');
  await composer.fill('围绕刚改的故事讨论结尾');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText('当前正文尚未保存成功，请处理保存提示后再发送。')).toBeVisible();
  await expect(composer).toHaveValue('围绕刚改的故事讨论结尾');
  expect(state.calls.filter(call => call.path.endsWith('/messages') && call.method === 'POST')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('late send preserves another conversation and its draft', async ({ page }) => {
  const state = await runtimeFixture(page); const gate = state.gatePost();
  await page.goto(`${root}/source?assistant=open&conversation=302`);
  const composer = page.getByRole('textbox', { name: '给助手的消息' }); await expect(composer).toBeVisible(); await composer.fill('B 的草稿');
  await page.getByRole('button', { name: '对话记录', exact: true }).click();
  await page.locator('.ai-assistant-history-entry').filter({ hasText: '主线讨论' }).click();
  await expect(composer).toHaveValue(''); await composer.fill('A 的讨论要求');
  await page.getByRole('button', { name: '发送', exact: true }).click(); await gate.started;
  await page.evaluate(() => { history.pushState({}, '', '?assistant=open&conversation=302'); window.dispatchEvent(new PopStateEvent('popstate')); }); await expect(composer).toHaveValue('B 的草稿');
  const response = page.waitForResponse(result => result.url().endsWith('/assistant/conversations/301/messages') && result.request().method() === 'POST');
  gate.release(); await response;
  await expect(page).toHaveURL(url => url.searchParams.get('conversation') === '302');
  await expect(composer).toHaveValue('B 的草稿');
  await page.getByRole('button', { name: '对话记录', exact: true }).click();
  await expect(page.locator('.ai-assistant-history-list article.is-current')).toContainText('备选方向');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('replayed events use a persistent cursor and preserve upward reading position', async ({ page }) => {
  const state = await runtimeFixture(page, true, 60);
  state.runs['301'] = state.makeRun('301');
  state.emit('assistant.delta', '901', { run_id: '901', turn_id: '981', delta: '🎬', offset: 0 });
  state.emit('assistant.delta', '901', { run_id: '901', turn_id: '981', delta: '雨夜', offset: 1 });
  await page.goto(`${root}/source?assistant=open&conversation=301`);
  const transcript = page.locator('.ai-assistant-log'); await expect(page.getByText('🎬雨夜', { exact: true })).toBeVisible();
  await transcript.evaluate(node => { node.scrollTop = 160; node.dispatchEvent(new Event('scroll')); });
  const before = await transcript.evaluate(node => node.scrollTop);
  state.emit('assistant.delta', '901', { run_id: '901', turn_id: '981', delta: '中的车站', offset: 3 });
  await expect(page.getByRole('button', { name: '查看最新消息' })).toBeVisible();
  expect(await transcript.evaluate(node => node.scrollTop)).toBeCloseTo(before, 0);
  await expect.poll(() => state.cursors.some(cursor => cursor >= 2)).toBe(true);
  await page.getByRole('button', { name: '载入更早消息' }).click();
  await expect(page.locator('[data-message-id="1001"]')).toBeAttached();
  Object.assign(state.runs['301'], { status: 'succeeded', finished_at: time }); state.emit('run.finished', '901');
  await expect(page.getByText('🎬雨夜中的车站', { exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('an uncertain send only retries explicitly with the original body and idempotency key', async ({ page }) => {
  const state = await runtimeFixture(page); state.abortNextSend();
  await page.goto(`${root}/source?assistant=open&conversation=301`);
  const composer = page.getByRole('textbox', { name: '给助手的消息' }); await expect(composer).toBeVisible();
  await composer.fill('先讨论雨夜的结尾'); await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText('发送结果尚未确认，草稿已保留。请使用原请求核对，避免重复调用。')).toBeVisible();
  await expect(composer).toHaveValue('先讨论雨夜的结尾');
  await composer.fill('继续编辑的草稿');
  await page.locator('.ai-assistant-uncertain').getByRole('button', { name: '核对状态', exact: true }).click();
  const sends = () => state.calls.filter(call => call.path.endsWith('/messages') && call.method === 'POST');
  expect(sends()).toHaveLength(1); await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '使用原请求核对', exact: true }).click();
  await expect(page.getByRole('button', { name: '使用原请求核对', exact: true })).toHaveCount(0);
  expect(sends()).toHaveLength(2); expect(sends()[1].body).toEqual(sends()[0].body); expect(sends()[1].key).toBe(sends()[0].key);
  await expect(composer).toHaveValue('继续编辑的草稿');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('reopening a long conversation skips completed history and recovers the active reply across reconnects', async ({ page }, info) => {
  const state = await runtimeFixture(page);
  for (let i = 0; i < 10000; i++) state.emit('run.started', String(10000 + i));
  state.runs['301'] = state.makeRun('301');
  state.emit('assistant.delta', '901', { turn_id: '981', delta: '保留雨夜', offset: 0 });
  await page.goto(`${root}/source?assistant=open&conversation=301`);
  await expect(page.locator('.ai-assistant-log > .ai-assistant-reply p')).toHaveText('保留雨夜');
  expect(state.cursors[0]).toBe(10000); expect(state.servedEvents[0]).toBe(1);
  const firstState = state.calls.findIndex(call => call.path.endsWith('/state'));
  const firstMessages = state.calls.findIndex(call => call.path.endsWith('/messages') && call.method === 'GET');
  expect(firstState).toBeLessThan(firstMessages);
  state.emit('assistant.delta', '901', { turn_id: '981', delta: '中的车站', offset: 4 });
  await expect(page.locator('.ai-assistant-log > .ai-assistant-reply p')).toHaveText('保留雨夜中的车站');
  expect(state.servedEvents.reduce((sum, count) => sum + count, 0)).toBe(2);
  await info.attach('long-conversation-recovery-counts', { body: Buffer.from(JSON.stringify({ historical_events: 10000, initial_cursor: state.cursors[0], delivered_events: state.servedEvents.reduce((sum, count) => sum + count, 0) })), contentType: 'application/json' });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('rejecting a plan sends the reviewed version without starting generation', async ({ page }) => {
  const state = await runtimeFixture(page, true, 0, true);
  state.runs['301'] = { ...state.makeRun('301'), status: 'waiting_review', phase: 'wait', review: {
    tool_call_id: '951', review_version: '4', review_hash: 'b'.repeat(64), title: '待拒绝计划', summary: '改写整集。',
    steps: [{ id: '1', kind: 'novel', target_id: null, instructions: '重新生成小说', count: 1, model_config_id: '71', model_name: '创作协作模型', source: {}, parameters: {} }],
  } };
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await page.getByRole('button', { name: '拒绝计划', exact: true }).click();
  await expect(page.getByRole('region', { name: '待确认创作计划' })).toHaveCount(0);
  expect(state.calls.filter(call => call.path.includes('/reviews/')).map(call => call.body)).toEqual([{ review_version: '4', review_hash: 'b'.repeat(64), decision: 'rejected' }]);
  expect(state.calls.filter(call => call.path.endsWith('/messages') && call.method === 'POST')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('access-ended clears private history and stops automatic reconnects', async ({ page }) => {
  const state = await runtimeFixture(page, true, 2);
  await page.goto(`${root}/source?assistant=open&conversation=301`);
  await expect(page.locator('[data-message-id="1001"]')).toBeVisible();
  state.endAccess();
  await expect(page.getByText('对话访问已结束，请核对登录与项目权限。')).toBeVisible();
  await expect(page.locator('[data-message-id]')).toHaveCount(0); await expect(page.locator('.ai-assistant-log .ant-skeleton')).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: '给助手的消息' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled();
  expect(state.calls.filter(call => call.method === 'POST')).toEqual([]);
  const connections = state.cursors.length;
  await page.getByRole('button', { name: '核对状态', exact: true }).click();
  await expect.poll(() => state.cursors.length).toBe(connections + 1);
  await expect(page.getByText('对话访问已结束，请核对登录与项目权限。')).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('Shift+Enter makes a newline and Enter sends outside IME composition', async ({ page }) => {
  const state = await runtimeFixture(page); await page.goto(`${root}/source?assistant=open&conversation=301`);
  const composer = page.getByRole('textbox', { name: '给助手的消息' }); await expect(composer).toBeVisible();
  await composer.fill('第一行'); await composer.press('Shift+Enter'); await expect(composer).toHaveValue('第一行\n'); await page.keyboard.insertText('第二行');
  await expect(composer).toHaveValue('第一行\n第二行');
  const sends = () => state.calls.filter(call => call.path.endsWith('/messages') && call.method === 'POST'); expect(sends()).toEqual([]);
  await composer.dispatchEvent('compositionstart'); await composer.press('Enter'); expect(sends()).toEqual([]);
  await composer.dispatchEvent('compositionend'); await composer.press('Enter'); await expect(composer).toHaveValue('');
  expect(sends()).toHaveLength(1); expect(sends()[0].body.content).toBe('第一行\n第二行'); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
