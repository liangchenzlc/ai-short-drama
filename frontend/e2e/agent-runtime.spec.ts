import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';
const time = '2026-10-03T00:00:00Z';
const deferred = () => { let resolve!: () => void; const promise = new Promise<void>(done => { resolve = done; }); return { promise, resolve }; };

async function runtimeFixture(page: Page, verified = true, history = 0) {
  const base = await fixture(page, true);
  const calls: { path: string; method: string; body: any; key?: string }[] = [];
  const cursors: number[] = [];
  const models = [{ id: '71', name: '创作协作模型', model_key: 'fixture-agent', row_version: 1, protocol: 'chat', verified, tool_calling: verified, tool_result_continuation: verified, streaming: 'verified', preferred: true }];
  const conversations = ['301', '302'].map((id, i) => ({ id, project_id: '10', episode_id: '20', title: i ? '备选方向' : '主线讨论', row_version: 1, archived: false, last_run_status: null, created_at: time, updated_at: time }));
  const messages: Record<string, any[]> = { '301': Array.from({ length: history }, (_, i) => ({ id: String(1001 + i), seq: i + 1, role: i % 2 ? 'assistant' : 'user', content: `历史消息 ${i + 1}：雨夜中的车站。${'保持故事与角色之间的张力。'.repeat(8)}`, references: [], artifacts: [], created_at: time })), '302': [] };
  const runs: Record<string, any | null> = { '301': null, '302': null };
  const events: any[] = [];
  let seq = 0;
  let postGate: ReturnType<typeof deferred> | null = null;
  let postStarted: ReturnType<typeof deferred> | null = null;
  let controlGate: ReturnType<typeof deferred> | null = null;
  let controlStarted: ReturnType<typeof deferred> | null = null;
  let abortNextPost = false;
  let accessEnded = false;
  const emit = (event_type: string, run_id: string | null, payload: any = {}) => events.push({ seq: ++seq, event_type, run_id, payload, created_at: time });
  const makeRun = (id: string, mode = 'discuss') => ({ id: '901', conversation_id: id, status: 'running', phase: 'model', row_version: 1, mode: mode === 'generate' ? 'workflow' : 'discuss', model_config_id: '71', model_name: '创作协作模型', error: null, usage: { decision_calls: 1, output_tokens: 20 }, budget: { decision_calls: 8 }, review: null, created_at: time, updated_at: time, finished_at: null });
  await page.route('**/api/v1/agent/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname.slice('/api/v1/agent'.length); const method = request.method();
    const body = method === 'POST' || method === 'PATCH' ? request.postDataJSON() : null;
    calls.push({ path, method, body, key: request.headers()['idempotency-key'] });
    const reply = (data: any, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path === '/status') return reply({ enabled: true, schema_ready: true });
    if (path === '/models') return reply({ items: models, preferred_id: '71' });
    if (path === '/models/71/verify') { Object.assign(models[0], { verified: true, tool_calling: true, tool_result_continuation: true }); return reply(models[0]); }
    if (path === '/conversations') return reply({ items: conversations, total: 2, offset: 0, limit: 20 });
    if (/^\/conversations\/\d+\/attachments$/.test(path) && method === 'GET') return reply({ items: [], total: 0, offset: 0, limit: 50 });
    const conversationId = path.split('/')[2];
    if (/^\/conversations\/\d+$/.test(path)) return reply(conversations.find(item => item.id === conversationId));
    if (path.endsWith('/events')) {
      const cursor = Number(url.searchParams.get('cursor') || 0); cursors.push(cursor);
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: accessEnded ? 'event: access-ended\ndata: {"code":"agent_access_ended"}\n\n' : events.filter(event => event.seq > cursor).map(event => `id: ${event.seq}\nevent: agent\ndata: ${JSON.stringify(event)}\n\n`).join('') + ': heartbeat\n\n' });
    }
    if (path.endsWith('/messages')) {
      if (method === 'POST') {
        if (abortNextPost) { abortNextPost = false; return route.abort('failed'); }
        postStarted?.resolve(); if (postGate) await postGate.promise;
        const items = messages[conversationId]; const message = { id: String(2001 + items.length), seq: items.length + 1, role: 'user', content: body.content, references: [], artifacts: [], created_at: time };
        items.push(message); runs[conversationId] = makeRun(conversationId, body.mode);
        emit('message.created', '901', { role: 'user' }); emit('run.started', '901');
        return reply({ message, run: runs[conversationId], cursor: seq }, 201);
      }
      const offset = Number(url.searchParams.get('offset') || 0); const items = messages[conversationId];
      return reply({ items: items.slice(Math.max(0, items.length - offset - 50), Math.max(0, items.length - offset)), total: items.length, offset, limit: 50 });
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
  return { ...base, calls, cursors, messages, runs, events, emit, makeRun, abortNextSend: () => { abortNextPost = true; }, endAccess: () => { accessEnded = true; },
    gateControl: () => { controlGate = deferred(); controlStarted = deferred(); return { started: controlStarted.promise, release: controlGate.resolve }; },
    gatePost: () => { postGate = deferred(); postStarted = deferred(); return { started: postStarted.promise, release: postGate.resolve }; } };
}

