import { expect, test, type Page } from '@playwright/test';
import type { AgentAttachment, AgentModel, AgentSkill } from '../src/api/types/agents';
import { fixture, image, root } from './studio-fixture';
import { captureRefinement, refinementViewports } from './refinement-visual';

const time = '2026-10-03T00:00:00Z';
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}

async function contextFixture(page: Page, stage: 'source' | 'assets' = 'source') {
  const base = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const models: AgentModel[] = [
    { id: '71', name: '文字协作模型', model_key: 'fixture-text', row_version: '1', protocol: 'chat', verified: true, tool_calling: true, tool_result_continuation: true, streaming: 'verified', preferred: true,
      input_capabilities: { text: true, image: false, audio: false, video: 'unsupported', evidence: 'text_only' } },
    { id: '72', name: '视觉协作模型', model_key: 'fixture-visual', row_version: '1', protocol: 'chat', verified: true, tool_calling: true, tool_result_continuation: true, streaming: 'verified', preferred: false,
      input_capabilities: { text: true, image: true, audio: false, video: 'sampled_frames', evidence: 'model_family' } },
  ];
  const skills: AgentSkill[] = [{ id: 'builtin:continuity', name: '人物连续性', filename: null, builtin: true, content_version: '1', row_version: null, enabled: true, instructions: '核对人物外貌与动机的连续性。', checksum_sha256: 'a'.repeat(64) }];
  const conversation = { id: '301', project_id: '10', episode_id: '20', stage, subject_type: 'episode', subject_id: '20', task_type: stage === 'source' ? 'writing' : 'extraction', scope_version: 1, title: '本人的素材讨论', row_version: '1', archived: false, last_run_status: null, created_at: time, updated_at: time };
  const state = {
    models, skills, attachments: [] as AgentAttachment[], messages: [] as Record<string, unknown>[],
    sends: [] as { body: Record<string, any>; key: string }[], uploads: [] as { key: string; body: string }[],
    references: [] as Record<string, unknown>[], modelUpdates: [] as Record<string, unknown>[],
    skillUpdates: [] as Record<string, unknown>[], skillDeletes: [] as Record<string, unknown>[], failSkill: false,
    uploadKind: 'text' as AgentAttachment['kind'], failUpload: false, failLoad: false,
    attachmentGate: null as ReturnType<typeof deferred> | null, attachmentReads: 0, abortAcceptedSend: false,
  };
  const receipts = new Map<string, { body: string; result: Record<string, unknown> }>();
  const attachment = (kind: AgentAttachment['kind'], name: string): AgentAttachment => ({
    id: String(7001 + state.attachments.length), kind, name, mime_type: kind === 'text' ? 'text/plain' : `${kind}/${kind === 'image' ? 'png' : kind === 'audio' ? 'mpeg' : 'mp4'}`,
    byte_size: 32, media_id: kind === 'text' ? null : String(9001 + state.attachments.length), url: kind === 'image' ? image : null,
    text_preview: kind === 'text' ? '雨夜站台：人物连续性参考。' : null, checksum_sha256: 'b'.repeat(64), pending: true,
    metadata: kind === 'video' ? { has_audio: true } : {},
  });
  await page.route('**/api/v1/agent/**', async route => {
    const request = route.request(); const url = new URL(request.url());
    const path = url.pathname.slice('/api/v1/agent'.length); const method = request.method();
    const isJson = request.headers()['content-type']?.includes('application/json');
    const body = isJson && ['POST', 'PATCH', 'DELETE'].includes(method) ? request.postDataJSON() : null;
    const reply = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path === '/status') return reply({ enabled: true, schema_ready: true });
    if (path === '/models') return reply({ items: models, preferred_id: '71' });
    if (path === '/models/71/inputs' && method === 'PATCH') {
      state.modelUpdates.push(body);
      Object.assign(models[0], { row_version: String(BigInt(models[0].row_version) + 1n), input_capabilities: { text: true, image: body.image, audio: body.audio, video: body.image ? 'sampled_frames' : 'unsupported', evidence: 'declared' } });
      return reply(models[0]);
    }
    if (path === '/conversations') return reply({ items: [conversation], total: 1, offset: 0, limit: 20 });
    if (path === '/conversations/301') return reply(conversation);
    if (path.endsWith('/events')) return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (path.endsWith('/state')) return reply({ conversation_id: '301', cursor: 0, active_run: null, queued_runs: [] });
    if (path.endsWith('/runs')) return reply({ items: [], total: 0, offset: 0, limit: 1 });
    if (path.endsWith('/attachments') && method === 'GET') {
      state.attachmentReads++;
      if (state.attachmentGate) await state.attachmentGate.promise;
      if (state.failLoad) return reply({ error: { code: 'UNAVAILABLE', message: '附件暂时无法载入。' } }, 503);
      return reply({ items: state.attachments.filter(item => item.pending), total: state.attachments.filter(item => item.pending).length, offset: 0, limit: 50 });
    }
    if (path.endsWith('/attachments/uploads') && method === 'POST') {
      state.uploads.push({ key: request.headers()['idempotency-key'], body: request.postDataBuffer()?.toString('utf8') ?? '' });
      if (state.failUpload) return reply({ error: { code: 'UNAVAILABLE', message: '上传结果尚未确认。' } }, 503);
      const item = attachment(state.uploadKind, `${state.uploadKind === 'text' ? '雨夜资料.txt' : state.uploadKind === 'image' ? '角色参考.png' : '站台视频.mp4'}`);
      state.attachments.push(item); return reply(item, 201);
    }
    if (path.endsWith('/attachments/references') && method === 'POST') {
      state.references.push(body);
      const item = attachment('image', '林晚（项目素材）'); state.attachments.push(item); return reply(item, 201);
    }
    if (/\/attachments\/\d+$/.test(path) && method === 'DELETE') {
      state.attachments = state.attachments.filter(item => item.id !== path.split('/').at(-1)); return route.fulfill({ status: 204 });
    }
    if (path === '/skills') return reply({ items: skills, total: skills.length, offset: 0, limit: 50 });
    if (path === '/skills/uploads' && method === 'POST') {
      const skill: AgentSkill = { id: '980', name: '雨夜创作规范', filename: 'rain.md', builtin: false, enabled: true, content_version: '1', row_version: '1', instructions: '保留站台对白。', checksum_sha256: 'c'.repeat(64) };
      skills.push(skill); return reply(skill, 201);
    }
    if (path === '/skills/980') {
      const skill = skills.find(item => item.id === '980')!;
      if (method === 'PATCH') {
        state.skillUpdates.push(body);
        if (state.failSkill) return reply({ error: { code: 'VERSION_CONFLICT', message: '技能版本已更新，请保留修改后重新核对。' } }, 409);
        if (body.name !== undefined || body.instructions !== undefined) skill.content_version = String(BigInt(skill.content_version) + 1n);
        Object.assign(skill, body, { row_version: String(BigInt(skill.row_version!) + 1n) });
        return reply(skill);
      }
      if (method === 'DELETE') {
        state.skillDeletes.push(body); skills.splice(skills.indexOf(skill), 1); return route.fulfill({ status: 204 });
      }
    }
    if (path.endsWith('/messages')) {
      if (method === 'POST') {
        const key = request.headers()['idempotency-key'];
        state.sends.push({ body, key });
        const prior = receipts.get(key);
        if (prior) return JSON.stringify(body) === prior.body ? reply(prior.result, 201)
          : reply({ error: { code: 'VERSION_CONFLICT', message: '原请求内容已改变。' } }, 409);
        const references = [
          ...state.attachments.filter(item => body.attachment_ids?.includes(item.id)).map(item => ({ ...item, type: 'attachment' })),
          ...skills.filter(skill => body.skills?.some((item: { id: string }) => item.id === skill.id)).map(skill => ({ id: skill.id, type: 'skill', name: skill.name, content_version: skill.content_version })),
        ];
        state.attachments.forEach(item => { if (body.attachment_ids?.includes(item.id)) item.pending = false; });
        const message = { id: String(2001 + state.messages.length), seq: state.messages.length + 1, role: 'user', content: body.content, references, artifacts: [], created_at: time };
        state.messages.push(message);
        const result = { message, run: { id: '901', conversation_id: '301', status: 'succeeded', phase: 'finished', row_version: '1', mode: body.mode === 'generate' ? 'workflow' : 'discuss', model_config_id: body.model_config_id, model_name: '测试协作模型', error: null, usage: {}, budget: {}, review: null, awaiting_artifact_ids: [], created_at: time, updated_at: time, finished_at: time }, cursor: 1 };
        receipts.set(key, { body: JSON.stringify(body), result });
        if (state.abortAcceptedSend) { state.abortAcceptedSend = false; return route.abort('failed'); }
        return reply(result, 201);
      }
      return reply({ items: state.messages, total: state.messages.length, offset: 0, limit: 50 });
    }
    base.unexpected.push(`${method} agent${path}`); return reply({ error: { code: 'UNEXPECTED' } }, 501);
  });
  return { ...base, state, attachment };
}

