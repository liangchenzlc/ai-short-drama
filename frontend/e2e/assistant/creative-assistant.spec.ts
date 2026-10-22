import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from '../support/studio-fixture';
import { checkPrimaryButtonContrast } from '../support/button-contrast';

async function openSaveDetails(page: Page) {
  const entry = page.getByRole('button', { name: /^保存状态：/ });
  if (await entry.getAttribute('aria-expanded') !== 'true') await entry.click();
  return page.locator('.standard-save-details');
}

const time = '2026-10-06T12:00:00Z';
async function assistantFixture(page: Page) {
  const base = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: '9007199254740993', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true } } }));
  const conversation = (id: string, project_id = '10') => ({ id, project_id, title: id === '301' ? '故事讨论' : '新对话', scope_version: 3, stage: null, subject_type: null, subject_id: null, task_type: null, row_version: '1', status: 'active', archived: false, last_run_status: null, created_at: time, updated_at: time });
  const conversations = [conversation('301')];
  const messages: Record<string, any[]> = { '301': [] };
  const attachments: any[] = []; const tasks: any[] = []; const operations: any[] = []; const inputs: any[] = [];
  const subjectOptions: any[] = [{ project_id: '10', source_kind: 'project', source_id: '10', kind: 'asset', id: '501', revision: '1', name: '林晚', project_name: '测试项目', source_name: '测试项目' }];
  const subject = (item: any) => ({ ...item, subject_key: `subject:${item.id.padStart(64, '0')}`, snapshot_id: 'b'.repeat(64), checksum: 'a'.repeat(64), allowed_fields: ['name', 'description'], availability: 'current', current_revision: item.revision, current_checksum: 'a'.repeat(64) });
  const deletionMemories: any[] = [];
  const calls: { path: string; method: string; body: any; key: string }[] = [];
  const receipts = new Map<string, any>();
  const discussionRevisions: any[] = [];
  let abortRevision = false; let conflictRevision = false; let historyDenied = false;
  let abortSend = false; let abortControl = false; let abortDeletion = false; let activeRun: any = null; let latestRun: any = null;
  const queuedRuns: any[] = []; const events: any[] = [];
  const cursors: number[] = [];
  let nextStreamFrames: string | null = null;
  let nextStreamStatus = 200;
  let snapshotGate: { started: () => void; wait: Promise<void> } | null = null;
  const run = (id: string, status: string, task_id = '501', queue_position = 0) => ({ id, task_id, conversation_id: '301', status, phase: status === 'succeeded' ? 'finished' : 'model', row_version: '1', mode: 'discuss', model_config_id: '71', model_name: '对话模型', error: null, usage: {}, budget: {}, review: null, awaiting_artifact_ids: [], created_at: time, updated_at: time, finished_at: status === 'succeeded' ? time : null, queue_position, waiting_reason: null });
  const task = (id = '501', goal = '讨论故事', status = 'running', conversation_id = '301') => ({ id, conversation_id, goal, status, phase: status === 'succeeded' ? 'terminal' : 'deciding', row_version: '1', plan_revision: '1', input_revision: '1', applied_input_revision: '1', control_seq: '0', applied_control_seq: '0', control_state: 'none', execution_target: { mode: 'standard', project_id: '10' }, memory_project_ids: ['10'], source_context: null, selected_subjects: [], skills: [], media_limits: { images: null, videos: null, audio: null }, media_usage: {}, active_operation: null, error: null, created_at: time, updated_at: time });
  const skills = [{ id: 'builtin:continuity', name: '人物连续性', builtin: true, content_version: '1', row_version: null, enabled: true, filename: null, checksum_sha256: 'a'.repeat(64), package_checksum: 'b'.repeat(64), slug: 'continuity', description: '核对人物动机与连续性。', tags: [], modes: ['standard', 'infinite_canvas'], exclusions: '', required_tools: [], source: '', invocation_mode: 'auto', settings_version: '0', compatible: true, issues: [], resource_count: 0, updated_at: time }];
  const emit = (event_type: string, payload: any, run_id = '901') => events.push({ seq: events.length + 1, event_type, run_id, payload, created_at: time });
  await page.route('**/api/v1/agent/**', route => {
    const path = new URL(route.request().url()).pathname.slice('/api/v1/agent'.length);
    if (path === '/status') return route.fulfill({ json: { enabled: true, schema_ready: true } });
    if (path === '/models') return route.fulfill({ json: { items: [{ id: '71', name: '对话模型', row_version: '1', model_key: 'fixture', protocol: 'chat', verified: false, preferred: true }], preferred_id: '71' } });
    if (path === '/skills') {
      const params = new URL(route.request().url()).searchParams;
      const filtered = skills.filter(item => (!params.get('query') || item.name.includes(params.get('query')!) || item.description.includes(params.get('query')!))
        && (!params.get('source') || item.builtin === (params.get('source') === 'builtin')));
      const offset = Number(params.get('offset') ?? 0);
      return route.fulfill({ json: { items: filtered.slice(offset, offset + 50), offset, limit: 50, total: filtered.length } });
    }
    return route.fallback();
  });
  await page.route('**/api/v1/assistant/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname.slice('/api/v1/assistant'.length);
    const method = request.method(); const body = ['POST', 'PATCH', 'DELETE'].includes(method) && request.headers()['content-type']?.includes('application/json') ? request.postDataJSON() : null;
    const key = request.headers()['idempotency-key']; calls.push({ path, method, body, key });
    const reply = (json: any, status = 200) => route.fulfill({ json, status });
    if (path === '/subjects/metadata') return reply(body.subjects.map((item: any) => ({ ...item, checksum: 'a'.repeat(64), ...(item.kind === 'node' ? { content_revision: item.revision } : {}) })));
    if (path === '/subjects') {
      const filtered = subjectOptions.filter(item => item.project_id === url.searchParams.get('project_id') && item.kind === url.searchParams.get('kind') && (!url.searchParams.get('query') || item.name.includes(url.searchParams.get('query'))));
      const offset = Number(url.searchParams.get('offset') ?? 0);
      return reply({ items: filtered.slice(offset, offset + 20), total: filtered.length, offset, limit: 20 });
    }
    if (path === '/global/conversations' && method === 'POST') { const next = conversation(String(301 + conversations.length), body.project_id); conversations.push(next); messages[next.id] = []; return reply(next, 201); }
    if (path === '/global/conversations') { const items = conversations.filter(item => item.status !== 'deleted' && item.project_id === url.searchParams.get('project_id') && (!url.searchParams.get('query') || item.title.includes(url.searchParams.get('query')!))); return reply({ items, total: items.length, offset: 0, limit: 20 }); }
    if (path === '/global/history') return reply({ items: [], total: 0, offset: 0, limit: 20 });
    const parts = path.split('/'); const id = path.startsWith('/global/') ? parts[3] : parts[2];
    if (/\/messages\/\d+\/revision-eligibility$/.test(path)) {
      const source = messages[id].find(item => item.id === parts[5]);
      return reply({ allowed: !historyDenied, code: historyDenied ? 'assistant_history_dependency' : null, reason: historyDenied ? '此讨论已有工具或已保存记忆依赖，请发送新消息纠正' : null, row_version: source.row_version ?? '1', current_content: source.content });
    }
    if (/\/messages\/\d+\/revisions$/.test(path)) {
      const source = messages[id].find(item => item.id === parts[5]);
      if (method === 'GET') {
        const all = discussionRevisions.filter(item => item.message_id === source.id); const offset = Number(url.searchParams.get('offset') ?? 0);
        return reply({ items: all.slice(offset, offset + 30), total: all.length, offset, limit: 30 });
      }
      if (receipts.has(key)) return reply(receipts.get(key), 201);
      if (conflictRevision) { conflictRevision = false; source.row_version = '2'; source.content = '其他标签页保存的新版本'; }
      if (body.row_version !== (source.row_version ?? '1')) return reply({ error: { code: 'assistant_history_changed', message: '消息版本已变化，请保留草稿并重新核对' } }, 409);
      if (historyDenied) return reply({ error: { code: 'assistant_history_dependency', message: '请发送新消息纠正' } }, 409);
      const answer = messages[id].find(item => item.task_id === source.task_id && item.role === 'assistant' && item.seq > source.seq);
      const previousSource = { ...source }; const previousReply = { ...answer };
      if (body.kind === 'edit') { source.content = body.content; source.row_version = String(BigInt(source.row_version ?? '1') + 1n); }
      answer.content = '修订后的讨论回复'; answer.row_version = String(BigInt(answer.row_version ?? '1') + 1n);
      const current = tasks.find(item => item.id === source.task_id); current.status = 'succeeded'; current.goal = source.content; current.row_version = String(BigInt(current.row_version) + 1n);
      latestRun = run(String(10000 + discussionRevisions.length), 'succeeded', current.id);
      for (const message of [previousSource, previousReply]) discussionRevisions.unshift({ id: String(20000 + discussionRevisions.length), message_id: message.id, kind: body.kind, row_version: message.row_version ?? '1', content: message.content, source_message_id: source.id, source_version: source.row_version ?? '1', run_id: latestRun.id, created_at: time });
      emit('message.updated', { id: answer.id, row_version: answer.row_version }, latestRun.id);
      const result = { message: source, task: current, run: latestRun, cursor: events.length }; receipts.set(key, result);
      if (abortRevision) { abortRevision = false; return route.abort('failed'); }
      return reply(result, 201);
    }
    if (path.endsWith('/deletion-memory')) return reply({ conversation_id: id, row_version: conversations.find(item => item.id === id)!.row_version, items: deletionMemories, total: deletionMemories.length, offset: 0, limit: 30 });
    if (/^\/global\/conversations\/\d+$/.test(path)) {
      if (method === 'DELETE' && receipts.has(key)) return reply(receipts.get(key));
      const item = conversations.find(item => item.id === id)!;
      if (!item || item.status === 'deleted') return reply({ error: { code: 'not_found', message: '对话不存在' } }, 404);
      if (method === 'PATCH') Object.assign(item, body, { row_version: String(BigInt(item.row_version) + 1n) });
      if (method === 'DELETE') {
        if ((body.memory_selections ?? []).some((selection: any) => !deletionMemories.some(item => item.id === selection.id && item.content_version === selection.content_version))) return reply({ error: { code: 'conflict', message: '待清理记忆已变化，请重新预览并明确选择；对话尚未删除' } }, 409);
        item.status = operations.some(op => ['running', 'waiting_media', 'reconciling'].includes(op.outcome)) ? 'deleting' : 'deleted'; receipts.set(key, item);
        if (abortDeletion) { abortDeletion = false; return route.abort('failed'); }
      }
      return reply(item);
    }
    if (path.endsWith('/events')) {
      const cursor = Number(url.searchParams.get('cursor') ?? 0); cursors.push(cursor);
      if (nextStreamFrames !== null) {
        const body = nextStreamFrames; const status = nextStreamStatus; nextStreamFrames = null;
        return route.fulfill({ status, contentType: status === 200 ? 'text/event-stream' : 'application/json', body });
      }
      return route.fulfill({ contentType: 'text/event-stream', body: events.filter(event => event.seq > cursor).map(event => `data: ${JSON.stringify(event)}\n\n`).join('') || ': heartbeat\n\n' });
    }
    if (path.endsWith('/state')) {
      const all = messages[id] ?? []; const runs = [...new Map([activeRun, ...queuedRuns, latestRun].filter(Boolean).map(value => [value.id, value])).values()];
      const body = JSON.stringify({ conversation_id: id, snapshot_cursor: events.length, resume_cursor: 0, messages: { items: all.slice(-50), total: all.length, offset: 0, limit: 50 }, tasks: tasks.filter(item => item.conversation_id === id), runs, operations, inputs, has_more_tasks: false, has_more_operations: false });
      if (snapshotGate) { const gate = snapshotGate; snapshotGate = null; gate.started(); await gate.wait; }
      return route.fulfill({ contentType: 'application/json', body });
    }
    if (/^\/tasks\/\d+\/operations$/.test(path)) return reply({ items: operations.filter(item => item.task_id === id), has_more: false, next_after_id: null, snapshot_cursor: events.length });
    if (/^\/tasks\/\d+\/subjects$/.test(path)) {
      const item = tasks.find(item => item.id === id)!;
      return reply({ task_id: id, input_revision: item.input_revision, applied_input_revision: item.applied_input_revision, pending: false, items: item.selected_subjects });
    }
    if (/^\/tasks\/\d+\/attachments$/.test(path)) {
      const item = tasks.find(item => item.id === id)!;
      return reply({ task_id: id, input_revision: item.input_revision, applied_input_revision: item.applied_input_revision, pending: false, items: item.task_attachments ?? [] });
    }
    if (/^\/operations\/\d+$/.test(path)) return reply({ ...operations.find(item => item.id === id), result: { target: null, write_authorized: false } });
    if (/^\/tasks\/\d+\/control$/.test(path)) {
      if (receipts.has(key)) return reply(receipts.get(key));
      const item = tasks.find(item => item.id === id)!;
      if (body.kind === 'resume' && body.expected_control_seq !== item.control_seq) return reply({ error: { code: 'CONFLICT', message: '停止或暂停状态已变化，请核对当前任务后继续' } }, 409);
      const reused = body.message_id ? messages[item.conversation_id].find(message => message.id === body.message_id && message.task_id === id && message.role === 'user') : null;
      if (body.message_id && !reused) return reply({ error: { code: 'NOT_FOUND' } }, 404);
      item.control_seq = String(BigInt(item.control_seq) + 1n); item.row_version = String(BigInt(item.row_version) + 1n);
      const started = operations.find(op => op.task_id === id && ['running', 'waiting_media', 'reconciling'].includes(op.outcome));
      if (body.kind === 'stop') { item.control_state = started ? 'stop_pending' : 'none'; item.status = started ? item.status : 'stopped'; if (!started) { activeRun = null; latestRun = run('901', 'cancelled', id); } }
      if (body.kind === 'resume') {
        if (!reused) messages[item.conversation_id].push({ id: String(2001 + messages[item.conversation_id].length), task_id: id, seq: messages[item.conversation_id].length + 1, role: 'user', content: body.content || '继续任务', references: [], artifacts: [], created_at: time });
        item.status = 'queued'; item.control_state = 'none'; latestRun = run('903', 'queued', id);
      }
      if (body.kind === 'redirect') item.control_state = 'redirect_pending';
      const result = { task: item, run: latestRun ?? activeRun, control_id: '1101', cursor: events.length }; receipts.set(key, result);
      if (abortControl) { abortControl = false; return route.abort('failed'); }
      return reply(result);
    }
    if (path.endsWith('/attachments') && method === 'GET') return reply({ items: attachments.filter(item => item.pending), total: attachments.filter(item => item.pending).length, offset: 0, limit: 50 });
    if (path.endsWith('/attachments/references')) {
      const item = { id: '7001', name: '林晚', kind: 'text', mime_type: 'text/plain', byte_size: 20, media_id: null, url: null, text_preview: '角色参考', checksum_sha256: 'b'.repeat(64), pending: true, metadata: { source_id: body.source_id } }; attachments.push(item); return reply(item, 201);
    }
    if (path.endsWith('/messages') && method === 'GET') {
      const all = messages[id] ?? []; const offset = Number(url.searchParams.get('offset') ?? 0);
      return reply({ items: all.slice(Math.max(0, all.length - offset - 50), all.length - offset), total: all.length, offset, limit: 50 });
    }
    if (path.endsWith('/messages') && method === 'POST') {
      if (receipts.has(key)) return reply(receipts.get(key), 201);
      let current = body.task_id ? tasks.find(item => item.id === body.task_id) : null;
      if (!current) { current = task(String(501 + tasks.length), body.content, activeRun ? 'queued' : 'running', id); tasks.push(current); }
      else current.input_revision = String(BigInt(current.input_revision) + 1n);
      if (body.attachment_selection) current.task_attachments = (current.task_attachments ?? []).filter((item: any) => body.attachment_selection.retain_ids.includes(item.id));
      if (body.attachment_ids?.length) {
        current.task_attachments = [...(current.task_attachments ?? []), ...attachments.filter(item => body.attachment_ids.includes(item.id)).map(item => ({ id: item.id, kind: item.kind, name: item.name, checksum_sha256: item.checksum_sha256, available: true }))];
        attachments.forEach(item => { if (body.attachment_ids.includes(item.id)) item.pending = false; });
      }
      if ('execution_target' in body) current.execution_target = body.execution_target;
      if ('skills' in body) current.skills = body.skills.map((reference: any) => ({ ...(current.skills.find((item: any) => item.id === reference.id && item.content_version === reference.content_version) ?? skills.find(item => item.id === reference.id)), ...reference }));
      if (body.memory_project_ids) current.memory_project_ids = body.memory_project_ids;
      if (body.subject_selection) current.selected_subjects = [
        ...current.selected_subjects.filter((item: any) => body.subject_selection.retain_keys.includes(item.subject_key)),
        ...body.subject_selection.subjects.map((item: any) => subject({ ...subjectOptions.find(candidate => candidate.id === item.id), ...item })),
      ];
      const message = { id: String(1001 + messages[id].length), task_id: current.id, seq: messages[id].length + 1, role: 'user', content: body.content, references: [], artifacts: [], created_at: time };
      messages[id].push(message); inputs.push({ task_id: current.id, message_id: message.id, input_revision: current.input_revision, applied_plan_revision: activeRun ? null : '1' });
      if (activeRun || current.status === 'stopped') {
        let acceptedRun = activeRun ?? latestRun;
        if (body.new_task) { acceptedRun = run(String(902 + queuedRuns.length), 'queued', current.id, queuedRuns.length + 1); queuedRuns.push(acceptedRun); }
        const result = { message, run: acceptedRun, task: current, cursor: events.length }; receipts.set(key, result);
        if (abortSend) { abortSend = false; return route.abort('failed'); }
        return reply(result, 201);
      }
      const replyMessage = { ...message, id: String(1002 + messages[id].length), seq: message.seq + 1, role: 'assistant', content: '## 创作建议\n\n保留人物的 **克制**。\n\n<script>window.__unsafe = true</script>\n\n[不安全链接](javascript:alert(1))', references: [], artifacts: [] };
      messages[id].push(replyMessage); attachments.forEach(item => { item.pending = false; }); current.status = 'succeeded';
      latestRun = run('901', 'succeeded', current.id);
      const result = { message, run: latestRun, task: current, cursor: events.length }; receipts.set(key, result);
      if (abortSend) { abortSend = false; return route.abort('failed'); }
      return reply(result, 201);
    }
    base.unexpected.push(`${method} assistant${path}`); return reply({ error: { code: 'UNEXPECTED' } }, 501);
  });
  return { ...base, calls, conversations, messages, attachments, tasks, operations, skills, deletionMemories, subjectOptions, subject, cursors, emit,
    abortNextRevision: () => { abortRevision = true; }, conflictNextRevision: () => { conflictRevision = true; }, denyHistory: () => { historyDenied = true; },
    nextStream: (frames: string, status = 200) => { nextStreamFrames = frames; nextStreamStatus = status; },
    delaySnapshot: () => {
      let release!: () => void; let started!: () => void;
      const wait = new Promise<void>(resolve => { release = resolve; });
      const requested = new Promise<void>(resolve => { started = resolve; });
      snapshotGate = { started, wait }; return { requested, release };
    },
    abortNextSend: () => { abortSend = true; }, abortNextControl: () => { abortControl = true; }, abortNextDeletion: () => { abortDeletion = true; },
    beginReply: (text: string) => {
      if (!tasks.length) tasks.push(task()); activeRun = run('901', 'running'); latestRun = activeRun;
      emit('assistant.delta', { turn_id: 'reply-901', offset: 0, delta: text });
    },
    startOperation: () => {
      const op = { id: '801', task_id: '501', run_id: '901', call_id: '601', tool_name: 'describe_creation_target', tool_version: '1', plan_revision: '1', state_version: '1', phase: 'tool_running', outcome: 'running', result_refs: [], error: null, created_at: time, finished_at: null };
      operations.push(op); tasks[0].active_operation = op; tasks[0].phase = 'tool_running';
      emit('step.started', { ...op, operation_id: op.id, trace_version: '1' });
    },
    finishOperation: () => {
      Object.assign(operations[0], { outcome: 'succeeded', phase: 'terminal', state_version: '2', finished_at: time });
      Object.assign(tasks[0], { status: 'stopped', phase: 'terminal', control_state: 'none', active_operation: null, row_version: '3' });
      emit('step.finished', { ...operations[0], operation_id: operations[0].id, trace_version: '2' }); activeRun = null; latestRun = run('901', 'cancelled');
    },
    finishReply: (text: string) => {
      messages['301'].push({ id: 'reply-901', task_id: '501', seq: messages['301'].length + 1, role: 'assistant', content: text, references: [], artifacts: [], created_at: time });
      tasks[0].status = 'succeeded'; activeRun = null; latestRun = run('901', 'succeeded');
    },
  };
}