async function selectOption(page: Page, name: string, label: string | RegExp) {
  await page.getByRole('combobox', { name, exact: true }).press('ArrowDown');
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: label }).first().click();
}
async function chooseGeneration(page: Page) {
  await page.getByRole('button', { name: '选择发送用途', exact: true }).click();
  await page.getByRole('menuitem', { name: '生成作品', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Agent 模型与执行设置', exact: true })).toBeVisible();
}
async function finishModelSettings(page: Page) {
  await page.getByRole('dialog', { name: 'Agent 模型与执行设置', exact: true }).getByRole('button', { name: '完成', exact: true }).click();
}
async function mediaModels(page: Page) {
  await page.route('**/api/v1/ai-model-configs/*/capabilities', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ known: true, reference_images: true, parameters: ['aspect', 'resolution', 'count'], video_input: { reference_images: true, first_frame: false, duration_seconds: [3, 5, 10], resolutions: ['480p', '720p', '1080p'] } }) }));
  await page.route('**/api/v1/ai-model-configs/77', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: '77', name: '图片生成模型', service_type: 'image', model_key: 'fixture-image', provider: 'fixture', enabled: 1, is_deleted: 0, is_default: 1, row_version: '1', has_api_key: true }) }));
  await page.route('**/api/v1/ai-model-configs/78', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: '78', name: '视频生成模型', service_type: 'video', model_key: 'fixture-video', provider: 'fixture', enabled: 1, is_deleted: 0, is_default: 1, row_version: '1', has_api_key: true }) }));
  await page.route('**/api/v1/ai-model-configs?*', route => {
    const kind = new URL(route.request().url()).searchParams.get('service_type');
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ id: kind === 'video' ? '78' : '77', name: kind === 'video' ? '视频生成模型' : '图片生成模型', service_type: kind, model_key: `fixture-${kind}`, provider: 'fixture', enabled: 1, is_deleted: 0, is_default: 1, row_version: '1', has_api_key: true }], total: 1, offset: 0, limit: 100 }) });
  });
}