async function upload(page: Page, kind: '文本' | '图片' | '视频', name: string, mimeType: string) {
  await page.getByRole('button', { name: '添加附件', exact: true }).click();
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('menuitem', { name: `添加${kind}`, exact: true }).click();
  await (await chooser).setFiles({ name, mimeType, buffer: Buffer.from('fixture media content') });
}
async function chooseModel(page: Page, label: string) {
  await page.getByRole('button', { name: '选择模型', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '选择 Agent 模型', exact: true });
  await dialog.getByRole('combobox', { name: 'Agent 协作模型', exact: true }).press('ArrowDown');
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: label }).click();
  await dialog.getByRole('button', { name: '完成', exact: true }).click();
}

test('input tools compose attachments, project assets and a versioned skill without changing the work', async ({ page }) => {
  const data = await contextFixture(page, 'assets');
  await page.goto(`${root}/assets?mode=agent&conversation=301`);
  await expect(page.getByRole('textbox', { name: '创作要求', exact: true })).toBeVisible();
  await expect(page.locator('.agent-conversation-toolbar')).toHaveCount(0);
  data.state.uploadKind = 'text';
  await upload(page, '文本', '雨夜资料.txt', 'text/plain');
  await expect(page.locator('.agent-attached-items')).toContainText('雨夜资料.txt');
  data.state.uploadKind = 'image';
  await upload(page, '图片', '角色参考.png', 'image/png');
  await expect(page.getByRole('alert').filter({ hasText: '当前模型不支持图片理解' })).toHaveCount(0);
  await chooseModel(page, '视觉协作模型');
  await page.getByRole('button', { name: '添加资产库', exact: true }).click();
  const assets = page.getByRole('dialog', { name: '添加资产上下文', exact: true });
  await assets.getByRole('button', { name: '添加', exact: true }).first().click();
  await expect(assets).toHaveCount(0);
  await expect(page.locator('.agent-attached-items')).toContainText('林晚（项目素材）');
  await page.getByRole('button', { name: '加载 Skill', exact: true }).click();
  const skills = page.getByRole('dialog', { name: '加载 Skill', exact: true });
  await skills.getByRole('checkbox', { name: '人物连续性', exact: true }).check();
  await skills.getByRole('button', { name: '完成', exact: true }).click();
  await page.getByRole('textbox', { name: '创作要求', exact: true }).fill('根据附件和人物规范整理素材候选。');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect.poll(() => data.state.sends.length).toBe(1);
  expect(data.state.sends[0].body).toEqual({ content: '根据附件和人物规范整理素材候选。', expected_scope: { stage: 'assets', subject_type: 'episode', subject_id: '20', task_type: 'extraction' }, model_config_id: '72', attachment_ids: ['7001', '7002', '7003'], skills: [{ id: 'builtin:continuity', content_version: '1' }], video_audio: 'include' });
  expect(data.state.references).toEqual([{ source_type: 'asset', source_id: '501' }]);
  expect(data.state.uploads.every(item => !!item.key)).toBe(true);
  await expect(page.locator('.agent-attached-items')).toHaveCount(0);
  await expect(page.locator('.agent-message-references')).toContainText('Skill：人物连续性');
  await page.getByText('查看文本资料', { exact: true }).click();
  await expect(page.getByText('雨夜站台：人物连续性参考。', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '预览角色参考.png', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '图片预览', exact: true })).toBeVisible();
  expect(data.writing.editing_script.content).toBe('林晚走进车站，抬头寻找站台。');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('video audio is sent intact unless the user explicitly chooses visual-only', async ({ page }) => {
  const data = await contextFixture(page);
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await page.getByRole('textbox', { name: '创作要求', exact: true }).fill('讨论站台运镜。');
  await chooseModel(page, '视觉协作模型');
  data.state.uploadKind = 'video'; await upload(page, '视频', '站台视频.mp4', 'video/mp4');
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  await page.getByRole('checkbox', { name: '视频只理解画面（忽略声音）', exact: true }).check();
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect.poll(() => data.state.sends.length).toBe(1);
  expect(data.state.sends[0].body).toMatchObject({ expected_scope: { stage: 'source', subject_type: 'episode', subject_id: '20', task_type: 'writing' }, attachment_ids: ['7001'], video_audio: 'visual_only' });
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('restoring pending attachments and retrying their load keep sending blocked', async ({ page }) => {
  const data = await contextFixture(page); const gate = deferred();
  data.state.attachmentGate = gate; data.state.failLoad = true;
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  const input = page.getByRole('textbox', { name: '创作要求', exact: true });
  await input.fill('继续讨论已有附件。');
  try { await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled(); }
  finally { gate.resolve(); }
  await expect(page.getByRole('button', { name: '重新载入附件', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled();
  data.state.failLoad = false; data.state.attachmentGate = null;
  await page.getByRole('button', { name: '重新载入附件', exact: true }).click();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  await expect(input).toHaveValue('继续讨论已有附件。');
  expect(data.state.sends).toEqual([]); expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('uncertain attachment uploads require explicit reconciliation with the same key', async ({ page }) => {
  const data = await contextFixture(page); data.state.failUpload = true;
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await page.getByRole('textbox', { name: '创作要求', exact: true }).fill('先核对上传，再讨论。');
  await upload(page, '文本', '雨夜资料.txt', 'text/plain');
  const retry = page.getByRole('button', { name: '使用原请求核对上传', exact: true });
  await expect(retry).toBeVisible();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled();
  expect(data.state.uploads).toHaveLength(1); expect(data.state.sends).toEqual([]);
  data.state.failUpload = false; await retry.click();
  await expect(retry).toHaveCount(0);
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  expect(data.state.uploads).toHaveLength(2);
  expect(data.state.uploads[1].key).toBe(data.state.uploads[0].key);
  await page.getByRole('button', { name: '移除附件雨夜资料.txt', exact: true }).click();
  await expect(page.locator('.agent-attached-items')).toHaveCount(0);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('model choice makes no capability declaration or provider request', async ({ page }) => {
  const data = await contextFixture(page);
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await chooseModel(page, '视觉协作模型');
  expect(data.state.modelUpdates).toEqual([]);
  expect(data.state.sends).toEqual([]);
  await page.getByRole('textbox', { name: '创作要求' }).fill('根据当前人物生成参考图。');
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: '选择发送用途' })).toHaveCount(0);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('an accepted attachment send keeps its original request after reload and preserves a separate draft', async ({ page }) => {
  const data = await contextFixture(page);
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await chooseModel(page, '视觉协作模型');
  data.state.uploadKind = 'video'; await upload(page, '视频', '站台视频.mp4', 'video/mp4');
  await page.getByRole('checkbox', { name: '视频只理解画面（忽略声音）', exact: true }).check();
  await page.getByRole('button', { name: '加载 Skill', exact: true }).click();
  const skills = page.getByRole('dialog', { name: '加载 Skill', exact: true });
  await skills.getByRole('checkbox', { name: '人物连续性', exact: true }).check();
  await skills.getByRole('button', { name: '完成', exact: true }).click();
  const input = page.getByRole('textbox', { name: '创作要求', exact: true });
  await input.fill('按原视频和人物规范生成候选。');
  data.state.abortAcceptedSend = true;
  await page.getByRole('button', { name: '发送', exact: true }).click();
  const retry = page.getByRole('button', { name: '使用原请求重试', exact: true });
  await expect(retry).toBeVisible();
  const original = structuredClone(data.state.sends[0]);
  expect(original.body).toMatchObject({ attachment_ids: ['7001'], video_audio: 'visual_only', skills: [{ id: 'builtin:continuity', content_version: '1' }] });
  expect(data.state.messages).toHaveLength(1); expect(data.state.attachments[0].pending).toBe(false);
  await input.fill('之后写的新草稿，等待原请求核对。');
  data.state.skills[0].content_version = '2'; data.state.skills[0].enabled = false;
  await page.reload();
  await expect(input).toHaveValue('之后写的新草稿，等待原请求核对。');
  await expect(retry).toBeVisible();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled();
  expect(data.state.sends).toHaveLength(1);
  await retry.click();
  await expect(retry).toHaveCount(0);
  expect(data.state.sends).toHaveLength(2); expect(data.state.sends[1]).toEqual(original);
  expect(data.state.messages).toHaveLength(1);
  await expect(input).toHaveValue('之后写的新草稿，等待原请求核对。');
  await page.reload();
  await expect(retry).toHaveCount(0);
  await expect(input).toHaveValue('之后写的新草稿，等待原请求核对。');
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  expect(data.state.sends).toHaveLength(2);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('a send stays local when its pending recovery record cannot be saved', async ({ page }) => {
  const data = await contextFixture(page);
  await page.addInitScript(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.includes(':agent-pending-send:')) throw new Error('storage quota');
      return original.call(this, key, value);
    };
  });
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  const input = page.getByRole('textbox', { name: '创作要求', exact: true });
  await input.fill('不能保存恢复记录时不要发送。');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: '无法保存待核对的发送记录' })).toBeVisible();
  await expect(input).toHaveValue('不能保存恢复记录时不要发送。');
  expect(data.state.sends).toEqual([]);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('a refreshed draft restores its model and validated Skill reference without sending', async ({ page }) => {
  const data = await contextFixture(page);
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await chooseModel(page, '视觉协作模型');
  await page.getByRole('button', { name: '加载 Skill', exact: true }).click();
  const skills = page.getByRole('dialog', { name: '加载 Skill', exact: true });
  await skills.getByRole('checkbox', { name: '人物连续性', exact: true }).check();
  await skills.getByRole('button', { name: '完成', exact: true }).click();
  const input = page.getByRole('textbox', { name: '创作要求', exact: true });
  await input.fill('先保留这份尚未发送的连续性草稿'); await page.reload();
  await expect(input).toHaveValue('先保留这份尚未发送的连续性草稿');
  await expect(page.locator('.agent-loaded-skills')).toContainText('人物连续性 v1');
  await page.getByRole('button', { name: '选择模型', exact: true }).click();
  const models = page.getByRole('dialog', { name: '选择 Agent 模型', exact: true });
  await expect(models.locator('.ant-select-selection-item')).toContainText('视觉协作模型');
  await models.getByRole('button', { name: '完成', exact: true }).click();
  data.state.skills[0].content_version = '2'; await page.reload();
  await expect(input).toHaveValue('先保留这份尚未发送的连续性草稿');
  await expect(page.getByRole('alert').filter({ hasText: '部分 Skill 已更新、停用或删除' })).toBeVisible();
  await expect(page.locator('.agent-loaded-skills')).toHaveCount(0);
  expect(data.state.sends).toEqual([]); expect(data.state.modelUpdates).toEqual([]);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('personal Skill conflicts preserve edits and selected skills follow the saved version', async ({ page }) => {
  const data = await contextFixture(page);
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await page.getByRole('button', { name: '加载 Skill', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '加载 Skill', exact: true });
  await dialog.getByText('我的技能', { exact: true }).click();
  const chooser = page.waitForEvent('filechooser');
  await dialog.getByRole('button', { name: '上传 Skill', exact: true }).click();
  await (await chooser).setFiles({ name: 'rain.md', mimeType: 'text/markdown', buffer: Buffer.from('保留站台对白。') });
  await dialog.getByRole('checkbox', { name: '雨夜创作规范', exact: true }).check();
  await dialog.getByRole('button', { name: '编辑', exact: true }).click();
  await dialog.getByRole('textbox', { name: 'Markdown 指令', exact: true }).fill('保留站台对白与雨夜人物动机。');
  await dialog.getByRole('button', { name: '完成', exact: true }).click();
  const discard = page.getByRole('dialog', { name: '未保存的 Skill', exact: true });
  await expect(discard).toBeVisible();
  await discard.getByRole('button', { name: '取消', exact: true }).click();
  await expect(dialog.getByRole('textbox', { name: 'Markdown 指令', exact: true })).toHaveValue('保留站台对白与雨夜人物动机。');
  data.state.failSkill = true;
  await dialog.getByRole('button', { name: '保存新版本', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('数据已被修改');
  await expect(dialog.getByRole('textbox', { name: 'Markdown 指令', exact: true })).toHaveValue('保留站台对白与雨夜人物动机。');
  data.state.failSkill = false;
  await dialog.getByRole('button', { name: '保存新版本', exact: true }).click();
  await expect(dialog.getByRole('textbox', { name: 'Markdown 指令', exact: true })).toHaveCount(0);
  expect(data.state.skillUpdates).toEqual([
    { row_version: '1', name: '雨夜创作规范', instructions: '保留站台对白与雨夜人物动机。' },
    { row_version: '1', name: '雨夜创作规范', instructions: '保留站台对白与雨夜人物动机。' },
  ]);
  await dialog.getByRole('button', { name: '完成', exact: true }).click();
  await expect(page.locator('.agent-loaded-skills')).toContainText('雨夜创作规范 v2');
  await page.getByRole('textbox', { name: '创作要求', exact: true }).fill('依据雨夜规范讨论人物。');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect.poll(() => data.state.sends.length).toBe(1);
  expect(data.state.sends[0].body.skills).toEqual([{ id: '980', content_version: '2' }]);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('compact Agent dialogue keeps its conversation and input tools usable at review viewports', async ({ page }) => {
  const data = await contextFixture(page);
  data.state.attachments.push(data.attachment('image', '角色参考.png'));
  data.state.attachments.push(data.attachment('text', '雨夜资料.txt'));
  data.state.messages.push(
    { id: '2001', seq: 1, role: 'user', content: '让林晚在雨夜站台认出信的主人，保留人物动机与外貌。', references: [{ type: 'skill', id: 'builtin:continuity', name: '人物连续性', content_version: '1' }], artifacts: [], created_at: time },
    { id: '2002', seq: 2, role: 'assistant', content: '可以把认出信主人的瞬间放在列车灯光扫过站台时。\n\n林晚先看见熟悉的手写字迹，再抬头确认对方的目光。先保留她的迟疑，让这次相认推动下一场对话。', references: [], artifacts: [], created_at: time },
  );
  await page.goto(`${root}/source?mode=agent&conversation=301`);
  await chooseModel(page, '视觉协作模型');
  await page.getByRole('button', { name: '加载 Skill', exact: true }).click();
  const skills = page.getByRole('dialog', { name: '加载 Skill', exact: true });
  await skills.getByRole('checkbox', { name: '人物连续性', exact: true }).check();
  await skills.getByRole('button', { name: '完成', exact: true }).click();
  await page.getByRole('textbox', { name: '创作要求', exact: true }).fill('保留雨夜氛围，进一步细化相认时的眼神和动作。');
  for (const viewport of refinementViewports) {
    await page.setViewportSize(viewport);
    const aiTab = page.getByRole('tab', { name: 'AI 创作', exact: true });
    if (await aiTab.isVisible()) await aiTab.click();
    await expect(page.locator('.agent-transcript')).toContainText('可以把认出信主人的瞬间');
    await expect(page.locator('.agent-attached-items')).toContainText('角色参考.png');
    await expect(page.locator('.agent-loaded-skills')).toContainText('人物连续性');
    await page.getByRole('button', { name: '发送', exact: true }).scrollIntoViewIfNeeded();
    await expect(page.getByRole('button', { name: '发送', exact: true })).toBeInViewport();
    await captureRefinement(page, 'agent-dialogue', viewport.width);
  }
  expect(data.state.sends).toEqual([]);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});