for (const width of [1440, 390]) test(`composer menus offer scoped attachments and multiple Skills without configuration dialogs at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  state.skills.push({ ...state.skills[0], id: '801', builtin: false, name: '对白节奏' });
  await page.goto(`${root}/source?assistant=open`);
  await page.getByRole('button', { name: '添加附件', exact: true }).click();
  const menu = page.getByRole('menu');
  await expect(menu.getByRole('menuitem')).toHaveCount(4);
  await expect(menu).toContainText('图片'); await expect(menu).toContainText('文本');
  await expect(menu.getByRole('menuitem', { name: /从当前画布选择/ })).toHaveAttribute('aria-disabled', 'true');
  await expect(menu).toContainText('仅画布模式可用');
  await page.keyboard.press('Escape');
  const trigger = page.getByRole('button', { name: '选择 Skill', exact: true });
  await trigger.click();
  const list = page.getByRole('region', { name: '选择 Skill', exact: true });
  await list.getByRole('checkbox', { name: '人物连续性', exact: true }).check();
  await list.getByRole('checkbox', { name: '对白节奏', exact: true }).check();
  await expect(list).toBeVisible(); await expect(list).toContainText('已选择 2 / 8');
  await list.getByRole('checkbox', { name: '对白节奏', exact: true }).uncheck();
  await expect(list).toContainText('已选择 1 / 8');
  const bounds = (await list.boundingBox())!;
  expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
  await page.screenshot({ path: info.outputPath(`composer-menus-${width}.png`) });
  await expect(page.getByRole('dialog', { name: /Skill/ })).toHaveCount(0);
  await page.keyboard.press('Escape'); await expect(trigger).toBeFocused();
  await expect(page.getByRole('region', { name: 'AI 创作助手', exact: true })).toBeVisible();
  await trigger.click();
  await page.getByRole('region', { name: 'AI 创作助手', exact: true }).locator('header').click({ position: { x: 70, y: 15 } });
  await expect(list).toHaveCount(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('image and text menu actions use the existing upload channel and retain the draft', async ({ page }) => {
  const state = await assistantFixture(page);
  const uploads: { key?: string; body: string }[] = [];
  await page.route('**/api/v1/assistant/conversations/301/attachments/uploads', route => {
    if (route.request().method() !== 'POST') return route.fallback();
    uploads.push({ key: route.request().headers()['idempotency-key'], body: route.request().postData() ?? '' });
    const image = uploads.length === 1;
    const item = { id: String(7000 + uploads.length), name: image ? '参考图.png' : '故事资料.md', kind: image ? 'image' : 'text', mime_type: image ? 'image/png' : 'text/markdown', byte_size: 20, media_id: null, url: null, text_preview: image ? null : '故事参考', checksum_sha256: 'b'.repeat(64), pending: true, metadata: {} };
    state.attachments.push(item); return route.fulfill({ status: 201, json: item });
  });
  await page.goto(`${root}/source?assistant=open`);
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await input.fill('保留上传时的输入');
  for (const kind of ['image', 'text']) {
    await page.getByRole('button', { name: '添加附件', exact: true }).click();
    const chooserReady = page.waitForEvent('filechooser');
    await page.getByRole('menuitem', { name: kind === 'image' ? '图片' : '文本', exact: true }).click();
    const chooser = await chooserReady;
    expect(await chooser.element().getAttribute('accept')).toMatch(kind === 'image' ? /image\/png/ : /text\/markdown/);
    await chooser.setFiles({ name: kind === 'image' ? '参考图.png' : '故事资料.md', mimeType: kind === 'image' ? 'image/png' : 'text/markdown', buffer: Buffer.from(kind === 'image' ? 'fixture-png' : '# 故事参考') });
    await expect(page.getByLabel('本次消息附件', { exact: true })).toContainText(kind === 'image' ? '参考图.png' : '故事资料.md');
    await expect(input).toHaveValue('保留上传时的输入');
  }
  expect(uploads).toHaveLength(2); expect(uploads.every(item => item.key && item.body.includes('filename='))).toBe(true);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(input).toHaveValue('');
  expect(state.calls.find(call => call.method === 'POST' && call.path.endsWith('/messages'))!.body.attachment_ids).toEqual(['7001', '7002']);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`attachment recovery restores clickable sending for a current project conversation at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  let restoreFails = true;
  await page.route('**/api/v1/assistant/conversations/301/attachments**', route => {
    if (restoreFails && route.request().method() === 'GET') return route.fulfill({ status: 409, json: { error: { code: 'assistant_legacy_readonly', message: '旧对话仅供查看' } } });
    return route.fallback();
  });
  await page.goto(`${root}/source?assistant=open`);
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  const send = page.getByRole('button', { name: '发送', exact: true });
  await input.fill('为当前项目梳理人物关系');
  await expect(page.getByText('旧对话仅供查看，请新建项目助手对话继续聊天。', { exact: true })).toBeVisible();
  await expect(send).toBeDisabled();
  restoreFails = false;
  await openSaveDetails(page);
  await page.getByRole('button', { name: '重新载入附件', exact: true }).click();
  await page.keyboard.press('Escape');
  await expect(send).toBeEnabled();
  await expect(input).toHaveValue('为当前项目梳理人物关系');
  await expect(page.getByText('旧对话仅供查看，请新建项目助手对话继续聊天。', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: '消息所属任务' })).toHaveCount(0);
  await send.click();
  await expect(input).toHaveValue('');
  const sent = state.calls.find(call => call.method === 'POST' && call.path.endsWith('/messages'))!;
  expect(sent.path).toBe('/global/conversations/301/messages');
  expect(sent.body.execution_target).toEqual({ mode: 'standard', project_id: '10' });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('standard assistant width supports pointer, keyboard, viewport limits and account storage', async ({ page }) => {
  const state = await assistantFixture(page);
  await page.goto(`${root}/source?assistant=open`);
  const handle = page.getByRole('separator', { name: '调整助手宽度', exact: true });
  const sidebar = page.getByRole('complementary', { name: 'AI 创作助手区域', exact: true });
  await expect(handle).toHaveAttribute('aria-valuenow', '380');
  await handle.focus(); await page.keyboard.press('ArrowLeft');
  await expect(handle).toHaveAttribute('aria-valuenow', '396');
  const box = (await handle.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + 120); await page.mouse.down();
  await page.mouse.move(box.x - 74, box.y + 120); await page.mouse.up();
  await expect(handle).toHaveAttribute('aria-valuenow', '476');
  await page.reload(); await expect(handle).toHaveAttribute('aria-valuenow', '476');
  await handle.focus(); await page.keyboard.press('End');
  await expect(handle).toHaveAttribute('aria-valuenow', '560');
  await page.setViewportSize({ width: 1000, height: 900 });
  await expect.poll(async () => (await sidebar.boundingBox())!.width).toBeLessThan(380);
  await expect(handle).toHaveAttribute('aria-valuemin', '320');
  await page.setViewportSize({ width: 390, height: 900 });
  await expect(handle).toHaveCount(0);
  await expect(page.getByRole('dialog', { name: 'AI 创作助手区域', exact: true })).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('project changes restore separate conversations and drafts despite a foreign conversation URL', async ({ page }) => {
  const state = await assistantFixture(page);
  state.conversations.push({ ...state.conversations[0], id: '401', project_id: '11', title: '项目二讨论' });
  state.messages['401'] = [];
  const url = '/e2e/assistant/fixtures/assistant-presentation-fixture.html?layout=standard';
  await page.goto(url);
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await input.fill('项目一的草稿');
  await page.goto(url + '&project=11&conversation=301');
  await expect(input).toHaveValue(''); await input.fill('项目二的草稿');
  await page.goto(url + '&project=10&conversation=401');
  await expect(input).toHaveValue('项目一的草稿');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(input).toHaveValue('');
  const sent = state.calls.find(call => call.method === 'POST' && call.path.endsWith('/messages'))!;
  expect(sent.path).toBe('/global/conversations/301/messages');
  expect(sent.body.execution_target).toEqual({ mode: 'standard', project_id: '10' });
  await page.goto(url + '&project=11'); await expect(input).toHaveValue('项目二的草稿');
  await page.getByRole('button', { name: '新对话', exact: true }).click();
  await expect(input).toHaveValue('');
  expect(state.calls.find(call => call.method === 'POST' && call.path === '/global/conversations')!.body).toEqual({ project_id: '11' });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('shared canvas presentation uses the same anchored Skill list without standard panel classes', async ({ page }) => {
  const state = await assistantFixture(page);
  await page.goto('/e2e/assistant/fixtures/assistant-presentation-fixture.html');
  const panel = page.getByRole('region', { name: 'AI 创作助手', exact: true });
  await expect(panel).not.toHaveClass(/ai-assistant-standard/);
  await panel.getByRole('button', { name: '选择 Skill', exact: true }).click();
  const list = page.getByRole('region', { name: '选择 Skill', exact: true });
  await expect(list.getByRole('checkbox', { name: '人物连续性', exact: true })).toBeVisible();
  await expect(page.getByRole('dialog', { name: /Skill/ })).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(panel.getByRole('button', { name: '选择 Skill', exact: true })).toBeFocused();
  await panel.getByRole('button', { name: '添加附件', exact: true }).click();
  await expect(page.getByRole('menuitem', { name: '从当前画布选择', exact: true })).not.toHaveAttribute('aria-disabled', 'true');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`lightweight methods keep frozen versions and explicit clearing across refresh at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page); state.beginReply('保留旧方法。');
  state.tasks[0].skills = [{ id: state.skills[0].id, name: state.skills[0].name, content_version: '1', builtin: true }];
  state.skills[0].content_version = '2'; state.skills[0].enabled = false;
  state.skills.push({ ...state.skills[0], id: '801', name: '我的对白方法', builtin: false, enabled: true, content_version: '1', invocation_mode: 'manual' });
  await page.goto(`${root}/source?assistant=open`);
  const selected = page.getByLabel('已加载技能', { exact: true });
  await expect(selected).toContainText('人物连续性');
  await expect(selected.locator('.assistant-skill-token')).toHaveAttribute('title', '人物连续性 · v1');
  await page.getByRole('button', { name: '选择 Skill', exact: true }).click();
  const dialog = page.getByRole('region', { name: '选择 Skill', exact: true });
  await expect(dialog.getByRole('textbox', { name: '搜索 Skill', exact: true })).toBeVisible();
  await expect(dialog.getByRole('button', { name: /上传|编辑|停用|删除/ })).toHaveCount(0);
  await expect(dialog.getByRole('link', { name: '管理 Skill', exact: true })).toHaveAttribute('href', '/ai_config?section=skills');
  await expect(dialog.getByText('任务使用 v1；目录 v2', { exact: false })).toBeVisible();
  await dialog.getByRole('checkbox', { name: '我的对白方法', exact: true }).check();
  await page.keyboard.press('Escape');
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await page.getByRole('button', { name: '停止', exact: true }).click();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeVisible();
  await input.fill('保留原方法，加入对白方法');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(input).toHaveValue('');
  expect(state.calls.find(call => call.method === 'POST' && call.path.endsWith('/messages'))!.body.skills).toEqual([{ id: state.skills[0].id, content_version: '1' }, { id: '801', content_version: '1' }]);
  await page.getByRole('button', { name: '停止', exact: true }).click();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeVisible();
  await selected.getByRole('button', { name: '移除方法人物连续性', exact: true }).click();
  await selected.getByRole('button', { name: '移除方法我的对白方法', exact: true }).click();
  await input.fill('清空手动方法后继续');
  await page.reload();
  await expect(input).toHaveValue('清空手动方法后继续');
  await expect(selected).toHaveCount(0);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(input).toHaveValue('');
  const sends = state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'));
  expect(sends.at(-1)!.body).toMatchObject({ task_id: '501', skills: [], content: '清空手动方法后继续' });
  await page.getByRole('button', { name: '选择 Skill', exact: true }).click();
  await expect(dialog.getByRole('checkbox', { name: '我的对白方法', exact: true })).not.toBeChecked();
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath(`lightweight-methods-${width}.png`) });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('lightweight method discovery searches all pages and keeps unavailable reasons visible', async ({ page }) => {
  const state = await assistantFixture(page);
  for (let index = 0; index < 55; index++) state.skills.push({ ...state.skills[0], id: String(800 + index), name: `个人方法 ${index}`, builtin: false });
  state.skills.at(-1)!.compatible = false; state.skills.at(-1)!.issues = ['缺少未提供的工具'];
  await page.goto(`${root}/source?assistant=open`);
  await page.getByRole('button', { name: '选择 Skill', exact: true }).click();
  const dialog = page.getByRole('region', { name: '选择 Skill', exact: true });
  await expect(dialog.getByRole('checkbox', { name: '个人方法 48', exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: '加载更多 Skill', exact: true }).click();
  await expect(dialog.getByRole('checkbox', { name: '个人方法 54', exact: true })).toBeDisabled();
  await expect(dialog.getByText('缺少未提供的工具', { exact: true })).toBeVisible();
  await dialog.getByRole('textbox', { name: '搜索 Skill', exact: true }).fill('个人方法 53');
  await dialog.getByRole('checkbox', { name: '个人方法 53', exact: true }).check();
  await page.keyboard.press('Escape');
  await expect(page.getByLabel('已加载技能', { exact: true })).toContainText('个人方法 53');
  await expect(page.locator('.assistant-skill-token')).toHaveAttribute('title', '个人方法 53 · v1');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`slash Skills confirm highlighted input tokens with real identity and restore across refresh at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  state.skills.splice(0, 1,
    ...['9007199254740993', '9007199254740995'].map(id => ({ ...state.skills[0], id, name: '同名对白方法', builtin: false, invocation_mode: 'manual' })));
  await page.goto(`${root}/source?assistant=open`);
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await input.fill('#同名');
  await expect(page.getByRole('menuitemcheckbox').filter({ hasText: '同名对白方法' })).toHaveCount(0);
  await input.fill('/同名');
  const choices = page.getByRole('menuitemcheckbox').filter({ hasText: '同名对白方法' });
  await expect(choices).toHaveCount(2);
  await expect(choices.nth(1)).toContainText('9007199254740995');
  await input.press('ArrowDown'); await input.press('Enter');
  await expect(input).toHaveValue('');
  const token = page.locator('.ai-assistant-sender .assistant-skill-token');
  await expect(token).toContainText('同名对白方法');
  await expect(token).toHaveAttribute('title', '同名对白方法 · v1');
  await expect(choices).toHaveCount(0);
  expect(state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'))).toHaveLength(0);
  await page.screenshot({ path: info.outputPath(`skill-token-${width}.png`) });
  await input.fill('用所选方法分析对白');
  await page.reload();
  await expect(input).toHaveValue('用所选方法分析对白'); await expect(token).toBeVisible();
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(input).toHaveValue('');
  expect(state.calls.find(call => call.method === 'POST' && call.path.endsWith('/messages'))!.body.skills).toEqual([{ id: '9007199254740995', content_version: '1' }]);
  await input.fill('/不存在');
  await page.getByRole('menuitemcheckbox').filter({ hasText: '浏览全部 Skill' }).click();
  const dialog = page.getByRole('region', { name: '选择 Skill', exact: true });
  await expect(dialog.getByRole('textbox', { name: '搜索 Skill', exact: true })).toBeVisible();
  await expect(dialog.getByRole('textbox', { name: '搜索 Skill', exact: true })).toBeFocused();
  await page.screenshot({ path: info.outputPath(`skill-mentions-${width}.png`) });
  await page.keyboard.press('Escape'); await expect(page.getByRole('button', { name: '选择 Skill', exact: true })).toBeFocused();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('mobile novel settings and assistant remain mutually exclusive while preserving adaptation input', async ({ page }) => {
  const state = await assistantFixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${root}/source`);
  await page.getByRole('button', { name: '展开设置', exact: true }).click();
  const prompt = page.getByRole('textbox', { name: '创作要求', exact: true });
  await prompt.fill('保留手机设置里的输入');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
  const assistant = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await expect(assistant).toBeVisible();
  await expect(prompt).not.toBeVisible();
  await assistant.fill('保留助手未发送的输入');
  await page.keyboard.press('Escape');
  await expect(assistant).not.toBeVisible();
  await page.getByRole('button', { name: '展开设置', exact: true }).click();
  await expect(prompt).toHaveValue('保留手机设置里的输入');
  await expect(assistant).not.toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
  await expect(assistant).toHaveValue('保留助手未发送的输入');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('global composer freezes explicitly selected subjects, Skills and safe Markdown', async ({ page }, info) => {
  const state = await assistantFixture(page);
  await page.goto(`${root}/source?assistant=open`);
  const input = page.getByRole('textbox', { name: '给助手的消息' }); await expect(input).toBeVisible();
  await input.fill('@林');
  await page.getByRole('menuitemcheckbox', { name: '林晚 素材', exact: true }).click();
  await expect(page.getByLabel('当前任务引用', { exact: true })).toContainText('林晚');
  await page.getByRole('button', { name: '选择 Skill', exact: true }).click();
  const skillDialog = page.getByRole('region', { name: '选择 Skill', exact: true });
  await skillDialog.getByRole('checkbox', { name: '人物连续性' }).check();
  await page.keyboard.press('Escape');
  await expect(page.getByLabel('已加载技能')).toContainText('人物连续性');
  await input.fill('一起分析人物动机');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(input).toHaveValue('');
  const sent = state.calls.find(call => call.method === 'POST' && call.path.endsWith('/messages'))!;
  expect(sent.body).toMatchObject({ content: '一起分析人物动机', new_task: true, model_config_id: '71', skills: [{ id: 'builtin:continuity', content_version: '1' }], subject_selection: { expected_input_revision: '0', retain_keys: [], subjects: [{ kind: 'asset', id: '501', project_id: '10', source_id: '10', revision: '1', source_kind: 'project' }] } });
  expect(sent.body).not.toHaveProperty('context');
  expect(sent.body).not.toHaveProperty('mode'); expect(sent.body).not.toHaveProperty('task');
  await expect(page.locator('.ai-assistant-reply h2')).toHaveText('创作建议');
  await expect(page.locator('.ai-assistant-reply strong')).toHaveText('克制');
  await expect(page.locator('.ai-assistant-reply script,.ai-assistant-reply a[href^="javascript:"]')).toHaveCount(0);
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

async function undoFixture(page: Page) {
  const state = await assistantFixture(page);
  state.beginReply('结构已保存。'); state.startOperation();
  state.messages['301'].push({ id: 'undo-question', task_id: '501', seq: 1, role: 'user', content: '创建两个角色', references: [], artifacts: [], created_at: time });
  Object.assign(state.operations[0], { id: '9007199254740999', tool_name: 'apply_standard_patch', result_refs: [{ kind: 'asset', id: '9007199254740993', project_id: '10', name: '新角色', row_version: '1', status: 'created' }] });
  state.finishOperation();
  const calls: any[] = [], receipts: any[] = []; const accepted = new Map<string, any>();
  let hash = 'a'.repeat(64), abort = false, conflict = false;
  await page.route('**/api/v1/assistant/operations/9007199254740999/undo*', async route => {
    const request = route.request(); const method = request.method();
    const body = method === 'POST' ? request.postDataJSON() : null; const key = request.headers()['idempotency-key'];
    calls.push({ method, body, key });
    if (method === 'GET') return route.fulfill({ json: {
      operation_id: '9007199254740999', preview_hash: hash, receipts,
      items: [
        { key: 'asset:9007199254740993', kind: 'asset', id: '9007199254740993', name: '新角色', allowed: !receipts.length, reason: receipts.length ? '这项内容已经撤销' : null, requires: [] },
        { key: 'asset:9007199254740995', kind: 'asset', id: '9007199254740995', name: '已被人工编辑的角色', allowed: false, reason: '对象已有后续变化', requires: [] },
      ],
    } });
    if (accepted.has(key)) return route.fulfill({ json: accepted.get(key), status: 201 });
    if (conflict) { conflict = false; hash = 'b'.repeat(64); return route.fulfill({ status: 409, json: { error: { code: 'assistant_undo_stale', message: '撤销预览已变化，输入保留，请重新核对' } } }); }
    const result = { id: '9007199254741001', operation_id: '9007199254740999', selected_keys: body.selected_keys, result_refs: [{ kind: 'asset', id: '9007199254740993', project_id: '10', name: '新角色', row_version: '2', status: 'deleted' }], created_at: time };
    receipts.push(result); accepted.set(key, result); hash = 'c'.repeat(64);
    state.operations[0].undo_receipts = [...receipts];
    if (abort) { abort = false; return route.abort('failed'); }
    return route.fulfill({ json: result, status: 201 });
  });
  return { ...state, undoCalls: calls, abortUndo: () => { abort = true; }, conflictUndo: () => { conflict = true; } };
}
async function openUndo(page: Page) {
  await page.goto(`${root}/source?assistant=open`);
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await trace.getByText('保存标准创作内容', { exact: true }).click();
  await trace.getByRole('button', { name: '预览批次撤销', exact: true }).click();
  return page.getByRole('dialog', { name: '撤销操作批次', exact: true });
}
for (const width of [1440, 390]) test(`batch undo previews conflicts and executes only the explicit safe selection at ${width}px`, async ({ page }, info) => {
  const state = await undoFixture(page); await page.setViewportSize({ width, height: 1000 });
  const dialog = await openUndo(page);
  await expect(dialog.getByRole('checkbox', { name: '已被人工编辑的角色', exact: true })).toBeDisabled();
  await expect(dialog.getByRole('button', { name: '撤销所选内容', exact: true })).toBeDisabled();
  await dialog.getByRole('checkbox', { name: '新角色', exact: true }).check();
  await checkPrimaryButtonContrast(page, dialog.getByRole('button', { name: '撤销所选内容', exact: true }), info, `batch-undo-${width}`);
  await page.screenshot({ path: info.outputPath(`batch-undo-${width}.png`) });
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await dialog.getByRole('button', { name: '撤销所选内容', exact: true }).click();
  await expect(dialog.getByRole('region', { name: '已保存的撤销回执' })).toContainText('已撤销 1 项');
  await dialog.getByRole('button', { name: '关闭并保留', exact: true }).click();
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await expect(trace.getByLabel('批次撤销状态')).toContainText('已明确撤销 1 项变更');
  const card = trace.getByRole('article', { name: '素材成果：新角色', exact: true });
  await expect(card).toContainText('已撤销本项变更'); await expect(card.getByRole('link')).toHaveCount(0);
  await page.reload(); await trace.getByText('保存标准创作内容', { exact: true }).click();
  await expect(card).toContainText('已撤销本项变更'); await expect(card.getByRole('link')).toHaveCount(0);
  expect(state.undoCalls.filter(call => call.method === 'POST').map(call => call.body)).toEqual([{ preview_hash: 'a'.repeat(64), selected_keys: ['asset:9007199254740993'] }]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
for (const kind of ['canvas_node', 'sound_line']) test(`batch undo keeps explicit native dependencies and restored ${kind} status`, async ({ page }) => {
  const state = await assistantFixture(page); await page.setViewportSize({ width: 390, height: 1000 });
  state.beginReply('成果已保存。'); state.startOperation();
  state.messages['301'].push({ id: 'native-undo-question', task_id: '501', seq: 1, role: 'user', content: '创建内容', references: [], artifacts: [], created_at: time });
  const native = kind === 'canvas_node'; const title = native ? '保存画布节点与连线' : '保存标准创作内容';
  const ref = { kind, id: 'native_piece', project_id: '10', canvas_id: 'saved-canvas', name: native ? '雨夜节点' : '旁白 · 雨夜台词', status: 'created' };
  Object.assign(state.operations[0], { tool_name: native ? 'apply_canvas_patch' : 'apply_standard_patch', result_refs: [ref] }); state.finishOperation();
  const receipts: any[] = [], requests: any[] = [];
  await page.route('**/api/v1/assistant/operations/801/undo*', async route => {
    const request = route.request();
    if (request.method() === 'GET') return route.fulfill({ json: { operation_id: '801', preview_hash: 'a'.repeat(64), receipts, items: [
      { key: `${kind}:native_piece`, kind, id: 'native_piece', name: ref.name, allowed: !receipts.length, reason: null, requires: native ? ['canvas_edge:edge'] : [] },
      ...(native ? [{ key: 'canvas_edge:edge', kind: 'canvas_edge', id: 'edge', name: '连线：雨夜节点 → 清晨节点', allowed: !receipts.length, reason: null, requires: [] }] : []),
    ] } });
    const body = request.postDataJSON(); requests.push(body);
    if (native && !body.selected_keys.includes('canvas_edge:edge')) return route.fulfill({ status: 409, json: { error: { code: 'assistant_undo_dependencies', message: '请明确同时选择必须撤销的关联内容' } } });
    const receipt = { id: '901', operation_id: '801', selected_keys: body.selected_keys, result_refs: [{ ...ref, status: 'deleted' }], created_at: time };
    receipts.push(receipt); state.operations[0].undo_receipts = [...receipts];
    return route.fulfill({ status: 201, json: receipt });
  });
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await trace.getByText(title, { exact: true }).click();
  await trace.getByRole('button', { name: '预览批次撤销', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '撤销操作批次', exact: true });
  await dialog.getByRole('checkbox', { name: ref.name, exact: true }).check();
  if (native) await expect(dialog.getByText('须同时选择：连线：雨夜节点 → 清晨节点', { exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: '撤销所选内容', exact: true }).click();
  if (native) {
    await expect(dialog.getByRole('checkbox', { name: ref.name, exact: true })).toBeChecked();
    await dialog.getByRole('checkbox', { name: '连线：雨夜节点 → 清晨节点', exact: true }).check();
    await dialog.getByRole('button', { name: '撤销所选内容', exact: true }).click();
  }
  await expect(dialog.getByRole('region', { name: '已保存的撤销回执' })).toBeVisible();
  await dialog.getByRole('button', { name: '关闭并保留', exact: true }).click();
  const card = trace.getByRole('article', { name: `${native ? '节点' : '台词'}成果：${ref.name}`, exact: true });
  await expect(card).toContainText('已撤销本项变更'); await expect(card.getByRole('link')).toHaveCount(0);
  await page.reload(); await trace.getByText(title, { exact: true }).click();
  await expect(card).toContainText('已撤销本项变更');
  expect(requests.at(-1).selected_keys).toEqual(native ? ['canvas_node:native_piece', 'canvas_edge:edge'] : ['sound_line:native_piece']);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const outcome of ['succeeded', 'failed']) test(`media batch undo restores a voice binding after ${outcome} and retains its receipt on refresh`, async ({ page }) => {
  const state = await assistantFixture(page);
  state.beginReply('已保存媒体产物。'); state.startOperation();
  state.messages['301'].push({ id: 'media-undo-question', task_id: '501', seq: 1, role: 'user', content: '生成角色音色', references: [], artifacts: [], created_at: time });
  const ref = { kind: 'asset', id: state.asset.id, project_id: '10', name: '雨夜音色', status: 'adopted', media_role: 'character_voice', media_kind: 'audio', media_id: '7001', voice_version: '1' };
  Object.assign(state.operations[0], { tool_name: 'submit_generation_batch', result_refs: [ref] }); state.finishOperation();
  Object.assign(state.operations[0], { outcome, state_version: '3' });
  const receipts: any[] = [], requests: any[] = [];
  await page.route('**/api/v1/assistant/operations/801/undo*', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: { operation_id: '801', preview_hash: 'a'.repeat(64), receipts, items: [{ key: `character_voice:${ref.id}`, kind: 'character_voice', id: ref.id, name: ref.name, allowed: !receipts.length, requires: [] }] } });
    const body = route.request().postDataJSON(); requests.push(body);
    const receipt = { id: '901', operation_id: '801', selected_keys: body.selected_keys, result_refs: [{ ...ref, media_id: null, voice_version: '2', status: 'updated' }], created_at: time };
    receipts.push(receipt); state.operations[0].undo_receipts = [...receipts];
    return route.fulfill({ status: 201, json: receipt });
  });
  await page.goto(`${root}/source?assistant=open`);
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await trace.getByText('生成并回填媒体', { exact: true }).click();
  await trace.getByRole('button', { name: '预览批次撤销', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '撤销操作批次', exact: true });
  await dialog.getByRole('checkbox', { name: ref.name, exact: true }).check();
  await dialog.getByRole('button', { name: '撤销所选内容', exact: true }).click();
  await expect(dialog.getByRole('region', { name: '已保存的撤销回执' })).toBeVisible();
  await dialog.getByRole('button', { name: '关闭并保留', exact: true }).click();
  const card = trace.getByRole('article', { name: '素材成果：雨夜音色', exact: true });
  await expect(card).toContainText('已撤销本项变更'); await expect(card).toContainText('音色版本 2');
  await expect(card.getByRole('button', { name: '试听生成音色', exact: true })).toHaveCount(0);
  await expect(card.getByRole('link')).toHaveAttribute('href', `/projects/10/assets?asset=${ref.id}`);
  await page.reload(); await trace.getByText('生成并回填媒体', { exact: true }).click();
  await expect(card).toContainText('已撤销本项变更'); expect(requests).toHaveLength(1);
  expect(state.operations[0].outcome).toBe(outcome);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('pending history and batch undo can reconcile their original requests without mutual deadlock', async ({ page }) => {
  const state = await undoFixture(page); state.messages['301'][0].id = '1001';
  const revisions: any[] = [];
  await page.route('**/api/v1/assistant/global/conversations/301/messages/1001/revisions', route => {
    if (route.request().method() !== 'POST') return route.fallback();
    revisions.push({ body: route.request().postDataJSON(), key: route.request().headers()['idempotency-key'] });
    return route.fulfill({ status: 409, json: { error: { code: 'assistant_history_dependency', message: '此讨论已有工具依赖，请保留草稿并发送新消息纠正' } } });
  });
  await page.goto(root + '/source?assistant=open');
  await page.evaluate(() => {
    localStorage.setItem('short-drama:user:9007199254740993:assistant-history:v1:301', JSON.stringify({ version: 1, messageId: '1001', key: 'original-history', pending: true, body: { kind: 'edit', row_version: '1', content: '保留的讨论修订' } }));
    localStorage.setItem('short-drama:user:9007199254740993:assistant-undo:v1:301', JSON.stringify({ version: 1, operationId: '9007199254740999', key: 'original-undo', pending: true, body: { preview_hash: 'a'.repeat(64), selected_keys: ['asset:9007199254740993'] } }));
  });
  await page.reload();
  const undo = page.getByRole('dialog', { name: '撤销操作批次', exact: true });
  await expect(undo.getByRole('button', { name: '使用原撤销请求核对', exact: true })).toBeEnabled();
  await undo.getByRole('button', { name: '使用原撤销请求核对', exact: true }).click();
  await expect(undo.getByRole('region', { name: '已保存的撤销回执' })).toBeVisible();
  await undo.getByRole('button', { name: '关闭并保留', exact: true }).click();
  const history = page.getByRole('dialog', { name: '编辑讨论', exact: true });
  await expect(history.getByRole('button', { name: '使用原请求核对', exact: true })).toBeEnabled();
  await history.getByRole('button', { name: '使用原请求核对', exact: true }).click();
  await expect(history.getByRole('textbox', { name: '修订讨论正文', exact: true })).toHaveValue('保留的讨论修订');
  await expect(history.getByRole('button', { name: '舍弃修订草稿', exact: true })).toBeVisible();
  expect(revisions).toEqual([{ body: { kind: 'edit', row_version: '1', content: '保留的讨论修订' }, key: 'original-history' }]);
  expect(state.undoCalls.filter(call => call.method === 'POST')[0].key).toBe('original-undo');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('an uncertain batch undo reuses its exact preview and selection after refresh', async ({ page }) => {
  const state = await undoFixture(page); let dialog = await openUndo(page);
  await dialog.getByRole('checkbox', { name: '新角色', exact: true }).check();
  state.abortUndo(); await dialog.getByRole('button', { name: '撤销所选内容', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '使用原撤销请求核对', exact: true })).toBeEnabled();
  await page.reload(); dialog = page.getByRole('dialog', { name: '撤销操作批次', exact: true });
  await expect(dialog.getByRole('checkbox', { name: '新角色', exact: true })).toBeChecked();
  await expect(dialog.getByRole('checkbox', { name: '新角色', exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: '使用原撤销请求核对', exact: true }).click();
  await expect(dialog.getByRole('region', { name: '已保存的撤销回执' })).toContainText('已撤销 1 项');
  const attempts = state.undoCalls.filter(call => call.method === 'POST');
  expect(attempts).toHaveLength(2); expect(attempts[1]).toEqual(attempts[0]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
test('a stale batch undo keeps the selection until the user adopts a fresh preview', async ({ page }) => {
  const state = await undoFixture(page); const dialog = await openUndo(page);
  await dialog.getByRole('checkbox', { name: '新角色', exact: true }).check();
  state.conflictUndo(); await dialog.getByRole('button', { name: '撤销所选内容', exact: true }).click();
  await dialog.getByRole('button', { name: /^提示详情：/ }).click();
  await dialog.getByRole('button', { name: '重新核对预览', exact: true }).click();
  await expect(dialog.getByRole('checkbox', { name: '新角色', exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: '采用新预览', exact: true }).click();
  await expect(dialog.getByRole('checkbox', { name: '新角色', exact: true })).toBeChecked();
  await dialog.getByRole('button', { name: '撤销所选内容', exact: true }).click();
  await expect(dialog.getByRole('region', { name: '已保存的撤销回执' })).toContainText('已撤销 1 项');
  const attempts = state.undoCalls.filter(call => call.method === 'POST');
  expect(attempts.map(call => call.body.preview_hash)).toEqual(['a'.repeat(64), 'b'.repeat(64)]);
  expect(attempts[1].key).not.toBe(attempts[0].key);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

async function restoreLegacyDiscussionDraft(page: Page, kind: 'edit' | 'regenerate' = 'edit') {
  const content = await page.locator('[data-message-id="1001"]').innerText();
  await page.evaluate(({ content, kind }) => localStorage.setItem('short-drama:user:9007199254740993:assistant-history:v1:301', JSON.stringify({
    version: 1, messageId: '1001', key: 'legacy-discussion-draft', pending: false,
    body: { kind, row_version: '1', ...(kind === 'edit' ? { content } : {}) },
  })), { content, kind });
  await page.reload();
}

for (const width of [1440, 390]) test(`legacy discussion drafts can finish while user messages expose no editing actions at ${width}px`, async ({ page }, info) => {
  const state = await assistantFixture(page); await page.setViewportSize({ width, height: 1000 });
  await page.goto(`${root}/source?assistant=open`);
  await page.getByRole('textbox', { name: '给助手的消息', exact: true }).fill('原始的纯讨论');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  const source = page.locator('[data-message-id="1001"]');
  await expect(source.getByRole('button')).toHaveCount(0);
  await restoreLegacyDiscussionDraft(page);
  const dialog = page.getByRole('dialog', { name: '编辑讨论', exact: true });
  await dialog.getByRole('textbox', { name: '修订讨论正文' }).fill('只讨论前三秒冲突，保留克制的对白');
  await expect(dialog.getByRole('button', { name: '保存并重新回答', exact: true })).toBeEnabled();
  await page.screenshot({ path: info.outputPath(`discussion-edit-${width}.png`) });
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await dialog.getByRole('button', { name: '保存并重新回答', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(source).toContainText('只讨论前三秒冲突');
  await expect(page.locator('.ai-assistant-turn')).toContainText('修订后的讨论回复');
  expect(state.messages['301']).toHaveLength(2);
  expect(state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'))).toHaveLength(1);
  await expect(source.getByRole('button')).toHaveCount(0);
  await page.locator('.ai-assistant-turn').getByRole('button', { name: '查看讨论修订记录', exact: true }).click();
  const history = page.getByRole('dialog', { name: '讨论修订记录', exact: true });
  await history.getByText('版本 1 · 编辑前', { exact: true }).click();
  await expect(history).toContainText('创作建议');
  await history.getByRole('button', { name: '关闭并保留', exact: true }).click();
  await page.reload(); await expect(page.locator('[data-message-id="1001"]')).toContainText('只讨论前三秒冲突');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('an uncertain discussion revision survives refresh and reuses its exact original request', async ({ page }) => {
  const state = await assistantFixture(page); await page.goto(`${root}/source?assistant=open`);
  await page.getByRole('textbox', { name: '给助手的消息' }).fill('这是一段纯讨论');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await restoreLegacyDiscussionDraft(page);
  let dialog = page.getByRole('dialog', { name: '编辑讨论', exact: true });
  await dialog.getByRole('textbox', { name: '修订讨论正文' }).fill('受理不明时保留的修订');
  state.abortNextRevision(); await dialog.getByRole('button', { name: '保存并重新回答', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '使用原请求核对', exact: true })).toBeEnabled();
  await page.reload(); dialog = page.getByRole('dialog', { name: '编辑讨论', exact: true });
  await expect(dialog.getByRole('textbox', { name: '修订讨论正文' })).toHaveValue('受理不明时保留的修订');
  await expect(dialog.getByRole('textbox', { name: '修订讨论正文' })).toBeDisabled();
  await dialog.getByRole('button', { name: '使用原请求核对', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  const attempts = state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/revisions'));
  expect(attempts).toHaveLength(2); expect(attempts[1]).toEqual(attempts[0]);
  expect(state.messages['301']).toHaveLength(2);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('discussion version conflicts preserve draft and require reviewing the current message', async ({ page }) => {
  const state = await assistantFixture(page); await page.goto(`${root}/source?assistant=open`);
  await page.getByRole('textbox', { name: '给助手的消息' }).fill('原始纯讨论');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await restoreLegacyDiscussionDraft(page);
  const dialog = page.getByRole('dialog', { name: '编辑讨论', exact: true });
  const text = dialog.getByRole('textbox', { name: '修订讨论正文' }); await text.fill('本机修订不能丢失');
  state.conflictNextRevision(); await dialog.getByRole('button', { name: '保存并重新回答', exact: true }).click();
  await expect(text).toHaveValue('本机修订不能丢失');
  await dialog.getByRole('button', { name: /^提示详情：/ }).click();
  await dialog.getByRole('button', { name: '重新核对', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '保存并重新回答', exact: true })).toBeDisabled();
  await dialog.getByText('核对当前消息 · 版本 2', { exact: true }).click();
  await expect(dialog).toContainText('其他标签页保存的新版本');
  await dialog.getByRole('button', { name: /^提示详情：/ }).click();
  await dialog.getByRole('button', { name: '采用当前消息版本', exact: true }).click();
  await expect(text).toHaveValue('本机修订不能丢失');
  await dialog.getByRole('button', { name: '保存并重新回答', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/revisions')).map(call => call.body.row_version)).toEqual(['1', '2']);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('regenerating a discussion keeps its source text and replaces only its original reply', async ({ page }) => {
  const state = await assistantFixture(page); await page.goto(`${root}/source?assistant=open`);
  await page.getByRole('textbox', { name: '给助手的消息' }).fill('保留这条用户原文');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  const source = page.locator('[data-message-id="1001"]');
  await restoreLegacyDiscussionDraft(page, 'regenerate');
  const dialog = page.getByRole('dialog', { name: '重新回答', exact: true });
  await expect(dialog).toContainText('保留用户原文');
  await expect(dialog.getByRole('textbox', { name: '修订讨论正文' })).toHaveCount(0);
  await dialog.getByRole('button', { name: '重新回答这段讨论', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(source).toContainText('保留这条用户原文');
  await expect(page.locator('.ai-assistant-turn')).toContainText('修订后的讨论回复');
  expect(state.messages['301']).toHaveLength(2);
  const revised = state.calls.find(call => call.method === 'POST' && call.path.endsWith('/revisions'))!;
  expect(revised.body).toEqual({ kind: 'regenerate', row_version: '1' });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('protected discussions offer a new correcting message without rewriting history', async ({ page }) => {
  const state = await assistantFixture(page); await page.goto(`${root}/source?assistant=open`);
  const input = page.getByRole('textbox', { name: '给助手的消息' }); await input.fill('已经成为执行依据的讨论');
  await page.getByRole('button', { name: '发送', exact: true }).click(); state.denyHistory();
  await restoreLegacyDiscussionDraft(page);
  const dialog = page.getByRole('dialog', { name: '编辑讨论', exact: true });
  await expect(dialog.getByRole('button', { name: '保存并重新回答', exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: /^提示详情：/ }).click();
  await expect(dialog).toContainText('此讨论已被后续创作使用');
  await expect(dialog).not.toContainText('记忆');
  await dialog.getByRole('button', { name: '用新消息纠正', exact: true }).click();
  await expect(dialog).not.toBeVisible(); await expect(input).toHaveValue('纠正此前讨论：已经成为执行依据的讨论');
  expect(state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/revisions'))).toHaveLength(0);
  expect(state.messages['301'][0].content).toBe('已经成为执行依据的讨论');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`subject versions refresh inline and retain other saved sources at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  const state = await assistantFixture(page); state.beginReply('按原引用讨论。');
  const remote = { project_id: '11', source_kind: 'project', source_id: '11', kind: 'asset', id: '601', revision: '3', name: '远方角色', project_name: '另一作品', source_name: '另一作品' };
  state.subjectOptions.push(remote); state.asset.row_version = '2';
  state.tasks[0].selected_subjects = [Object.assign(state.subject(state.subjectOptions[0]), { availability: 'changed', current_revision: '2', current_checksum: 'c'.repeat(64) }), state.subject(remote)];
  await page.goto(`${root}/source?assistant=open`);
  const chips = page.getByLabel('当前任务引用', { exact: true });
  await expect(chips).toContainText('有新版本');
  await page.getByRole('button', { name: '刷新引用林晚', exact: true }).click();
  await expect(chips).toContainText('林晚 · 待发送');
  await expect(chips).toContainText('远方角色');
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await input.fill('按刷新后的资料继续比较');
  await page.getByRole('button', { name: '停止', exact: true }).click();
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(input).toHaveValue('');
  const sent = state.calls.find(call => call.method === 'POST' && call.path.endsWith('/messages'))!;
  expect(sent.body.subject_selection).toMatchObject({ expected_input_revision: '1', retain_keys: [state.subject(remote).subject_key], subjects: [{ id: '501', revision: '2', checksum: 'c'.repeat(64) }] });
  expect(sent.body).not.toHaveProperty('execution_target');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('cross-page image-slot selection and uncertain acceptance preserve one original request', async ({ page }) => {
  const state = await assistantFixture(page); state.beginReply('比较当前角色。');
  state.tasks[0].selected_subjects = [state.subject(state.subjectOptions[0])];
  state.subjectOptions.push({ project_id: '11', source_kind: 'project', source_id: '11', kind: 'image_slot', id: '777', revision: '4', name: '图片组 700 · 图位 2', project_name: '另一作品', source_name: '另一作品' });
  await page.addInitScript(({ added, retained }) => {
    const key = 'short-drama:user:9007199254740993:assistant-draft:v1:301';
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ version: 1, text: '', skills: [], subjectDraft: { taskId: '501', expectedInputRevision: '1', retainKeys: [retained], added: [added] } }));
  }, { added: state.subjectOptions.at(-1), retained: state.subject(state.subjectOptions[0]).subject_key });
  await page.goto(`${root}/source?assistant=open`);
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await input.fill('跨页比较这些参考');
  await page.goto(`${root}/storyboard?assistant=open`);
  await expect(input).toHaveValue('跨页比较这些参考');
  await expect(page.getByLabel('当前任务引用', { exact: true })).toContainText('图位 2 · 待发送');
  await page.getByRole('button', { name: '停止', exact: true }).click();
  state.abortNextSend(); await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText('发送结果尚未确认，草稿已保留。请使用原请求核对，避免重复调用。', { exact: true })).toBeVisible();
  await openSaveDetails(page);
  await expect(page.getByRole('button', { name: '使用原请求核对', exact: true })).toBeVisible();
  const original = state.calls.find(call => call.method === 'POST' && call.path.endsWith('/messages'))!;
  expect(original.body.subject_selection).toMatchObject({ expected_input_revision: '1', retain_keys: [state.subject(state.subjectOptions[0]).subject_key], subjects: [{ project_id: '11', source_id: '11', kind: 'image_slot', id: '777', revision: '4' }] });
  let reloading = true;
  page.removeAllListeners('dialog');
  page.on('dialog', dialog => { if (reloading && dialog.type() === 'beforeunload') void dialog.accept(); else { state.unexpected.push(`native dialog: ${dialog.message()}`); void dialog.dismiss(); } });
  try { await page.reload(); } finally { reloading = false; }
  await openSaveDetails(page);
  await page.getByRole('button', { name: '使用原请求核对', exact: true }).click();
  await expect(input).toHaveValue('');
  const sends = state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'));
  expect(sends).toHaveLength(2); expect(sends[1].body).toEqual(original.body); expect(sends[1].key).toEqual(original.key);
  expect(state.messages['301'].filter(item => item.role === 'user')).toHaveLength(1);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('image-slot source link opens only its real saved slot and rejects a mismatched source', async ({ page }, info) => {
  const state = await assistantFixture(page); state.beginReply('核对引用图位。');
  state.tasks[0].selected_subjects = [state.subject({ project_id: '10', source_kind: 'project', source_id: '10', kind: 'image_slot', id: '777', revision: '4', name: '人物图位', project_name: '测试项目', source_location: { project_id: '10', kind: 'asset', id: '501', image_group_id: '700', image_slot_id: '777' } })];
  await page.route('**/api/v1/image-groups/700', route => route.fulfill({ json: { id: '700', target_kind: 'asset', target_id: '501', is_current: false, primary_version: '3', primary_index: 2, status: 'completed', expected_count: 2, items: [
    { id: '776', output_index: 1, item_version: '1', url: null, status: 'failed', width: null, height: null },
    { id: '777', output_index: 2, item_version: '4', url: null, status: 'ready', width: 1024, height: 768 },
  ] } }));
  await page.goto(`${root}/source?assistant=open`);
  const source = page.getByRole('link', { name: '查看引用来源人物图位', exact: true });
  await expect(source).toHaveAttribute('target', '_blank');
  const href = await source.getAttribute('href');
  expect(href).toBe('/projects/10/assets?asset=501&source_image_group=700&source_image_slot=777');
  await page.goto(href!);
  const dialog = page.getByRole('dialog', { name: '引用图位来源', exact: true });
  await expect(dialog.getByLabel('来源图位 2', { exact: true })).toContainText('v4');
  await expect(dialog.getByLabel('来源图位 1', { exact: true })).toHaveCount(0);
  await expect(dialog.getByText('1024 × 768', { exact: true })).toBeVisible();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath(`assistant-source-slot-${width}.png`) });
  }
  await dialog.getByRole('button', { name: '关闭来源', exact: true }).click();
  await expect(page).not.toHaveURL(/source_image_group/);
  await page.goto(`${root}/assets?asset=501&source_image_group=700&source_image_slot=999`);
  await expect(dialog.getByText('图片来源不属于当前主体，或图位已移除。', { exact: true })).toBeVisible();
  await expect(dialog.getByLabel('来源图位 2', { exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('task attachment removal survives navigation and replays the original selection after a lost response', async ({ page }) => {
  const state = await assistantFixture(page); state.beginReply('附件已读取。');
  state.tasks[0].task_attachments = [{ id: '7001', kind: 'text', name: '故事资料.txt', checksum_sha256: 'b'.repeat(64), available: true }];
  state.tasks[0].selected_subjects = [state.subject(state.subjectOptions[0])];
  await page.goto(`${root}/source?assistant=open`);
  await page.getByRole('button', { name: '移除任务附件故事资料.txt', exact: true }).click();
  await expect(page.getByLabel('当前任务附件', { exact: true })).toContainText('待移除');
  await page.goto(`${root}/storyboard?assistant=open`);
  await expect(page.getByLabel('当前任务附件', { exact: true })).toContainText('待移除');
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await input.fill('后续不再引用附件，保留人物卡');
  await page.getByRole('button', { name: '停止', exact: true }).click();
  state.abortNextSend(); await page.getByRole('button', { name: '发送', exact: true }).click();
  await openSaveDetails(page);
  await expect(page.getByRole('button', { name: '使用原请求核对', exact: true })).toBeVisible();
  const original = state.calls.find(call => call.method === 'POST' && call.path.endsWith('/messages'))!;
  expect(original.body.attachment_selection).toEqual({ expected_input_revision: '1', retain_ids: [] });
  expect(original.body).not.toHaveProperty('subject_selection');
  let reloading = true;
  page.removeAllListeners('dialog');
  page.on('dialog', dialog => { if (reloading && dialog.type() === 'beforeunload') void dialog.accept(); else { state.unexpected.push(`native dialog: ${dialog.message()}`); void dialog.dismiss(); } });
  try { await page.reload(); } finally { reloading = false; }
  await openSaveDetails(page); await page.getByRole('button', { name: '使用原请求核对', exact: true }).click();
  await expect(input).toHaveValue('');
  const sends = state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'));
  expect(sends).toHaveLength(2); expect(sends[1].body).toEqual(original.body); expect(sends[1].key).toEqual(original.key);
  await expect(page.getByLabel('当前任务引用', { exact: true })).toContainText('林晚');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('copy failures stay out of the reply layout and a successful retry clears their saved detail', async ({ page }) => {
  const state = await assistantFixture(page);
  state.messages['301'].push({ id: 'copy-reply', seq: 1, role: 'assistant', content: '保留人物动机与集尾悬念。', references: [], artifacts: [], created_at: time });
  await page.goto(`${root}/source?assistant=open`);
  const reply = page.locator('[data-message-id="copy-reply"]');
  const copy = reply.getByRole('button', { name: '复制回复', exact: true });
  await expect(copy).toBeVisible();
  const replyBox = await reply.boundingBox();
  const composerBox = await page.locator('.ai-assistant-composer').boundingBox();
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new DOMException('Clipboard is unavailable', 'NotAllowedError'); } } }));
  await copy.click();
  await expect(page.locator('.studio-message').filter({ hasText: '复制失败，请选中回复文字复制。' })).toBeVisible();
  expect(await reply.boundingBox()).toEqual(replyBox);
  expect(await page.locator('.ai-assistant-composer').boundingBox()).toEqual(composerBox);
  await expect(reply).not.toContainText('复制失败');
  const details = await openSaveDetails(page);
  await expect(details).toContainText('复制失败，请选中回复文字复制。');
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (value: string) => { (window as any).__copiedReply = value; } } }));
  await copy.click();
  await expect(copy).toHaveText('已复制');
  expect(await page.evaluate(() => (window as any).__copiedReply)).toBe('保留人物动机与集尾悬念。');
  await openSaveDetails(page);
  await expect(details).not.toContainText('复制失败');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('uncertain accepted message reuses its frozen payload after refresh and context removal', async ({ page }) => {
  const state = await assistantFixture(page); state.abortNextSend();
  await page.goto(`${root}/source?assistant=open`);
  const input = page.getByRole('textbox', { name: '给助手的消息' }); await expect(input).toBeVisible();
  await input.fill('普通问答');
  const composerBox = await page.locator('.ai-assistant-composer').boundingBox();
  const inputBox = await input.boundingBox();
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText('发送结果尚未确认，草稿已保留。请使用原请求核对，避免重复调用。')).toBeVisible();
  for (const [locator, before] of [[page.locator('.ai-assistant-composer'), composerBox], [input, inputBox]] as const) {
    const after = (await locator.boundingBox())!;
    for (const key of ['x', 'y', 'width', 'height'] as const) expect(Math.abs(after[key] - before![key])).toBeLessThanOrEqual(1);
  }
  let reloading = true;
  page.removeAllListeners('dialog');
  page.on('dialog', dialog => {
    if (reloading && dialog.type() === 'beforeunload') { void dialog.accept(); return; }
    state.unexpected.push(`native dialog: ${dialog.message()}`); void dialog.dismiss();
  });
  try { await page.reload(); }
  finally { reloading = false; }
  await expect(input).toHaveValue('普通问答');
  await openSaveDetails(page);
  await page.getByRole('button', { name: '使用原请求核对', exact: true }).click();
  await expect(input).toHaveValue('');
  const sends = state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'));
  expect(sends).toHaveLength(2); expect(sends[0].body).not.toHaveProperty('context'); expect(sends[1].body).toEqual(sends[0].body); expect(sends[1].key).toEqual(sends[0].key);
  expect(state.messages['301'].filter(item => item.role === 'user')).toHaveLength(1);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('closing the assistant ignores a delayed new conversation response and preserves the current draft', async ({ page }) => {
  const state = await assistantFixture(page);
  let releaseCreate = () => {};
  const heldCreate = new Promise<void>(resolve => { releaseCreate = resolve; });
  await page.route('**/api/v1/assistant/global/conversations', async route => {
    if (route.request().method() !== 'POST') return route.fallback();
    await heldCreate;
    return route.fallback();
  });
  await page.goto(`${root}/source?assistant=open`);
  const input = page.getByRole('textbox', { name: '给助手的消息' });
  await expect(input).toBeVisible(); await expect(page).toHaveURL(/conversation=301/);
  await input.fill('保留在当前对话里的草稿');
  const newConversation = page.getByRole('button', { name: '新对话', exact: true, includeHidden: true });
  const createdRequest = page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/assistant/global/conversations');
  await newConversation.click(); await createdRequest;
  await expect(newConversation).toHaveClass(/ant-btn-loading/);
  await page.getByRole('button', { name: '关闭助手', exact: true }).click();
  await expect(input).toBeHidden();
  const createdResponse = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/assistant/global/conversations');
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

test('global history renames and permanently deletes conversations without archive entries', async ({ page }) => {
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
  await expect(dialog.getByText('包含归档对话')).toHaveCount(0);
  await dialog.getByRole('button', { name: '管理对话修改后的讨论' }).click(); await page.getByRole('menuitem', { name: '删除', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '删除对话', exact: true }); await confirmation.getByRole('button', { name: '永久删除', exact: true }).click();
  await expect.poll(() => state.conversations[0].status).toBe('deleted');
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '给助手的消息' })).toHaveCount(0);
  await page.getByRole('button', { name: '新对话', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '给助手的消息' })).toBeEditable(); await expect(page).toHaveURL(/conversation=302/);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('current references remain above the composer and new messages preserve the history reading position', async ({ page }) => {
  const state = await assistantFixture(page);
  state.messages['301'].push(...Array.from({ length: 65 }, (_, index) => ({ id: String(2000 + index), seq: index + 1,
    role: index % 2 ? 'assistant' : 'user', content: `第 ${index + 1} 条故事讨论。${'补充人物动机与故事冲突。'.repeat(12)}`, references: [], artifacts: [], created_at: time })));
  await page.goto(`${root}/source?assistant=open`);
  const log = page.getByLabel('助手消息', { exact: true });
  const context = page.getByRole('button', { name: '添加附件', exact: true });
  await expect(log).toBeVisible(); await expect(context).toBeVisible();
  await expect.poll(() => log.evaluate(node => node.scrollHeight - node.clientHeight)).toBeGreaterThan(500);
  const contextAtBottom = (await context.boundingBox())!;
  await log.evaluate(node => { node.scrollTop = 120; node.dispatchEvent(new Event('scroll')); });
  const contextWhileReading = (await context.boundingBox())!;
  expect(Math.abs(contextWhileReading.y - contextAtBottom.y)).toBeLessThanOrEqual(1);
  const bounds = (await log.boundingBox())!;
  expect(contextWhileReading.y).toBeGreaterThanOrEqual(bounds.y + bounds.height - 1);
  await log.evaluate(node => { node.scrollTop = 0; node.dispatchEvent(new Event('scroll')); });
  const anchor = page.locator('[data-message-id="2015"]');
  const anchorBefore = (await anchor.boundingBox())!;
  await page.getByRole('button', { name: '载入更早消息', exact: true }).click();
  await expect(page.locator('[data-message-id="2000"]')).toHaveCount(1);
  expect(Math.abs((await anchor.boundingBox())!.y - anchorBefore.y)).toBeLessThanOrEqual(1);
  const scrollBefore = await log.evaluate(node => node.scrollTop);
  state.messages['301'].push({ id: 'newest-reply', seq: 66, role: 'assistant', content: '新的回复已经完成，原有阅读位置应保留。', references: [], artifacts: [], created_at: time });
  await page.evaluate(() => window.dispatchEvent(new Event('agent-run-updated')));
  await expect(page.getByRole('button', { name: '查看最新消息', exact: true })).toBeVisible();
  expect(Math.abs(await log.evaluate(node => node.scrollTop) - scrollBefore)).toBeLessThanOrEqual(1);
  await page.getByRole('button', { name: '查看最新消息', exact: true }).click();
  await expect(page.locator('[data-message-id="newest-reply"]')).toBeInViewport();
  await expect(context).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('streaming and completed replies retain the same bubble width and content inset', async ({ page }) => {
  const state = await assistantFixture(page);
  const text = '这是一段正在形成的创作建议，先保留人物目标，再加强阻碍。';
  state.messages['301'].push({ id: 'question-901', seq: 1, role: 'user', content: '请分析人物目标', references: [], artifacts: [], created_at: time });
  state.beginReply(text);
  await page.goto(`${root}/source?assistant=open`);
  const streaming = page.locator('.ai-assistant-streaming .ai-assistant-reply');
  await expect(streaming).toHaveText(text);
  const before = (await streaming.boundingBox())!;
  state.finishReply(text);
  await page.evaluate(() => window.dispatchEvent(new Event('agent-run-updated')));
  const completed = page.locator('[data-message-id="reply-901"] .ai-assistant-reply');
  await expect(completed).toHaveText(text); await expect(streaming).toHaveCount(0);
  const after = (await completed.boundingBox())!;
  expect(Math.abs(after.x - before.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(after.width - before.width)).toBeLessThanOrEqual(1);
  await expect(page.locator('[data-message-id="reply-901"]').getByRole('button', { name: '复制回复', exact: true })).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`explicit resync replays active text and keeps one trace without new writes at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  state.messages['301'].push({ id: 'question-901', task_id: '501', seq: 1, role: 'user', content: '讨论人物目标', references: [], artifacts: [], created_at: time });
  state.beginReply('保留片段'); state.startOperation();
  await page.goto(`${root}/source?assistant=open`);
  const streaming = page.locator('.ai-assistant-streaming .ai-assistant-reply');
  await expect(streaming).toHaveText('保留片段');
  await expect(page.getByRole('region', { name: '任务过程：讨论故事', exact: true })).toHaveCount(1);
  state.emit('assistant.delta', { turn_id: 'reply-901', offset: 4, delta: '，补齐后续。' });
  state.nextStream('event: resync\ndata: {"code":"agent_event_resync_required"}\n\n');
  await expect.poll(() => state.cursors.filter(value => value === 0).length).toBeGreaterThanOrEqual(2);
  await expect(streaming).toHaveText('保留片段，补齐后续。');
  await expect(page.getByRole('region', { name: '任务过程：讨论故事', exact: true })).toHaveCount(1);
  expect(state.calls.filter(call => call.method !== 'GET')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('a missing SSE sequence reloads the snapshot before replaying the complete active reply', async ({ page }) => {
  const state = await assistantFixture(page);
  state.beginReply('开头');
  await page.goto(`${root}/source?assistant=open`);
  const streaming = page.locator('.ai-assistant-streaming .ai-assistant-reply');
  await expect(streaming).toHaveText('开头');
  state.emit('assistant.delta', { turn_id: 'reply-901', offset: 2, delta: '中间' });
  state.emit('assistant.delta', { turn_id: 'reply-901', offset: 4, delta: '结尾' });
  state.nextStream(`data: ${JSON.stringify({ seq: 3, event_type: 'assistant.delta', run_id: '901', payload: { turn_id: 'reply-901', offset: 4, delta: '结尾' }, created_at: time })}\n\n`);
  await expect.poll(() => state.cursors.filter(value => value === 0).length).toBeGreaterThanOrEqual(2);
  await expect(streaming).toHaveText('开头中间结尾');
  expect(state.calls.filter(call => call.method !== 'GET')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('an expired stream cursor returns to snapshot resume while a temporary retry keeps private text', async ({ page }) => {
  const state = await assistantFixture(page); state.beginReply('持续保留');
  await page.goto(`${root}/source?assistant=open`);
  const streaming = page.locator('.ai-assistant-streaming .ai-assistant-reply');
  await expect(streaming).toHaveText('持续保留');
  state.nextStream('event: retry\ndata: {"code":"agent_stream_unavailable"}\n\n');
  await expect.poll(() => state.cursors.length).toBeGreaterThanOrEqual(2);
  await expect(streaming).toHaveText('持续保留');
  state.nextStream(JSON.stringify({ error: { code: 'agent_event_resync_required' } }), 409);
  state.emit('assistant.delta', { turn_id: 'reply-901', offset: 4, delta: '，恢复完整。' });
  await expect.poll(() => state.cursors.filter(value => value === 0).length).toBeGreaterThanOrEqual(2);
  await expect(streaming).toHaveText('持续保留，恢复完整。');
  expect(state.calls.filter(call => call.method !== 'GET')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('a delayed snapshot cannot rewind a settled trace or a stopped task', async ({ page }) => {
  const state = await assistantFixture(page);
  state.messages['301'].push({ id: 'question-901', task_id: '501', seq: 1, role: 'user', content: '讨论人物目标', references: [], artifacts: [], created_at: time });
  state.beginReply('保留讨论'); state.startOperation();
  await page.goto(`${root}/source?assistant=open`);
  const trace = page.getByRole('region', { name: '任务过程：讨论故事', exact: true });
  await expect(trace).toBeVisible();
  const delayed = state.delaySnapshot();
  await page.evaluate(() => window.dispatchEvent(new Event('agent-run-updated')));
  await delayed.requested;
  try {
    state.finishOperation();
    await page.evaluate(() => window.dispatchEvent(new Event('agent-run-updated')));
    await expect(page.getByText('已停止回复，可以继续输入新的要求。', { exact: true })).toBeVisible();
  } finally { delayed.release(); }
  await expect(page.getByText('已停止回复，可以继续输入新的要求。', { exact: true })).toBeVisible();
  await expect(trace).toContainText('结果已保存');
  expect(state.calls.filter(call => call.method !== 'GET')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('access-ended rejects delayed private snapshots and stops reconnecting', async ({ page }) => {
  const state = await assistantFixture(page); state.beginReply('不能复活的私人片段');
  await page.goto(`${root}/source?assistant=open`);
  const streaming = page.locator('.ai-assistant-streaming .ai-assistant-reply');
  await expect(streaming).toHaveText('不能复活的私人片段');
  const delayed = state.delaySnapshot();
  await page.evaluate(() => window.dispatchEvent(new Event('agent-run-updated')));
  await delayed.requested;
  try {
    state.nextStream('event: access-ended\ndata: {"code":"agent_access_ended"}\n\n');
    await expect(page.getByText('对话访问已结束，请核对登录与项目权限。', { exact: true })).toBeVisible();
  } finally { delayed.release(); }
  await expect(streaming).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: '给助手的消息', exact: true })).toBeDisabled();
  const connections = state.cursors.length;
  await page.evaluate(() => window.dispatchEvent(new Event('agent-run-updated')));
  expect(state.cursors.length).toBe(connections);
  expect(state.calls.filter(call => call.method !== 'GET')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('mention keyboard selection and Chinese composition do not submit before explicit Enter', async ({ page }) => {
  const state = await assistantFixture(page);
  await page.route('**/api/v1/projects/10/assets**', route => new URL(route.request().url()).searchParams.get('q') === '没有匹配的素材'
    ? route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 20 } })
    : route.fallback());
  await page.goto(`${root}/source?assistant=open`);
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await input.fill('@没有匹配的素材');
  const closeReferences = page.getByRole('button', { name: '关闭引用列表', exact: true });
  await expect(page.getByText('没有匹配的项目素材', { exact: true })).toBeVisible();
  await closeReferences.focus();
  await page.keyboard.press('Escape');
  await expect(closeReferences).toBeHidden();
  await expect(input).toBeFocused();
  await input.fill('@林');
  await expect(page.getByRole('menuitemcheckbox', { name: '林晚 素材', exact: true })).toBeVisible();
  await input.press('ArrowDown'); await input.press('ArrowUp'); await input.press('Enter');
  await expect(page.getByLabel('当前任务引用', { exact: true })).toContainText('林晚');
  expect(state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'))).toHaveLength(0);
  await input.fill('一起分析人物');
  await input.dispatchEvent('compositionstart');
  await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true });
  expect(state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'))).toHaveLength(0);
  await input.dispatchEvent('compositionend');
  await input.press('Shift+Enter'); await expect(input).toHaveValue('一起分析人物\n');
  await input.press('Enter'); await expect(input).toHaveValue('');
  expect(state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'))).toHaveLength(1);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`reply waiting dots animate and the Sender stops without clearing the draft at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 900 });
  const state = await assistantFixture(page); state.beginReply('');
  state.messages['301'].push({ id: 'waiting-question', task_id: '501', seq: 1, role: 'user', content: '帮我完善人物动机', references: [], artifacts: [], created_at: time });
  await page.goto(`${root}/source?assistant=open`);
  const sender = page.locator('.ai-assistant-sender');
  await expect(page.locator('.ai-assistant-loading').first()).toContainText(/正在回复|正在恢复连接/);
  const dots = page.locator('.ai-assistant-loading-dots');
  await expect.poll(() => dots.evaluate(element => element.getAnimations({ subtree: true }).length)).toBe(3);
  await expect(sender.getByRole('button', { name: '停止', exact: true })).toBeEnabled();
  await expect(page.locator('.ai-assistant-user').getByRole('button')).toHaveCount(0);
  await expect(page.locator('.ai-assistant-composer')).not.toContainText('正在回复');
  await expect(page.locator('.ai-assistant-composer')).not.toContainText('补充当前任务');
  await page.screenshot({ path: info.outputPath(`waiting-sender-${width}.png`) });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect.poll(() => dots.evaluate(element => element.getAnimations({ subtree: true }).length)).toBe(0);
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await input.fill('下一条可以补充，也可以改方向');
  await sender.getByRole('button', { name: '停止', exact: true }).click();
  await expect(input).toHaveValue('下一条可以补充，也可以改方向');
  await expect(sender.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  expect(state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'))).toHaveLength(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const lost of ['message', 'resume']) test(`a message after stopping reconciles a lost ${lost} response before replying once`, async ({ page }) => {
  const state = await assistantFixture(page); state.beginReply('原来的方向');
  await page.goto(`${root}/source?assistant=open`);
  await page.locator('.ai-assistant-sender').getByRole('button', { name: '停止', exact: true }).click();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeVisible();
  if (lost === 'message') state.abortNextSend(); else state.abortNextControl();
  await page.getByRole('textbox', { name: '给助手的消息', exact: true }).fill('改成悬疑方向，保留已保存的角色');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await openSaveDetails(page);
  const reconcile = page.getByRole('button', { name: lost === 'message' ? '使用原请求核对' : '使用原请求核对控制', exact: true });
  await expect(reconcile).toBeEnabled();
  if (lost === 'message') expect(state.calls.filter(call => call.path.endsWith('/control') && call.body.kind === 'resume')).toHaveLength(0);
  await page.reload(); await openSaveDetails(page); await reconcile.click();
  await expect(reconcile).toHaveCount(0);
  await expect.poll(() => state.tasks[0].status).toBe('queued');
  const sends = state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'));
  const resumes = state.calls.filter(call => call.path.endsWith('/control') && call.body.kind === 'resume');
  const retried = lost === 'message' ? sends : resumes;
  expect(retried).toHaveLength(2); expect(retried[1].body).toEqual(retried[0].body); expect(retried[1].key).toBe(retried[0].key);
  expect(state.messages['301']).toHaveLength(1); expect(state.tasks).toHaveLength(1);
  expect(resumes[0].body).toEqual({ kind: 'resume', expected_control_seq: '1', message_id: state.messages['301'][0].id });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('a message cannot resume over a newer stop from another tab', async ({ page }) => {
  const state = await assistantFixture(page); state.beginReply('原来的方向');
  await page.goto(`${root}/source?assistant=open`);
  await page.locator('.ai-assistant-sender').getByRole('button', { name: '停止', exact: true }).click();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeVisible();
  await page.route('**/api/v1/assistant/global/conversations/301/messages', route => {
    if (route.request().method() === 'POST') state.tasks[0].control_seq = '2';
    return route.fallback();
  });
  await page.getByRole('textbox', { name: '给助手的消息', exact: true }).fill('补充人物动机');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.locator('.studio-message').filter({ hasText: '数据已被修改，请重新加载最新内容后再操作。' })).toBeVisible();
  expect(state.tasks[0].status).toBe('stopped'); expect(state.tasks[0].control_seq).toBe('2');
  expect(state.messages['301']).toHaveLength(1);
  await expect.poll(() => page.evaluate(() => localStorage.getItem('short-drama:user:9007199254740993:assistant-pending-control:v1:301'))).toBeNull();
  expect(state.calls.filter(call => call.path.endsWith('/control') && call.body.kind === 'resume')).toHaveLength(1);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('sending after stopping keeps task context and starts fresh after completion without a task selector', async ({ page }) => {
  const state = await assistantFixture(page); state.beginReply('正在分析当前故事。');
  await page.goto(root + '/source?assistant=open');
  await expect(page.getByRole('combobox', { name: '消息所属任务' })).toHaveCount(0);
  await expect(page.getByText('独立新任务', { exact: true })).toHaveCount(0);
  await expect(page.getByText('补充当前任务', { exact: true })).toHaveCount(0);
  await expect(page.locator('.ai-assistant-task-controls')).toHaveCount(0);
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await input.fill('补充：人物知道真相，但暂时不说。');
  await expect(page.getByRole('button', { name: '发送', exact: true })).toHaveCount(0);
  await input.press('Enter');
  expect(state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'))).toHaveLength(0);
  await page.locator('.ai-assistant-sender').getByRole('button', { name: '停止', exact: true }).click();
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(input).toHaveValue('');
  await expect.poll(() => state.calls.filter(call => call.path.endsWith('/control') && call.body.kind === 'resume').length).toBe(1);
  const supplement = state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'))[0];
  expect(supplement.body.task_id).toBe('501'); expect(supplement.body).not.toHaveProperty('new_task');
  expect(state.tasks).toHaveLength(1);
  state.finishReply('当前故事已经分析完毕。');
  await page.evaluate(() => window.dispatchEvent(new Event('agent-run-updated')));
  await expect(page.getByText('当前故事已经分析完毕。', { exact: true })).toBeVisible();
  await input.fill('另开一个故事');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(input).toHaveValue('');
  const independent = state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'))[1];
  expect(independent.body.new_task).toBe(true); expect(independent.body).not.toHaveProperty('task_id');
  expect(independent.body).not.toHaveProperty('context'); expect(independent.body).not.toHaveProperty('skills');
  expect(independent.body.execution_target).toEqual({ mode: 'standard', project_id: '10' }); expect(independent.body).not.toHaveProperty('memory_project_ids');
  expect(state.tasks).toHaveLength(2);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('stopping a claimed operation retains its receipt and the next direction resumes through one normal send', async ({ page }) => {
  const state = await assistantFixture(page); state.beginReply('正在核对目标。'); state.startOperation();
  state.messages['301'].push({ id: 'task-question', task_id: '501', seq: 1, role: 'user', content: '讨论故事', references: [], artifacts: [], created_at: time });
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await expect(trace).toContainText('核对写入目标'); await expect(trace).toContainText('执行中');
  await page.locator('.ai-assistant-sender').getByRole('button', { name: '停止', exact: true }).click();
  await expect(page.getByText('当前工具完整结束后停止', { exact: true })).toBeVisible();
  await expect(trace).toContainText('执行中'); expect(state.operations[0].outcome).toBe('running');
  state.finishOperation(); await page.evaluate(() => window.dispatchEvent(new Event('agent-run-updated')));
  await expect(trace).toContainText('结果已保存');
  await expect(page.getByRole('button', { name: '明确继续', exact: true })).toHaveCount(0);
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await input.fill('改成悬疑方向，保留已保存内容'); await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(input).toHaveValue('');
  await expect.poll(() => state.tasks[0].status).toBe('queued');
  const resume = state.calls.find(call => call.path === '/tasks/501/control' && call.body.kind === 'resume')!;
  expect(resume.body.expected_control_seq).toBe('1'); expect(resume.key).toBeTruthy();
  expect(state.operations).toHaveLength(1); expect(state.operations[0].outcome).toBe('succeeded');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`skill trace restores frozen manual methods, automatic loads and resource reads at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  state.beginReply('按指定方法检查对白。'); state.startOperation(); state.finishOperation();
  state.messages['301'].push({ id: 'skill-question', task_id: '501', seq: 1, role: 'user', content: '用这两项方法检查对白', references: [], artifacts: [], created_at: time });
  const manual = { activation_id: '9007199254740993', skill_id: 'script.v1', name: '对白动机方法', content_version: '2', package_checksum: 'a'.repeat(64), source: 'manual', purpose: '用户手动指定', active: true, plan_revision: '1', created_at: time };
  const automatic = { ...manual, activation_id: '9007199254740995', skill_id: '9007199254740997', name: '雨夜连续性方法', content_version: '7', source: 'auto', purpose: '检查道具与动作的衔接' };
  Object.assign(state.tasks[0], { skill_activations: [manual, { ...manual, activation_id: '9007199254740999', name: '已移除的旧方法', active: false }, { ...manual, activation_id: '9007199254741001', name: '暂缺能力的方法', issue: '当前任务尚无方法所需工具：apply_standard_patch' }, automatic] });
  Object.assign(state.operations[0], { tool_name: 'search_skills', result_refs: [] });
  state.operations.push(
    { ...state.operations[0], id: '802', call_id: '602', tool_name: 'load_skill', result_refs: [{ kind: 'skill', ...automatic }] },
    { ...state.operations[0], id: '803', call_id: '603', tool_name: 'read_skill_resource', skill_activations: [manual, automatic], result_refs: [{ kind: 'skill_resource', ...automatic, path: 'references/continuity.md', offset: 4000, next_offset: 8000 }] },
  );
  await page.route('**/api/v1/assistant/operations/803', route => route.fulfill({ json: { ...state.operations[2], result: { activation_id: automatic.activation_id, path: 'references/continuity.md', offset: 4000, next_offset: 8000, content: '冻结版本七的连续性资料片段。' } } }));
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await expect(trace.getByText('对白动机方法 · 版本 2 · 本任务持续使用')).toBeVisible();
  await expect(trace.getByText('已移除的旧方法 · 版本 2 · 已移除')).toBeVisible();
  await trace.getByText('加载手动创作方法', { exact: true }).nth(2).click();
  await expect(trace.getByText('当前任务尚无方法所需工具：apply_standard_patch')).toBeVisible();
  await expect(trace.getByText('检索创作方法', { exact: true })).toBeVisible();
  await trace.getByText('加载创作方法', { exact: true }).click();
  const load = trace.getByRole('article', { name: '创作方法：雨夜连续性方法' }).first();
  await expect(load).toContainText('按需加载 · 版本 7');
  await expect(load).toContainText('检查道具与动作的衔接');
  await trace.getByText('读取方法资料', { exact: true }).click();
  await expect(trace.getByText('使用方法：对白动机方法 · 版本 2、雨夜连续性方法 · 版本 7')).toBeVisible();
  await expect(trace.getByRole('article').last()).toContainText('references/continuity.md · 从字符 4000 读取');
  await trace.getByRole('button', { name: '查看回执', exact: true }).last().click();
  const dialog = page.getByRole('dialog', { name: '读取方法资料', exact: true });
  await expect(dialog.locator('.ai-assistant-receipt')).toContainText('冻结版本七的连续性资料片段');
  await expect(dialog.locator('.ai-assistant-receipt')).not.toContainText('storage_key');
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath(`skill-resource-receipt-${width}.png`) });
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.screenshot({ path: info.outputPath(`skill-task-trace-${width}.png`) });
  expect(await trace.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.reload();
  await expect(trace.getByText('对白动机方法 · 版本 2 · 本任务持续使用')).toBeVisible();
  await trace.getByText('加载创作方法', { exact: true }).click();
  await expect(trace.getByRole('article').first()).toContainText('按需加载 · 版本 7');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`standard result cards recover real references, deletion and receipts at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  state.asset.id = '9007199254740993';
  state.asset.name = '雨夜车站里的林晚';
  state.shots[0].id = '9007199254740995';
  const refs = [
    { kind: 'asset', id: state.asset.id, project_id: '10', name: state.asset.name, row_version: '1', status: 'created' },
    { kind: 'shot', id: state.shots[0].id, project_id: '10', name: '开场分镜', row_version: '2', status: 'updated' },
    { kind: 'asset', id: '9007199254740997', project_id: '10', name: '已移除的旧道具', row_version: '3', status: 'deleted' },
  ];
  state.beginReply('已保存。'); state.startOperation();
  state.messages['301'].push({ id: 'standard-question', task_id: '501', seq: 1, role: 'user', content: '创建素材与分镜', references: [], artifacts: [], created_at: time });
  Object.assign(state.operations[0], { id: '9007199254740999', tool_name: 'apply_standard_patch', result_refs: refs });
  state.finishOperation();
  await page.route('**/api/v1/assistant/operations/9007199254740999', route => route.fulfill({ json: { ...state.operations[0], result: { project_id: '10', result_refs: refs, already_applied: false } } }));
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await trace.getByText('保存标准创作内容', { exact: true }).click();
  await expect(trace.getByRole('article')).toHaveCount(3);
  await expect(trace.getByRole('article', { name: `素材成果：${state.asset.name}`, exact: true })).toContainText('已创建');
  await expect(trace.getByRole('article', { name: '分镜成果：开场分镜', exact: true })).toContainText('已修改');
  await expect(trace.getByRole('article', { name: '素材成果：已移除的旧道具', exact: true }).getByRole('link')).toHaveCount(0);
  const assetLink = trace.getByRole('link', { name: state.asset.name, exact: true });
  await expect(assetLink).toHaveAttribute('href', '/projects/10/assets?asset=9007199254740993');
  await expect(trace.getByRole('link', { name: '开场分镜', exact: true })).toHaveAttribute('href', '/projects/10/storyboard?shot=9007199254740995');
  await assetLink.focus(); await expect(assetLink).toBeFocused();
  await expect.poll(() => trace.evaluate(element => element.getAnimations({ subtree: true }).every(animation => animation.playState !== 'running'))).toBe(true);
  await page.screenshot({ path: info.outputPath(`standard-results-${width}.png`) });
  const bounds = await trace.boundingBox(); expect(bounds).not.toBeNull(); expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
  await trace.getByRole('button', { name: '查看回执', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '保存标准创作内容', exact: true });
  await expect(dialog.getByRole('article')).toHaveCount(3);
  await expect(dialog.locator('.ai-assistant-receipt')).toContainText('already_applied');
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath(`standard-receipt-${width}.png`) });
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.reload();
  await trace.getByText('保存标准创作内容', { exact: true }).click();
  await expect(trace.getByRole('article')).toHaveCount(3);
  await assetLink.click();
  await expect(page).toHaveURL(/\/projects\/10\/assets\?asset=9007199254740993/);
  await expect(page.getByRole('region', { name: `素材 ${state.asset.name}`, exact: true })).toBeVisible();
  if (width === 390) await page.getByRole('button', { name: '展开生成设置', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '素材名称', exact: true })).toHaveValue(state.asset.name);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`media batches retain a pending stop until saved outputs settle at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  state.beginReply('正在生成图片。'); state.startOperation();
  state.messages['301'].push({ id: 'media-question', task_id: '501', seq: 1, role: 'user', content: '为已选素材生成两张图片', references: [], artifacts: [], created_at: time });
  Object.assign(state.operations[0], { tool_name: 'submit_generation_batch', outcome: 'waiting_media', phase: 'waiting_media', state_version: '2' });
  Object.assign(state.tasks[0], { status: 'waiting_media', phase: 'waiting_media' });
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await expect(trace.getByText('生成并回填媒体', { exact: true })).toBeVisible();
  await expect(trace.getByText('生成中', { exact: true })).toBeVisible();
  await page.locator('.ai-assistant-sender').getByRole('button', { name: '停止', exact: true }).click();
  await expect(page.getByText('当前工具完整结束后停止', { exact: true })).toBeVisible();
  expect(state.operations[0].outcome).toBe('waiting_media');
  const ref = { kind: 'asset', id: '9007199254740993', project_id: '10', name: '图片组 9007199254740995', status: 'adopted', row_version: '2', image_group_id: '9007199254740995', primary_version: '2', generated_count: 2, requested_count: 2 };
  state.operations[0].result_refs = [ref]; state.finishOperation();
  await page.reload();
  await trace.getByText('生成并回填媒体', { exact: true }).click();
  const card = trace.getByRole('article', { name: `素材成果：${ref.name}`, exact: true });
  await expect(card).toContainText('已设为主图');
  await expect(card).toContainText('已生成 2 / 2 张');
  await expect(card.getByRole('link')).toHaveAttribute('href', '/projects/10/assets?asset=9007199254740993&source_image_group=9007199254740995');
  await card.getByRole('link').focus(); await expect(card.getByRole('link')).toBeFocused();
  await expect.poll(() => trace.evaluate(element => element.getAnimations({ subtree: true }).every(animation => animation.playState !== 'running'))).toBe(true);
  expect(await trace.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath(`assistant-media-batch-${width}.png`) });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`character voice results preview the actual generated sample at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  state.beginReply('角色音色已生成并采用。'); state.startOperation();
  state.messages['301'].push({ id: 'voice-question', task_id: '501', seq: 1, role: 'user', content: '为已选角色生成一个音色', references: [], artifacts: [], created_at: time });
  const ref = { kind: 'asset', id: state.asset.id, project_id: '10', name: '林晚 · 角色音色', status: 'adopted', row_version: '2', voice_version: '9007199254740993', media_kind: 'audio', media_role: 'character_voice', media_id: '9007199254740997', generation_id: '9007199254740995', generated_count: 1, requested_count: 1 };
  Object.assign(state.operations[0], { tool_name: 'submit_generation_batch', result_refs: [ref] }); state.finishOperation();
  let fail = true, sampleUrl: string | null = null;
  await page.route(`**/api/v1/projects/10/characters/${state.asset.id}/voice`, route => {
    if (fail) { fail = false; return route.fulfill({ status: 503, json: { error: { code: 'unavailable', message: '样音读取暂时失败' } } }); }
    return route.fulfill({ json: { row_version: 6, record_id: 'newer-record', current_voice: { media_id: 'newer-media', url: 'data:audio/wav;base64,UklGRg==', duration_ms: 4000, row_version: 6 }, candidates: [{ record_id: 'original-record', task_id: ref.generation_id, status: 'succeeded', can_resume: false, media_id: ref.media_id, duration_ms: 3200, url: sampleUrl, description: '本次生成的沉稳男声', preview_text: '本次明确的试音文本', adoptable: true }] } });
  });
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await trace.getByText('生成并回填媒体', { exact: true }).click();
  const card = trace.getByRole('article', { name: `素材成果：${ref.name}`, exact: true });
  await expect(card).toContainText('已自动采用'); await expect(card).toContainText('音色版本 9007199254740993');
  await expect(card).toContainText('已生成 1 / 1 个音色');
  await expect(card.getByRole('link')).toHaveAttribute('href', `/projects/10/assets?asset=${state.asset.id}`);
  const preview = card.getByRole('button', { name: '试听生成音色', exact: true });
  await preview.focus(); await expect(preview).toBeFocused();
  await expect.poll(() => trace.evaluate(element => element.getAnimations({ subtree: true }).every(animation => animation.playState !== 'running'))).toBe(true);
  await page.screenshot({ path: info.outputPath(`assistant-voice-result-${width}.png`) });
  await preview.click();
  const dialog = page.getByRole('dialog', { name: '试听生成音色', exact: true });
  await expect(dialog.getByRole('alert')).toContainText('服务暂时不可用，请稍后重试。');
  await dialog.getByRole('button', { name: '重新读取样音', exact: true }).click();
  await expect(dialog.getByText('样音地址暂不可用。', { exact: true })).toBeVisible();
  sampleUrl = 'data:audio/wav;base64,UklGRg==';
  await dialog.getByRole('button', { name: '刷新样音地址', exact: true }).click();
  await expect(dialog.getByText('本次生成的沉稳男声', { exact: true })).toBeVisible();
  await expect(dialog.getByLabel('试听本次生成音色')).toHaveAttribute('src', sampleUrl);
  await expect(dialog.getByText('样音 3.20 秒', { exact: true })).toBeVisible();
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  const bounds = (await dialog.boundingBox())!; expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
  await page.screenshot({ path: info.outputPath(`assistant-voice-preview-${width}.png`) });
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await expect(preview).toBeFocused();
  ref.status = 'source_unavailable'; await page.reload();
  await trace.getByText('生成并回填媒体', { exact: true }).click();
  await expect(card.getByRole('link')).toHaveCount(0); await expect(card.getByRole('button')).toHaveCount(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`standard video result opens the saved shot video at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  state.shots[0].id = '9007199254740993';
  state.beginReply('视频已生成并采用。'); state.startOperation();
  state.messages['301'].push({ id: 'video-question', task_id: '501', seq: 1, role: 'user', content: '为已选分镜生成视频', references: [], artifacts: [], created_at: time });
  const ref = { kind: 'shot', id: state.shots[0].id, project_id: '10', name: '雨夜站台视频', status: 'adopted', row_version: '6', media_kind: 'video', generation_id: '9007199254740995', generated_count: 1, requested_count: 1 };
  Object.assign(state.operations[0], { tool_name: 'submit_generation_batch', result_refs: [ref] }); state.finishOperation();
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await trace.getByText('生成并回填媒体', { exact: true }).click();
  const card = trace.getByRole('article', { name: `分镜成果：${ref.name}`, exact: true });
  await expect(card).toContainText('已自动采用');
  await expect(card).toContainText('已生成 1 / 1 段，其他结果保留在私人任务和素材');
  const link = card.getByRole('link');
  await expect(link).toHaveAttribute('href', '/projects/10/storyboard?shot=9007199254740993&source_media=video');
  await link.focus(); await expect(link).toBeFocused();
  await expect.poll(() => trace.evaluate(element => element.getAnimations({ subtree: true }).every(animation => animation.playState !== 'running'))).toBe(true);
  expect(await trace.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath(`assistant-standard-video-${width}.png`) });
  await link.click();
  await expect(page).toHaveURL(/source_media=video/);
  await expect(page.getByRole('tab', { name: '分镜视频', exact: true })).toHaveAttribute('aria-selected', 'true');
  if (width === 390) await page.getByRole('button', { name: '展开生成设置', exact: true }).click();
  await expect(page.getByLabel('分镜视频预览', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) for (const mediaKind of ['image', 'video', 'audio'] as const) test(`canvas ${mediaKind} media cards keep native results, quantities and source availability at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  state.beginReply('媒体已生成并回填。'); state.startOperation();
  state.messages['301'].push({ id: 'canvas-media-question', task_id: '501', seq: 1, role: 'user', content: '为已选节点生成媒体', references: [], artifacts: [], created_at: time });
  const ref = { kind: 'canvas_node', id: 'node-9007199254740993', project_id: '10', canvas_id: 'canvas-key', name: '雨夜画面与声音', status: 'bound', row_version: '6', private_revision: '8', media_kind: mediaKind, generation_id: '9007199254740995', generated_count: 1, requested_count: 1 };
  Object.assign(state.operations[0], { tool_name: 'submit_generation_batch', result_refs: [ref] }); state.finishOperation();
  await page.route('**/api/v1/assistant/operations/801', route => route.fulfill({ json: { ...state.operations[0], result: { operation_id: '801', settled: true, items: [{ media_kind: mediaKind, generation_id: ref.generation_id, status: 'succeeded', result: { bound: true } }] } } }));
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await trace.getByText('生成并回填媒体', { exact: true }).click();
  const card = trace.getByRole('article', { name: `节点成果：${ref.name}`, exact: true });
  await expect(card).toContainText('已回填节点'); await expect(card).toContainText('参数版本 8');
  await expect(card).toContainText(`已生成 1 / 1 ${mediaKind === 'image' ? '张' : '段'}，其他结果保留在私人任务和素材`);
  const link = card.getByRole('link');
  await expect(link).toHaveAttribute('href', '/canvas-app/canvas/canvas-key?node=node-9007199254740993');
  await link.focus(); await expect(link).toBeFocused();
  await expect.poll(() => trace.evaluate(element => element.getAnimations({ subtree: true }).every(animation => animation.playState !== 'running'))).toBe(true);
  expect(await trace.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath(`assistant-canvas-${mediaKind}-${width}.png`) });
  await trace.getByRole('button', { name: '查看回执', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '生成并回填媒体' }).locator('.ai-assistant-receipt')).toContainText(`"generation_id": "${ref.generation_id}"`);
  await page.getByRole('dialog', { name: '生成并回填媒体' }).getByRole('button', { name: '关闭', exact: true }).click();
  state.operations[0].result_refs = [{ ...ref, status: 'source_unavailable' }];
  await page.reload(); await trace.getByText('生成并回填媒体', { exact: true }).click();
  await expect(card).toContainText('来源不可用'); await expect(card.getByRole('link')).toHaveCount(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('media reconciliation and adoption conflicts keep the original receipt and saved group visible', async ({ page }) => {
  const state = await assistantFixture(page);
  state.beginReply('核对原请求'); state.startOperation();
  state.messages['301'].push({ id: 'media-unknown-question', task_id: '501', seq: 1, role: 'user', content: '为已选素材生成两张图片', references: [], artifacts: [], created_at: time });
  Object.assign(state.operations[0], { tool_name: 'submit_generation_batch', outcome: 'reconciling', phase: 'reconciling', state_version: '3' });
  Object.assign(state.tasks[0], { status: 'waiting_media', phase: 'reconciling' });
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await expect(trace.getByText('正在核实结果', { exact: true })).toBeVisible();
  const ref = { kind: 'asset', id: '501', project_id: '10', name: '图片组 701', status: 'adoption_conflict', image_group_id: '701', generated_count: 1, requested_count: 2 };
  Object.assign(state.operations[0], { outcome: 'conflict', phase: 'terminal', state_version: '4', result_refs: [ref], error: { code: 'assistant_media_batch_failed', message: '成功产物已保存，请核对' } });
  Object.assign(state.tasks[0], { status: 'paused_error', phase: 'terminal', active_operation: null, row_version: '4' });
  await page.route('**/api/v1/assistant/operations/801', route => route.fulfill({ json: { ...state.operations[0], result: { operation_id: '801', settled: true, items: [{ generation_id: '9011', status: 'conflict', result: { image_group_id: '701', adopted: false } }] } } }));
  await page.reload();
  await trace.getByText('生成并回填媒体', { exact: true }).click();
  const card = trace.getByRole('article', { name: '素材成果：图片组 701', exact: true });
  await expect(card).toContainText('回填有冲突'); await expect(card).toContainText('已生成 1 / 2 张');
  await trace.getByRole('button', { name: '查看回执', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '生成并回填媒体' }).locator('.ai-assistant-receipt')).toContainText('"generation_id": "9011"');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`on demand image reads show their exact receipt at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  state.beginReply('按已选图位读取图片。'); state.startOperation();
  state.messages['301'].push({ id: 'read-image-question', task_id: '501', seq: 1, role: 'user', content: '查看这张图片的构图', references: [], artifacts: [], created_at: time });
  Object.assign(state.operations[0], { tool_name: 'read_image_content' }); state.finishOperation();
  const result = { group_id: '9007199254740993', item_id: '9007199254740995', media_id: '9007199254740997', source_changed: false, delivery: 'next_model_input' };
  await page.route('**/api/v1/assistant/operations/801', route => route.fulfill({ json: { ...state.operations[0], result } }));
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await trace.getByText('读取指定图片', { exact: true }).click();
  await trace.getByRole('button', { name: '查看回执', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '读取指定图片', exact: true });
  await expect(dialog.locator('.ai-assistant-receipt')).toContainText('"media_id": "9007199254740997"');
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath(`image-content-receipt-${width}.png`) });
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.reload();
  await expect(trace.getByText('读取指定图片', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`image primary cards show adoption, versions and exact group locations at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  const ref = { kind: 'asset', id: '9007199254740993', project_id: '10', name: '图片组 9007199254740995 · 图位 2',
    row_version: '6', status: 'adopted', image_group_id: '9007199254740995', image_slot_id: '9007199254740997', primary_version: '3', item_version: '2' };
  state.beginReply('已将第二张设为主图。'); state.startOperation();
  state.messages['301'].push({ id: 'primary-question', task_id: '501', seq: 1, role: 'user', content: '把第二张设为主图', references: [], artifacts: [], created_at: time });
  Object.assign(state.operations[0], { id: '9007199254740999', tool_name: 'set_image_group_primary', result_refs: [ref] });
  state.finishOperation();
  await page.route('**/api/v1/assistant/operations/9007199254740999', route => route.fulfill({ json: { ...state.operations[0], result: { adopted: true, result_refs: [ref], already_applied: false } } }));
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await trace.getByText('设置图片组主图', { exact: true }).click();
  const card = trace.getByRole('article', { name: `素材成果：${ref.name}`, exact: true });
  await expect(card).toContainText('已设为主图');
  await expect(card).toContainText('保存版本 6');
  await expect(card).toContainText('主图版本 3');
  await expect(card).toContainText('图位版本 2');
  const link = card.getByRole('link', { name: ref.name, exact: true });
  await expect(link).toHaveAttribute('href', '/projects/10/assets?asset=9007199254740993&source_image_group=9007199254740995&source_image_slot=9007199254740997');
  await link.focus(); await expect(link).toBeFocused();
  await trace.getByRole('button', { name: '查看回执', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '设置图片组主图', exact: true });
  await expect(dialog.locator('.ai-assistant-receipt')).toContainText('"adopted": true');
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath(`image-primary-receipt-${width}.png`) });
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.reload();
  await trace.getByText('设置图片组主图', { exact: true }).click();
  await expect(card).toContainText('已设为主图');
  await expect.poll(() => trace.evaluate(element => element.getAnimations({ subtree: true }).every(animation => animation.playState !== 'running'))).toBe(true);
  expect(await trace.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath(`image-primary-${width}.png`) });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`canvas result cards preserve node locations, private versions and deletion at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  const refs = [
    { kind: 'canvas_node', id: 'assistant-9007199254740999-story', project_id: '10', canvas_id: 'canvas-key', name: '雨夜故事文本', row_version: '1', private_revision: '1', status: 'created' },
    { kind: 'canvas_node', id: 'old-image', project_id: '10', canvas_id: 'canvas-key', name: '已移除的画面', row_version: '3', private_revision: '0', status: 'deleted' },
  ];
  state.beginReply('节点已保存。'); state.startOperation();
  state.messages['301'].push({ id: 'canvas-question', task_id: '501', seq: 1, role: 'user', content: '创建文本节点', references: [], artifacts: [], created_at: time });
  Object.assign(state.operations[0], { id: '9007199254740999', tool_name: 'apply_canvas_patch', result_refs: refs });
  state.finishOperation();
  await page.route('**/api/v1/assistant/operations/9007199254740999', route => route.fulfill({ json: { ...state.operations[0], result: { project_id: '10', canvas_id: 'canvas-key', canvas_revision: '4', result_refs: refs, already_applied: false } } }));
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await trace.getByText('保存画布节点与连线', { exact: true }).click();
  const card = trace.getByRole('article', { name: '节点成果：雨夜故事文本', exact: true });
  await expect(card).toContainText('已创建');
  await expect(card).toContainText('保存版本 1');
  await expect(card).toContainText('参数版本 1');
  await expect(trace.getByRole('article', { name: '节点成果：已移除的画面', exact: true })).toContainText('已删除');
  await expect(trace.getByRole('article', { name: '节点成果：已移除的画面', exact: true }).getByRole('link')).toHaveCount(0);
  const link = card.getByRole('link', { name: '雨夜故事文本', exact: true });
  await expect(link).toHaveAttribute('href', '/canvas-app/canvas/canvas-key?node=assistant-9007199254740999-story');
  await link.focus(); await expect(link).toBeFocused();
  await expect(trace).toContainText('操作批次 9007199254740999');
  await trace.getByRole('button', { name: '查看回执', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '保存画布节点与连线', exact: true });
  await expect(dialog.getByRole('article')).toHaveCount(2);
  await expect(dialog.locator('.ai-assistant-receipt')).toContainText('canvas_revision');
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath(`canvas-receipt-${width}.png`) });
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.reload();
  await trace.getByText('保存画布节点与连线', { exact: true }).click();
  await expect(trace.getByRole('article')).toHaveCount(2);
  const bounds = await trace.boundingBox(); expect(bounds).not.toBeNull(); expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
  await expect.poll(() => trace.evaluate(element => element.getAnimations({ subtree: true }).every(animation => animation.playState !== 'running'))).toBe(true);
  await page.screenshot({ path: info.outputPath(`canvas-results-${width}.png`) });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('an uncertain attachment keeps the draft and reuses the original reference request', async ({ page }) => {
  const state = await assistantFixture(page);
  let rejectReference = true;
  const attempts: { body: any; key: string | undefined }[] = [];
  await page.route('**/api/v1/assistant/conversations/301/attachments/references', route => {
    attempts.push({ body: route.request().postDataJSON(), key: route.request().headers()['idempotency-key'] });
    if (rejectReference) { rejectReference = false; return route.abort('failed'); }
    return route.fallback();
  });
  await page.goto(`${root}/source?assistant=open`);
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await input.fill('保留附件草稿');
  await page.getByRole('button', { name: '添加附件', exact: true }).click();
  await page.getByRole('menuitem', { name: '从资产库选择', exact: true }).click();
  await page.getByRole('dialog', { name: '添加资产上下文', exact: true }).getByRole('button', { name: '添加', exact: true }).first().click();
  await expect(page.locator('.studio-message').filter({ hasText: '无法连接服务' })).toBeVisible();
  await openSaveDetails(page);
  await expect(page.getByRole('button', { name: '使用原请求核对资产引用', exact: true })).toBeVisible();
  await expect(input).toHaveValue('保留附件草稿');
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '使用原请求核对资产引用', exact: true }).click();
  await expect(page.getByLabel('本次消息附件')).toContainText('林晚');
  const referenceRequests = state.calls.filter(call => call.path.endsWith('/attachments/references'));
  expect(referenceRequests).toHaveLength(1);
  expect(attempts).toHaveLength(2); expect(attempts[0].key).toBeTruthy(); expect(attempts[1]).toEqual(attempts[0]);
  expect(state.calls.filter(call => call.method === 'POST' && call.path.endsWith('/messages'))).toHaveLength(0);
  await expect(input).toHaveValue('保留附件草稿');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('uncertain task controls survive refresh and reuse the frozen control request', async ({ page }) => {
  const state = await assistantFixture(page); state.beginReply('讨论当前故事'); state.abortNextControl();
  await page.goto(root + '/source?assistant=open');
  await expect(page).toHaveURL(/conversation=301/);
  await page.locator('.ai-assistant-sender').getByRole('button', { name: '停止', exact: true }).click();
  await openSaveDetails(page);
  await expect(page.getByRole('button', { name: '使用原请求核对控制', exact: true })).toBeVisible();
  await page.reload();
  await openSaveDetails(page);
  await page.getByRole('button', { name: '使用原请求核对控制', exact: true }).click();
  await expect(page.getByRole('button', { name: '使用原请求核对控制', exact: true })).toHaveCount(0);
  const calls = state.calls.filter(call => call.path === '/tasks/501/control');
  expect(calls).toHaveLength(2); expect(calls[1].body).toEqual(calls[0].body); expect(calls[1].key).toBe(calls[0].key);
  expect(state.tasks[0].control_seq).toBe('1'); expect(state.tasks[0].status).toBe('stopped');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('uncertain deletion survives refresh after the conversation is already hidden', async ({ page }) => {
  const state = await assistantFixture(page); state.abortNextDeletion();
  await page.goto(root + '/source?assistant=open');
  await expect(page).toHaveURL(/conversation=301/);
  await page.getByRole('button', { name: '对话记录', exact: true }).click();
  const history = page.getByRole('dialog', { name: '对话记录', exact: true });
  await history.getByRole('button', { name: '管理对话故事讨论', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '删除对话', exact: true });
  await confirmation.getByRole('button', { name: '永久删除', exact: true }).click();
  await confirmation.getByRole('button', { name: /^提示详情：/ }).click();
  await expect(confirmation.getByRole('button', { name: '使用原请求核对删除', exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '对话记录', exact: true }).click();
  await history.getByRole('button', { name: /^提示详情：/ }).click();
  await history.getByRole('button', { name: '使用原请求核对删除', exact: true }).click();
  await expect(history.getByRole('button', { name: '使用原请求核对删除', exact: true })).toHaveCount(0);
  const calls = state.calls.filter(call => call.method === 'DELETE' && call.path === '/global/conversations/301');
  expect(calls).toHaveLength(2); expect(calls[1].body).toEqual(calls[0].body); expect(calls[1].key).toBe(calls[0].key);
  expect(calls[1].body.clear_derived_memory).toBe(false); expect(state.conversations[0].status).toBe('deleted');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('a legacy pending deletion keeps its exact original selection without showing memory controls', async ({ page }) => {
  const state = await assistantFixture(page);
  state.deletionMemories.push({ id: '901', content_version: '3' });
  await page.goto(`${root}/source?assistant=open`);
  const pending = { version: 1, kind: 'delete', targetId: '301', key: 'legacy-delete-request', body: { row_version: '1', clear_derived_memory: true, memory_selections: [{ id: '901', content_version: '3' }] } };
  const storageKey = 'short-drama:user:9007199254740993:assistant-pending-delete:v1:history';
  await page.evaluate(({ storageKey, pending }) => localStorage.setItem(storageKey, JSON.stringify(pending)), { storageKey, pending });
  await page.getByRole('button', { name: '对话记录', exact: true }).click();
  const history = page.getByRole('dialog', { name: '对话记录', exact: true });
  await history.getByRole('button', { name: /^提示详情：/ }).click();
  await history.getByRole('button', { name: '使用原请求核对删除', exact: true }).click();
  await expect(history.getByRole('button', { name: '使用原请求核对删除', exact: true })).toHaveCount(0);
  const call = state.calls.find(call => call.method === 'DELETE')!;
  expect(call.key).toBe(pending.key); expect(call.body).toEqual(pending.body);
  expect(await page.evaluate(key => localStorage.getItem(key), storageKey)).toBeNull();
  expect(state.calls.filter(call => call.path.includes('memory'))).toHaveLength(0);
  await expect(history.getByRole('checkbox')).toHaveCount(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`memory and task configuration stay invisible including conversation deletion at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  const state = await assistantFixture(page);
  await page.goto(`${root}/source?assistant=open`);
  for (const name of ['记忆管理', '任务设置', '管理任务引用', '附件与技能']) await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0);
  await expect(page.getByText('对话仅你可见', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '对话记录', exact: true }).click();
  const history = page.getByRole('dialog', { name: '对话记录', exact: true });
  await expect(history.getByRole('checkbox')).toHaveCount(0);
  await history.getByRole('button', { name: '管理对话故事讨论', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '删除对话', exact: true });
  await expect(confirmation).not.toContainText('记忆');
  await confirmation.getByRole('button', { name: '永久删除', exact: true }).click();
  await expect(confirmation).toHaveCount(0);
  expect(state.calls.find(call => call.method === 'DELETE')!.body).toEqual({ row_version: '1', clear_derived_memory: false });
  expect(state.calls.filter(call => call.path.includes('memory'))).toHaveLength(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`saved sound references remain visible and removal survives refresh at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page); state.beginReply('核对台词配音。');
  const option = { project_id: '10', source_kind: 'project', source_id: '10', kind: 'sound_line', id: 'deadbeef', revision: '9007199254740993', name: '旁白 · deadbeef', project_name: '测试项目', source_name: '测试项目' };
  state.tasks[0].selected_subjects = [state.subject(option)];
  await page.goto(root + '/source?assistant=open');
  await expect(page.getByRole('link', { name: '查看引用来源旁白 · deadbeef', exact: true })).toHaveAttribute('href', '/projects/10/assembly?source_line=deadbeef');
  await page.getByRole('button', { name: '移除任务引用旁白 · deadbeef', exact: true }).click();
  const input = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await input.fill('不再引用此台词'); await page.reload();
  await expect(input).toHaveValue('不再引用此台词');
  await expect(page.getByLabel('当前任务引用', { exact: true })).not.toContainText(option.name);
  await page.locator('.ai-assistant-sender').getByRole('button', { name: '停止', exact: true }).click();
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(input).toHaveValue('');
  const sent = state.calls.find(call => call.method === 'POST' && call.path.endsWith('/messages'))!;
  expect(sent.body.subject_selection).toEqual({ expected_input_revision: '1', retain_keys: [], subjects: [] });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`dialogue audio results restore native source and exact receipt at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page); state.beginReply('所选台词已配音并采用。'); state.startOperation();
  state.messages['301'].push({ id: 'dubbing-question', task_id: '501', seq: 1, role: 'user', content: '为已选台词生成配音', references: [], artifacts: [], created_at: time });
  const ref = { kind: 'sound_line', id: 'line_a-9', project_id: '10', name: '林晚 · line_a-9', status: 'adopted', row_version: '9007199254740993', media_kind: 'audio', media_role: 'dialogue_audio', media_id: '9007199254740997', generation_id: '9007199254740995', generated_count: 1, requested_count: 1 };
  Object.assign(state.operations[0], { tool_name: 'submit_generation_batch', result_refs: [ref] }); state.finishOperation();
  await page.route('**/api/v1/assistant/operations/801', route => route.fulfill({ json: { ...state.operations[0], result: { settled: true, items: [{ target_kind: 'sound_line', target_id: ref.id, generation_id: ref.generation_id }] } } }));
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await trace.getByText('生成并回填媒体', { exact: true }).click();
  const card = trace.getByRole('article', { name: `配音成果：${ref.name}`, exact: true });
  await expect(card).toContainText('已自动采用'); await expect(card).toContainText('保存版本 9007199254740993');
  await expect(card.getByRole('link')).toHaveAttribute('href', '/projects/10/assembly?source_line=line_a-9');
  await card.getByRole('link').focus(); await expect(card.getByRole('link')).toBeFocused();
  await trace.getByRole('button', { name: '查看回执', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '生成并回填媒体', exact: true });
  await expect(dialog.locator('.ai-assistant-receipt')).toContainText('line_a-9');
  await expect(dialog.locator('.ai-assistant-receipt')).toContainText('9007199254740995');
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath(`dialogue-audio-receipt-${width}.png`) });
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.reload(); await trace.getByText('生成并回填媒体', { exact: true }).click();
  await expect(card).toContainText('已自动采用');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});


for (const width of [1440, 390]) test(`sound text results recover saved lines and receipts at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page); state.beginReply('台词已保存。'); state.startOperation();
  state.messages['301'].push({ id: 'sound-text-question', task_id: '501', seq: 1, role: 'user', content: '创建台词并修改本任务台词', references: [], artifacts: [], created_at: time });
  const lineId = '943de689-ec54-59a4-86a0-57dc5cfa3f52';
  const refs = [
    { kind: 'sound_line', id: lineId, project_id: '10', name: `林晚 · ${lineId}`, status: 'created', row_version: '9007199254740993', assembly_version: '9007199254740995' },
    { kind: 'sound_line', id: 'line_a-9', project_id: '10', name: '旁白 · line_a-9', status: 'updated', row_version: '9007199254740993', assembly_version: '9007199254740995' },
  ];
  Object.assign(state.operations[0], { tool_name: 'apply_standard_patch', result_refs: refs }); state.finishOperation();
  await page.route('**/api/v1/assistant/operations/801', route => route.fulfill({ json: { ...state.operations[0], result: { project_id: '10', result_refs: refs, sound_revision: { row_version: '9007199254740993', assembly_version: '9007199254740995', timeline_hash: 'a'.repeat(64) } } } }));
  await page.goto(root + '/source?assistant=open');
  const trace = page.getByRole('region', { name: '任务过程：讨论故事' });
  await trace.getByText('保存标准创作内容', { exact: true }).click();
  const card = trace.getByRole('article', { name: `台词成果：${refs[0].name}`, exact: true });
  await expect(card).toContainText('已创建'); await expect(card).toContainText('画面版本 9007199254740995');
  await expect(trace.getByRole('article', { name: `台词成果：${refs[1].name}`, exact: true })).toContainText('已修改');
  await expect(trace.getByRole('article').filter({ hasText: '已自动采用' })).toHaveCount(0);
  await expect(card.getByRole('link')).toHaveAttribute('href', `/projects/10/assembly?source_line=${lineId}`);
  await card.getByRole('link').focus(); await expect(card.getByRole('link')).toBeFocused();
  expect(await card.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath(`sound-text-results-${width}.png`) });
  await trace.getByRole('button', { name: '查看回执', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '保存标准创作内容', exact: true });
  await expect(dialog.locator('.ai-assistant-receipt')).toContainText('sound_revision');
  await expect(dialog.getByRole('article')).toHaveCount(2);
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath(`sound-text-receipt-${width}.png`) });
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.reload(); await trace.getByText('保存标准创作内容', { exact: true }).click();
  await expect(card).toContainText('已创建');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 390]) test(`dialogue audio source opens only the real saved sound line at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1000 });
  const state = await assistantFixture(page);
  const line = { id: 'line_a-9', character: '林晚', text: '这封信终于送到了。', voice: 'alloy', config_id: '77', start_ms: 0, media_id: null, adopted_hash: null };
  const sound = { mode: 'legacy', row_version: '9007199254740993', timeline_hash: 'a'.repeat(64), duration_ms: 0, needs_review: false, stale_lines: [], document: { schema_version: 2, dialogue: [line, { ...line, id: 'line_other', text: '另一条保留台词' }], audio_clips: [], tracks: [], subtitles: [], native_ducking: [], music: null, original_volume: 1, dialogue_volume: 1, burn_subtitles: false, font_size: 24 }, media: {}, uploads: [], voice_defaults: { row_version: '0', voices: {} } };
  await page.route('**/api/v1/projects/10/assembly/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/editor')) return route.fulfill({ json: { editor_version: 2, assembly: { id: '111', row_version: '1', aspect: '16:9', resolution: '720p', current_media_id: null }, source_hash: 'a'.repeat(64), context_hash: 'a'.repeat(64), clips: [], sources: [], changes: [], jobs: [], sound, materials: [], voice_context_hash: 'b'.repeat(64) } });
    if (path.endsWith('/exports')) return route.fulfill({ json: { items: [], has_more: false } });
    if (path.endsWith('/materials') || path.endsWith('/media-jobs')) return route.fulfill({ json: { items: [] } });
    return route.fallback();
  });
  await page.route('**/api/v1/projects/10/sound**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/sound-mode')) return route.fulfill({ json: { mode: 'legacy', row_version: '1' } });
    if (path.endsWith('/capabilities')) return route.fulfill({ json: { enabled: true } });
    if (path.includes('/candidates/')) return route.fulfill({ json: [] });
    if (path.endsWith('/sound')) return route.fulfill({ json: sound });
    return route.fallback();
  });
  await page.goto(root + '/assembly?source_line=line_a-9');
  await expect(page.getByRole('textbox', { name: '台词', exact: true })).toHaveValue(line.text);
  await expect(page.getByRole('navigation', { name: '台词列表' }).getByRole('button', { name: /林晚.*这封信/ })).toHaveAttribute('aria-pressed', 'true');
  await page.screenshot({ path: info.outputPath(`dialogue-audio-source-${width}.png`) });
  await page.goto(root + '/assembly?source_line=missing_line');
  await expect(page.getByRole('textbox', { name: '台词', exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