test('a specified asset image task confirms concrete settings and keeps the collaboration model separate', async ({ page }) => {
  const state = await runtimeFixture(page); await mediaModels(page);
  state.referenceState['asset/501'] = [{ media_id: '991', name: '角色参考', url: '' }];
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  const composer = page.getByRole('textbox', { name: '创作要求' }); await expect(composer).toBeVisible();
  await chooseGeneration(page);
  await selectOption(page, '生成方式', '指定图片任务');
  await selectOption(page, '媒体创作对象', '角色：林晚');
  await selectOption(page, '媒体清晰度', /^4K$/); await selectOption(page, '媒体画幅', /^9:16$/);
  await page.getByRole('spinbutton', { name: '媒体候选数量' }).fill('2'); await finishModelSettings(page); await composer.fill('保持人物黑发与深色风衣，生成雨夜中的角色肖像。');
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  expect(state.calls.filter(call => call.method === 'POST')).toEqual([]);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '确认生成任务' }); await expect(confirmation).toContainText('角色：林晚');
  await expect(confirmation).toContainText('清晰度 4K · 画幅 9:16 · 参考图片 1 张'); await expect(confirmation).toContainText('2 张图片');
  expect(state.calls.filter(call => call.method === 'POST')).toEqual([]);
  await expect(confirmation.getByRole('button', { name: '取消', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(composer).toHaveValue('保持人物黑发与深色风衣，生成雨夜中的角色肖像。');
  expect(state.calls.filter(call => call.method === 'POST')).toEqual([]);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await confirmation.getByRole('button', { name: '确认并生成' }).click(); await expect(composer).toHaveValue('');
  const send = state.calls.find(call => call.path.endsWith('/messages') && call.method === 'POST')!;
  expect(send.body).toMatchObject({ mode: 'generate', model_config_id: '71', task: { kind: 'image', target_id: '501', model_config_id: '77', count: 2, parameters: { target_kind: 'asset', resolution: '4K', aspect: '9:16', reference_media_ids: ['991'] } } });
  expect(send.body.task.parameters.layout).toBeUndefined(); expect(state.requests.filter(call => call.path === '/ai/generations/image' && call.method === 'POST')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('a specified shot image task includes linked and saved references without exposing internal IDs', async ({ page }) => {
  const state = await runtimeFixture(page); await mediaModels(page);
  state.referenceState['shot/101'] = [{ media_id: '9901', name: '重复的已采用图', url: '' }, { media_id: '991', name: '运镜参考', url: '' }];
  await page.goto(`${root}/source?mode=agent&conversation=301`); const composer = page.getByRole('textbox', { name: '创作要求' }); await expect(composer).toBeVisible();
  await chooseGeneration(page); await selectOption(page, '生成方式', '指定图片任务');
  await selectOption(page, '生成对象类型', '分镜画面'); await selectOption(page, '媒体创作对象', /^第 1 镜头 ·/);
  await selectOption(page, '图片布局', /^四宫格$/); await finishModelSettings(page); await composer.fill('用四个画面表现旅人走进站台。');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '确认生成任务' }); await expect(confirmation).toContainText('第 1 镜头'); await expect(confirmation).toContainText('布局 四宫格 · 参考图片 2 张');
  await confirmation.getByRole('button', { name: '确认并生成' }).click(); await expect(composer).toHaveValue('');
  const send = state.calls.find(call => call.path.endsWith('/messages') && call.method === 'POST')!;
  expect(send.body.task).toMatchObject({ kind: 'image', target_id: '101', parameters: { target_kind: 'shot', aspect: '16:9', resolution: '2K', layout: 'four', reference_media_ids: ['9901', '991'] } });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('a specified video task offers 480p and five seconds with explicit authorization', async ({ page }, info) => {
  const state = await runtimeFixture(page); await mediaModels(page);
  await page.goto(`${root}/source?mode=agent&conversation=301`); const composer = page.getByRole('textbox', { name: '创作要求' }); await expect(composer).toBeVisible();
  await chooseGeneration(page); await selectOption(page, '生成方式', '指定视频任务');
  await selectOption(page, '媒体创作对象', /^第 1 镜头 ·/);
  await expect(page.getByRole('spinbutton', { name: '视频时长（秒）' })).toHaveValue('3');
  await expect(page.locator('.agent-media-task').getByText('沿用目标（720p）', { exact: true })).toBeVisible();
  await selectOption(page, '媒体清晰度', /^480p$/); await page.getByRole('spinbutton', { name: '视频时长（秒）' }).fill('5');
  await finishModelSettings(page);
  await composer.fill('保持旅人外貌与雨夜站台，镜头缓慢向前推进。');
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  await expect(page.locator('.ant-select-dropdown:visible')).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: info.outputPath('media-task-1440.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 900 }); await page.getByRole('tab', { name: 'AI 创作', exact: true }).click(); await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: info.outputPath('media-task-390.png'), fullPage: true });
  await page.getByRole('button', { name: '发送', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '确认生成任务' }); await expect(confirmation).toContainText('清晰度 480p · 画幅 16:9 · 时长 5 秒 · 参考图片 1 张');
  await expect(confirmation).toContainText('视频生成模型'); expect(state.calls.filter(call => call.method === 'POST')).toEqual([]);
  await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: info.outputPath('media-confirm-390.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 }); await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: info.outputPath('media-confirm-1440.png'), fullPage: true });
  await confirmation.getByRole('button', { name: '确认并生成' }).click(); await expect(composer).toHaveValue('');
  const send = state.calls.find(call => call.path.endsWith('/messages') && call.method === 'POST')!;
  expect(send.body).toMatchObject({ mode: 'generate', model_config_id: '71', task: { kind: 'video', target_id: '101', model_config_id: '78', count: 1, parameters: { target_kind: 'shot', aspect: '16:9', resolution: '480p', duration_ms: 5000 } } });
  expect(send.body.task.parameters.reference_media_ids).toBeUndefined(); expect(state.requests.filter(call => call.path === '/ai/generations/video' && call.method === 'POST')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('a video reference that becomes stale during preparation keeps the draft and makes no paid request', async ({ page }) => {
  const state = await runtimeFixture(page); await mediaModels(page);
  await page.goto(`${root}/source?mode=agent&conversation=301`); const composer = page.getByRole('textbox', { name: '创作要求' }); await expect(composer).toBeVisible();
  await chooseGeneration(page); await selectOption(page, '生成方式', '指定视频任务');
  await selectOption(page, '媒体创作对象', /^第 1 镜头 ·/); await finishModelSettings(page); await composer.fill('保持人物外貌，镜头缓慢推进。');
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  state.shots[0].image.is_stale = true;
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText('分镜内容已变化，请先核对并采用当前内容的分镜图。', { exact: true })).toBeVisible();
  await expect(composer).toHaveValue('保持人物外貌，镜头缓慢推进。');
  await expect(page.getByRole('dialog', { name: '确认生成任务' })).toHaveCount(0);
  expect(state.calls.filter(call => call.method === 'POST')).toEqual([]); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('explicit capability verification, IME, plan approval and stop stay user initiated', async ({ page }, info) => {
  const state = await runtimeFixture(page, false);
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  const composer = page.getByRole('textbox', { name: '创作要求' }); await expect(composer).toBeVisible();
  await composer.fill('把结尾改成悬念，先给我制作计划。');
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled();
  expect(state.calls.filter(call => call.method === 'POST')).toEqual([]);
  await page.getByRole('button', { name: '选择模型', exact: true }).click();
  await page.getByRole('button', { name: '校验能力', exact: true }).click();
  await expect(page.getByText(/最多会向这个文本模型发送 2 次请求/)).toBeVisible();
  expect(state.calls.filter(call => call.path.endsWith('/verify'))).toEqual([]);
  await page.getByRole('button', { name: '开始校验' }).click();
  await expect(page.getByRole('dialog', { name: 'Agent 模型与执行设置', exact: true }).getByRole('button', { name: '校验能力', exact: true })).toBeEnabled();
  expect(state.calls.filter(call => call.path.endsWith('/verify'))).toHaveLength(1);
  await finishModelSettings(page);
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  await composer.dispatchEvent('compositionstart'); await composer.press('Enter');
  expect(state.calls.filter(call => call.path.endsWith('/messages') && call.method === 'POST')).toEqual([]);
  await composer.dispatchEvent('compositionend');
  await page.getByRole('button', { name: '选择发送用途', exact: true }).click();
  await page.getByRole('menuitem', { name: '生成作品', exact: true }).click();
  await finishModelSettings(page);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(composer).toHaveValue('');
  const send = state.calls.find(call => call.path.endsWith('/messages') && call.method === 'POST')!;
  expect(send.key).toBeTruthy(); expect(send.body.mode).toBe('generate'); expect(send.body.task).toBeUndefined();
  Object.assign(state.runs['301'], { status: 'waiting_review', phase: 'wait', review: { tool_call_id: '951', review_version: 2, review_hash: 'a'.repeat(64), title: '雨夜结尾制作计划', summary: '保留车站对白，强化最后一封信的悬念。', steps: [{ id: '1', kind: 'script', target_id: null, instructions: '改写最后一幕，保留人物的克制对白。', count: 1, model_config_id: '71', model_name: '创作协作模型', source: { secret: 'DO_NOT_RENDER' }, parameters: { secret: 'DO_NOT_RENDER' } }] } });
  state.emit('run.waiting', '901');
  await expect(page.getByRole('region', { name: '待确认创作计划' })).toBeVisible();
  expect(state.calls.filter(call => call.path.includes('/reviews/'))).toEqual([]);
  await expect(page.getByText('DO_NOT_RENDER')).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: info.outputPath('agent-plan-1440.png'), fullPage: true });
  await page.getByRole('button', { name: '批准并生成' }).click();
  await expect(page.getByRole('region', { name: '待确认创作计划' })).toHaveCount(0);
  expect(state.calls.find(call => call.path.includes('/reviews/'))?.body).toEqual({ review_version: 2, review_hash: 'a'.repeat(64), decision: 'approved' });
  await page.setViewportSize({ width: 390, height: 900 });
  await page.getByRole('tab', { name: 'AI 创作', exact: true }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: info.outputPath('agent-running-390.png'), fullPage: true });
  await page.getByRole('button', { name: '停止运行', exact: true }).click();
  await expect(page.getByRole('button', { name: '停止运行', exact: true })).toHaveCount(0);
  expect(state.calls.filter(call => call.path.endsWith('/stop'))).toHaveLength(1);
  expect(state.requests.filter(call => call.path.startsWith('/ai/generations/') && call.method === 'POST')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const action of ['stop', 'review'] as const) test(`late ${action} response cannot restore a private run after access-ended`, async ({ page }) => {
  const state = await runtimeFixture(page, true, 2); const gate = state.gateControl();
  state.runs['301'] = { ...state.makeRun('301'), ...(action === 'review' ? { status: 'waiting_review', review: {
    tool_call_id: '951', review_version: 1, review_hash: 'c'.repeat(64), title: '图片制作计划', summary: '为本镜生成画面。',
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

test('save failure retains the composer and sends no Agent request', async ({ page }) => {
  const state = await runtimeFixture(page);
  await page.route(`**/api/v1${root}/novel`, route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'SAVE_UNAVAILABLE' } }) }));
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  const composer = page.getByRole('textbox', { name: '创作要求' }); await expect(composer).toBeVisible();
  await page.getByRole('textbox', { name: '本集小说正文' }).fill('尚未保存的雨夜故事。');
  await composer.fill('围绕刚改的故事讨论结尾');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText('当前作品尚未保存成功。对话草稿已保留，请处理保存提示后再发送。')).toBeVisible();
  await expect(composer).toHaveValue('围绕刚改的故事讨论结尾');
  expect(state.calls.filter(call => call.path.endsWith('/messages') && call.method === 'POST')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('late send preserves another conversation and its draft', async ({ page }) => {
  const state = await runtimeFixture(page); const gate = state.gatePost();
  await page.goto(`${root}/source?mode=agent&conversation=302`);
  const composer = page.getByRole('textbox', { name: '创作要求' }); await expect(composer).toBeVisible(); await composer.fill('B 的草稿');
  await page.getByRole('button', { name: '对话记录', exact: true }).click();
  await page.locator('.agent-conversation-toolbar .ant-select-selection-item').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '主线讨论' }).click();
  await expect(composer).toHaveValue(''); await composer.fill('A 的讨论要求');
  await page.getByRole('button', { name: '发送', exact: true }).click(); await gate.started;
  await page.goBack(); await expect(composer).toHaveValue('B 的草稿');
  const response = page.waitForResponse(result => result.url().endsWith('/agent/conversations/301/messages') && result.request().method() === 'POST');
  gate.release(); await response;
  await expect(page).toHaveURL(url => url.searchParams.get('conversation') === '302');
  await expect(composer).toHaveValue('B 的草稿');
  await page.getByRole('button', { name: '对话记录', exact: true }).click();
  await expect(page.locator('.agent-conversation-toolbar .ant-select-selection-item')).toHaveText('备选方向');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('replayed events use a persistent cursor and preserve upward reading position', async ({ page }) => {
  const state = await runtimeFixture(page, true, 60);
  state.runs['301'] = state.makeRun('301');
  state.emit('assistant.delta', '901', { run_id: '901', turn_id: '981', delta: '🎬', offset: 0 });
  state.emit('assistant.delta', '901', { run_id: '901', turn_id: '981', delta: '雨夜', offset: 1 });
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  const transcript = page.locator('.agent-transcript'); await expect(page.getByText('🎬雨夜', { exact: true })).toBeVisible();
  await transcript.evaluate(node => { node.scrollTop = 160; node.dispatchEvent(new Event('scroll')); });
  const before = await transcript.evaluate(node => node.scrollTop);
  state.emit('assistant.delta', '901', { run_id: '901', turn_id: '981', delta: '中的车站', offset: 3 });
  await expect(page.getByRole('button', { name: '查看最新消息' })).toBeVisible();
  expect(await transcript.evaluate(node => node.scrollTop)).toBeCloseTo(before, 0);
  await expect.poll(() => state.cursors.some(cursor => cursor >= 2)).toBe(true);
  await page.getByRole('button', { name: '载入更早消息' }).click();
  await expect(page.locator('[data-message-id="1001"]')).toBeAttached();
  Object.assign(state.runs['301'], { status: 'succeeded', finished_at: time }); state.emit('run.finished', '901');
  await expect(page.locator('.is-streaming')).toHaveCount(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('an uncertain send only retries explicitly with the original body and idempotency key', async ({ page }) => {
  const state = await runtimeFixture(page); state.abortNextSend();
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  const composer = page.getByRole('textbox', { name: '创作要求' }); await expect(composer).toBeVisible();
  await composer.fill('先讨论雨夜的结尾'); await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText('发送结果尚未确认，草稿已保留。先核对消息与运行状态，避免重复请求。')).toBeVisible();
  await expect(composer).toHaveValue('先讨论雨夜的结尾');
  await composer.fill('继续编辑的草稿');
  await page.getByRole('button', { name: '核对发送状态', exact: true }).click();
  const sends = () => state.calls.filter(call => call.path.endsWith('/messages') && call.method === 'POST');
  expect(sends()).toHaveLength(1); await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '使用原请求重试', exact: true }).click();
  await expect(page.getByRole('button', { name: '使用原请求重试', exact: true })).toHaveCount(0);
  expect(sends()).toHaveLength(2); expect(sends()[1].body).toEqual(sends()[0].body); expect(sends()[1].key).toBe(sends()[0].key);
  await expect(composer).toHaveValue('继续编辑的草稿');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('rejecting a plan sends the reviewed version without starting generation', async ({ page }) => {
  const state = await runtimeFixture(page);
  state.runs['301'] = { ...state.makeRun('301'), status: 'waiting_review', phase: 'wait', review: {
    tool_call_id: '951', review_version: 4, review_hash: 'b'.repeat(64), title: '待拒绝计划', summary: '改写整集。',
    steps: [{ id: '1', kind: 'novel', target_id: null, instructions: '重新生成小说', count: 1, model_config_id: '71', model_name: '创作协作模型', source: {}, parameters: {} }],
  } };
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await page.getByRole('button', { name: '拒绝计划', exact: true }).click();
  await expect(page.getByRole('region', { name: '待确认创作计划' })).toHaveCount(0);
  expect(state.calls.filter(call => call.path.includes('/reviews/')).map(call => call.body)).toEqual([{ review_version: 4, review_hash: 'b'.repeat(64), decision: 'rejected' }]);
  expect(state.calls.filter(call => call.path.endsWith('/messages') && call.method === 'POST')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('access-ended clears private history and stops automatic reconnects', async ({ page }) => {
  const state = await runtimeFixture(page, true, 2);
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await expect(page.locator('[data-message-id="1001"]')).toBeVisible();
  state.endAccess();
  await expect(page.getByText('对话访问已结束，请核对登录与项目权限。')).toBeVisible();
  await expect(page.locator('[data-message-id]')).toHaveCount(0); await expect(page.locator('.agent-transcript .ant-skeleton')).toHaveCount(0);
  const composer = page.getByRole('textbox', { name: '创作要求' }); await composer.fill('访问已结束时的草稿');
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled();
  expect(state.calls.filter(call => call.method === 'POST')).toEqual([]);
  const connections = state.cursors.length;
  await page.getByRole('button', { name: '核对状态', exact: true }).click();
  await expect.poll(() => state.cursors.length).toBe(connections + 1);
  await expect(page.getByText('对话访问已结束，请核对登录与项目权限。')).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const modifier of ['Control', 'Meta']) test(`Enter makes a newline and ${modifier}+Enter sends outside IME composition`, async ({ page }) => {
  const state = await runtimeFixture(page); await page.goto(`${root}/source?mode=agent&conversation=301`);
  const composer = page.getByRole('textbox', { name: '创作要求' }); await expect(composer).toBeVisible();
  await composer.fill('第一行'); await composer.press('Enter'); await expect(composer).toHaveValue('第一行\n'); await page.keyboard.insertText('第二行');
  await expect(composer).toHaveValue('第一行\n第二行');
  const sends = () => state.calls.filter(call => call.path.endsWith('/messages') && call.method === 'POST');
  expect(sends()).toEqual([]);
  await composer.dispatchEvent('compositionstart'); await composer.press(`${modifier}+Enter`); expect(sends()).toEqual([]);
  await composer.dispatchEvent('compositionend'); await composer.press(`${modifier}+Enter`);
  await expect(composer).toHaveValue(''); expect(sends()).toHaveLength(1); expect(sends()[0].body.content).toBe('第一行\n第二行');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
