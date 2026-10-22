import { expect, test, type Locator, type Page } from '@playwright/test';
import { fixture, root } from '../support/studio-fixture';

async function openSaveDetails(page: Page) {
  const entry = page.getByRole('button', { name: /^保存状态：/ });
  if (await entry.getAttribute('aria-expanded') !== 'true') await entry.click();
  return page.locator('.standard-save-details');
}

async function richFixture(page: Page) {
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  data.writing.editing_script.content = '第一场 雨夜\n林晚：信是谁寄来的？\n\n第二场 车站\n周野：你终于来了。';
  let draft: any = { id: '801', row_version: '1', draft_version: '1', source_version: '1', project_id: '10', episode_id: '20', object_id: '20', purpose: 'novel_adaptation', model_config_id: '77', input_text: '', image_settings: null, video_settings: null, reference_media_ids: [] };
  const scriptWrites: any[] = [], draftWrites: any[] = [], submissions: any[] = [], lookups: string[] = [];
  const controls = { loseSubmit: false, lookupReceipt: null as any, conflictScript: false };
  await page.route(`**/api/v1${root}/drafts**`, async route => {
    const body = route.request().postDataJSON();
    if (route.request().method() !== 'GET') {
      draftWrites.push(body); draft = { ...draft, ...body, row_version: String(BigInt(draft.row_version) + 1n), draft_version: String(BigInt(draft.draft_version) + 1n) };
    }
    return route.fulfill({ json: draft });
  });
  await page.route(`**/api/v1${root}/script`, async route => {
    if (controls.conflictScript) return route.fulfill({ status: 409, json: { error: { code: 'VERSION_CONFLICT', message: '正文版本冲突' } } });
    const body = route.request().postDataJSON(); scriptWrites.push(body);
    Object.assign(data.writing.editing_script, { content: body.content, editor_document: body.editor_document ?? null, state: 'unconfirmed' });
    data.writing.confirmed_script_id = null; data.writing.content_version = String(BigInt(data.writing.content_version) + 1n);
    return route.fulfill({ json: { content_version: data.writing.content_version, script: data.writing.editing_script } });
  });
  await page.route('**/api/v1/ai/generations/text', async route => {
    submissions.push({ body: route.request().postDataJSON(), key: route.request().headers()['idempotency-key'] });
    if (controls.loseSubmit) return route.abort();
    return route.fulfill({ json: { generation_id: '99001', service_type: 'text', status: 'queued' } });
  });
  await page.route('**/api/v1/ai/generations/by-idempotency-key**', async route => {
    lookups.push(new URL(route.request().url()).searchParams.get('idempotency_key')!);
    return route.fulfill({ json: controls.lookupReceipt });
  });
  return { ...data, scriptWrites, draftWrites, submissions, lookups, controls, get draft() { return draft; } };
}

test('source and synopsis feedback stays outside the editing layout without starting generation', async ({ page }) => {
  const data = await richFixture(page);
  data.writing.editing_script.content = '';
  data.writing.confirmed_script_id = null;
  await page.goto(`${root}/source`);
  const empty = page.getByRole('region', { name: '开始创作本集剧本', exact: true });
  const emptyBox = await empty.boundingBox();
  await empty.getByRole('button', { name: /从小说改编/ }).click();
  await expect(page.locator('.studio-message').filter({ hasText: '小说原文已准备好' })).toBeVisible();
  expect(await empty.boundingBox()).toEqual(emptyBox);
  await page.screenshot({ path: test.info().outputPath('writing-empty-1440.png') });
  await empty.getByRole('button', { name: /从梗概创作/ }).click();
  const synopsis = page.getByRole('region', { name: '从梗概创作', exact: true });
  await synopsis.getByRole('textbox', { name: '故事梗概', exact: true }).fill('林晚在车站收到旧信，决定寻找寄信的人。');
  const synopsisBox = await synopsis.boundingBox();
  await synopsis.getByRole('button', { name: '保存梗概', exact: true }).click();
  await expect(page.locator('.studio-message').filter({ hasText: '当前 AI 生成仅支持小说来源' })).toBeVisible();
  expect(await synopsis.boundingBox()).toEqual(synopsisBox);
  await expect.poll(() => data.draftWrites.length).toBeGreaterThan(0);
  expect(data.submissions).toHaveLength(0);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('script import keeps pasted text and shows file errors inside its dialog', async ({ page }) => {
  const data = await richFixture(page);
  data.writing.editing_script.content = '';
  data.writing.confirmed_script_id = null;
  await page.goto(`${root}/source`);
  await page.getByRole('button', { name: /导入已有剧本/ }).click();
  const dialog = page.getByRole('dialog', { name: '导入已有剧本', exact: true });
  const text = dialog.getByRole('textbox', { name: '剧本文本', exact: true });
  await text.fill('已粘贴的剧本必须保留。');
  await dialog.locator('input[type=file]').setInputFiles({ name: 'script.csv', mimeType: 'text/csv', buffer: Buffer.from('invalid') });
  await expect(dialog.getByRole('alert')).toContainText('请选择 .txt 文件');
  await expect(text).toHaveValue('已粘贴的剧本必须保留。');
  await page.screenshot({ path: test.info().outputPath('writing-import-1440.png') });
  await page.setViewportSize({ width: 320, height: 640 });
  await expect(dialog.getByRole('button', { name: '预览导入内容', exact: true })).toBeInViewport({ ratio: 1 });
  await expect(dialog.getByRole('alert')).toBeInViewport({ ratio: 1 });
  expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath('writing-import-320.png') });
  expect(data.scriptWrites).toHaveLength(0);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('script import blocks duplicate actions while reading and only opens a read-only preview', async ({ page }) => {
  const data = await richFixture(page);
  data.writing.editing_script.content = '';
  data.writing.confirmed_script_id = null;
  await page.addInitScript(() => {
    const read = File.prototype.arrayBuffer;
    File.prototype.arrayBuffer = async function () {
      await new Promise<void>(resolve => { (window as any).finishScriptRead = resolve; });
      return read.call(this);
    };
  });
  await page.goto(`${root}/source`);
  await page.getByRole('button', { name: /导入已有剧本/ }).click();
  const dialog = page.getByRole('dialog', { name: '导入已有剧本', exact: true });
  const text = dialog.getByRole('textbox', { name: '剧本文本', exact: true });
  await text.fill('读取前的粘贴内容。');
  await dialog.locator('input[type=file]').setInputFiles({ name: 'script.txt', mimeType: 'text/plain', buffer: Buffer.from('读取完成后的新剧本。') });
  await expect(dialog.getByRole('button', { name: /读取中/ })).toBeDisabled();
  await expect(dialog.getByRole('button', { name: '预览导入内容', exact: true })).toBeDisabled();
  await expect(dialog.getByRole('button', { name: '关闭弹窗', exact: true })).toBeDisabled();
  await expect(text).toBeDisabled();
  await page.keyboard.press('Escape'); await expect(dialog).toBeVisible();
  await page.evaluate(() => (window as any).finishScriptRead());
  await expect(text).toBeEnabled(); await expect(text).toHaveValue('读取完成后的新剧本。');
  await dialog.getByRole('button', { name: '预览导入内容', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  const preview = page.getByRole('textbox', { name: '新剧本结果预览', exact: true });
  await expect(preview).toHaveAttribute('contenteditable', 'false');
  await expect(preview).toContainText('读取完成后的新剧本。');
  expect(data.scriptWrites).toHaveLength(0); expect(data.writing.editing_script.content).toBe('');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('Quill format-only edits save one version, survive refresh, and retain undo and redo', async ({ page }) => {
  const data = await richFixture(page); await page.goto(`${root}/source`);
  const editor = page.getByRole('textbox', { name: '本集剧本正文', exact: true });
  await expect(editor).toBeEditable();
  await expect(page.getByRole('button', { name: '确认当前剧本', exact: true })).toHaveCount(0);
  await editor.click(); await page.keyboard.press('Control+A');
  await page.getByRole('button', { name: '加粗', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认当前剧本', exact: true })).toHaveCount(0);
  await expect.poll(() => data.scriptWrites.length).toBe(1);
  const saved = data.scriptWrites[0];
  expect(saved.content).toBe(data.writing.editing_script.content);
  expect(saved.content_version).toBe('1'); expect(saved.editor_document.ops.some((op: any) => op.attributes?.bold)).toBe(true);
  expect(saved.editor_document.ops.map((op: any) => op.insert).join('').slice(0, -1)).toBe(saved.content);
  await expect(page.getByRole('button', { name: '撤销编辑', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '撤销编辑', exact: true }).click();
  await expect(editor.locator('strong')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '重做编辑', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '重做编辑', exact: true }).click();
  await expect(editor.locator('strong').first()).toBeVisible();
  await page.reload(); await expect(editor).toBeEditable();
  await expect(editor.locator('strong').first()).toBeVisible();
  await expect(page.locator('.novel-script-toolbar').getByText('2 个场次', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '定位到第二场 车站', exact: true }).click();
  expect(await editor.evaluate(node => getComputedStyle(node).outlineStyle)).toBe('none');
  expect(data.errors).toEqual([]);
});

test('moving from Quill to requirements, model and assistant retains focus without adding focus decoration', async ({ page }) => {
  const data = await richFixture(page);
  const time = '2026-10-13T00:00:00Z';
  const conversation = { id: '301', project_id: '10', episode_id: null, title: '创作讨论', scope_version: 2, stage: null, subject_type: null, subject_id: null, task_type: null, row_version: '1', archived: false, last_run_status: null, created_at: time, updated_at: time };
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: '9007199254740993', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true } } }));
  await page.route('**/api/v1/agent/status', route => route.fulfill({ json: { enabled: true, schema_ready: true } }));
  await page.route('**/api/v1/agent/models', route => route.fulfill({ json: { items: [{ id: '71', name: '创作讨论模型', model_key: 'fixture', row_version: '1', protocol: 'chat', tool_calling: false, tool_result_continuation: false, streaming: 'not_tested', verified: false, preferred: true }], preferred_id: '71' } }));
  await page.route('**/api/v1/agent/skills**', route => route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 50 } }));
  await page.route('**/api/v1/assistant/**', route => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path === '/api/v1/assistant/conversations/resolve') return route.fulfill({ json: conversation });
    if (request.method() !== 'GET') return route.fallback();
    if (path === '/api/v1/assistant/conversations/301') return route.fulfill({ json: conversation });
    if (path === '/api/v1/assistant/conversations') return route.fulfill({ json: { items: [conversation], total: 1, offset: 0, limit: 20 } });
    if (path === '/api/v1/assistant/conversations/301/events') return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (path === '/api/v1/assistant/conversations/301/state') return route.fulfill({ json: { conversation_id: '301', cursor: 0, resume_cursor: 0, active_run: null, queued_runs: [] } });
    if (path === '/api/v1/assistant/conversations/301/runs') return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 1 } });
    if (['/api/v1/assistant/conversations/301/messages', '/api/v1/assistant/conversations/301/attachments'].includes(path)) return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 50 } });
    return route.fallback();
  });
  await page.goto(`${root}/source`);
  const editor = page.getByRole('textbox', { name: '本集剧本正文', exact: true });
  const prompt = page.getByRole('textbox', { name: '创作要求', exact: true });
  const model = page.getByRole('combobox', { name: '文本模型', exact: true });
  const composer = page.locator('.novel-adaptation-settings .generation-composer');
  const modelSurface = composer.locator('.ant-select-selector');
  const appearance = (target: Locator) => target.evaluate(node => {
    const style = getComputedStyle(node);
    return { outline: style.outlineStyle, shadow: style.boxShadow, borderWidth: style.borderWidth, borderStyle: style.borderStyle, border: style.borderWidth === '0px' ? null : style.borderColor };
  });
  await expect(editor).toBeEditable();
  await expect(prompt).toBeEnabled(); await expect(model).toBeEnabled();
  const original = data.writing.editing_script.content;
  const editorBefore = await appearance(editor), promptBefore = await appearance(prompt);
  const composerBefore = await appearance(composer), modelBefore = await appearance(modelSurface);
  await editor.click(); await expect(editor).toBeFocused();
  expect(await appearance(editor)).toEqual(editorBefore);
  expect(editorBefore.outline).toBe('none'); expect(editorBefore.shadow).toBe('none');
  await prompt.click(); await expect(prompt).toBeFocused();
  await page.keyboard.insertText('保持改编要求输入在自己的区域');
  await expect(prompt).toHaveValue('保持改编要求输入在自己的区域');
  expect(await appearance(prompt)).toEqual(promptBefore); expect(await appearance(composer)).toEqual(composerBefore);
  expect(promptBefore.outline).toBe('none'); expect(promptBefore.shadow).toBe('none');
  await modelSurface.click(); await expect(model).toBeFocused();
  expect(await appearance(modelSurface)).toEqual(modelBefore);
  expect(modelBefore.outline).toBe('none'); expect(modelBefore.shadow).toBe('none');
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').first().click();
  await expect(modelSurface.locator('.ant-select-selection-item')).toHaveText(/.+/);
  await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
  const assistant = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await assistant.click(); await expect(assistant).toBeFocused();
  await page.keyboard.insertText('保持助手讨论在自己的区域');
  await expect(assistant).toHaveValue('保持助手讨论在自己的区域');
  await expect(editor).toHaveText(original, { useInnerText: true });
  expect(data.writing.editing_script.content).toBe(original); expect(data.scriptWrites).toHaveLength(0);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('composition waits for completed Chinese text and StrictMode does not duplicate editors or saves', async ({ page }) => {
  const data = await richFixture(page); await page.goto(`${root}/source`);
  const editor = page.getByRole('textbox', { name: '本集剧本正文', exact: true }); await expect(editor).toBeEditable();
  await editor.dispatchEvent('compositionstart', { data: '' });
  await editor.fill('中文输入法组合期间的正文');
  await page.waitForTimeout(1200); expect(data.scriptWrites).toHaveLength(0);
  await editor.dispatchEvent('compositionend', { data: '中文输入法组合期间的正文' });
  await expect.poll(() => data.scriptWrites.length).toBe(1);
  expect(data.scriptWrites[0].content).toBe('中文输入法组合期间的正文');
  expect(await page.locator('.novel-current-document .ql-editor').count()).toBe(1);
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  const original = page.getByRole('textbox', { name: '本集小说正文', exact: true });
  await original.fill('原文抽屉修改后仍保留输入');
  await page.getByRole('button', { name: '返回剧本', exact: true }).click();
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  await expect(original).toHaveValue('原文抽屉修改后仍保留输入');
  expect(data.errors).toEqual([]);
});

test('candidate preview stays read-only and one adoption action opens editing', async ({ page }) => {
  const data = await richFixture(page); await page.goto(`${root}/source`);
  const original = data.writing.editing_script.content;
  await page.getByRole('button', { name: '生成记录', exact: true }).click();
  await page.getByRole('button', { name: '预览全文', exact: true }).click();
  const preview = page.getByRole('textbox', { name: '新剧本结果预览', exact: true });
  await expect(preview).toHaveAttribute('contenteditable', 'false');
  await expect(page.getByRole('dialog', { name: '候选剧本预览', exact: true })).toHaveCount(0);
  expect(data.writing.editing_script.content).toBe(original);
  await page.getByRole('button', { name: '采用并编辑', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '确认操作', exact: true })).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: '本集剧本正文', exact: true })).toContainText('候选剧本');
  await expect(page.getByRole('button', { name: '确认当前剧本', exact: true })).toHaveCount(0);
  expect(data.writing.editing_script.state).toBe('unconfirmed'); expect(data.errors).toEqual([]);
});

test('a failed script history preview stays inside its dialog without shrinking the editor', async ({ page }) => {
  const data = await richFixture(page);
  await page.route(`**/api/v1${root}/scripts/*`, route => route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE' } } }));
  await page.goto(`${root}/source`);
  const editor = page.getByRole('textbox', { name: '本集剧本正文', exact: true });
  await expect(editor).toBeEditable();
  const editorBox = await editor.boundingBox();
  await page.getByRole('button', { name: '生成记录', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '剧本生成记录', exact: true });
  await dialog.getByRole('button', { name: '预览全文', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('服务暂时不可用');
  expect(await editor.boundingBox()).toEqual(editorBox);
  await expect(page.locator('.novel-script-panel').getByRole('alert')).toHaveCount(0);
  expect(data.scriptWrites).toHaveLength(0);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('unknown generation survives refresh and lookup reuses the original key without another POST', async ({ page }) => {
  const data = await richFixture(page); data.controls.loseSubmit = true;
  await page.goto(`${root}/source`); const prompt = page.getByRole('textbox', { name: '创作要求', exact: true });
  await prompt.fill('让第一场冲突更清晰');
  await page.getByRole('button', { name: '生成剧本', exact: true }).click();
  await expect.poll(() => data.submissions.length).toBe(1);
  expect(data.submissions[0].body.source.draft_id).toBe('801');
  expect(data.submissions[0].body.source.draft_version).toBe(data.draft.draft_version);
  await openSaveDetails(page);
  await expect(page.getByRole('button', { name: '核对生成请求', exact: true })).toBeVisible();
  await page.reload(); await expect(prompt).toHaveValue('让第一场冲突更清晰');
  await openSaveDetails(page);
  await page.getByRole('button', { name: '核对生成请求', exact: true }).click();
  await expect.poll(() => data.lookups.length).toBe(1); expect(data.lookups[0]).toBe(data.submissions[0].key);
  expect(data.submissions).toHaveLength(1); await expect(page.getByRole('button', { name: '生成剧本', exact: true })).toBeDisabled();
  data.controls.lookupReceipt = { generation_id: '99001', service_type: 'text', status: 'queued' };
  await page.getByRole('button', { name: '核对生成请求', exact: true }).click();
  await expect(page.getByRole('button', { name: '核对生成请求', exact: true })).toHaveCount(0);
  expect(data.submissions).toHaveLength(1); expect(data.errors).toEqual([]);
});

test('completed generation previews the matching candidate automatically without changing the current script', async ({ page }) => {
  const data = await richFixture(page);
  data.draft.input_text = '保留主线，突出本集冲突';
  let ready = false;
  await page.route('**/api/v1/ai/generations?**', async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('source_scene') !== 'novel_script') return route.fallback();
    const items = data.submissions.length ? [{ generation_id: '99001', service_type: 'text', status: ready ? 'succeeded' : 'queued', created_at: '2026-10-13T00:00:00Z', can_cancel: false, can_retry: false, can_resume: false }] : [];
    return route.fulfill({ json: { items, total: items.length, offset: 0, limit: 100 } });
  });
  await page.route(`**/api/v1${root}/scripts?**`, async route => {
    if (!ready) return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 100 } });
    return route.fulfill({ json: { items: [{ id: '41', generation_id: '99001', position: 2, state: 'unconfirmed', preview: '新剧本', is_editing: false, is_confirmed: false, created_at: '2026-10-13T00:00:00Z', updated_at: '2026-10-13T00:00:00Z' }], total: 1, offset: 0, limit: 100 } });
  });
  await page.goto(`${root}/source`);
  const before = data.writing.editing_script.content;
  await page.getByRole('button', { name: '生成剧本', exact: true }).click();
  await expect.poll(() => data.submissions.length).toBe(1); ready = true;
  await expect(page.getByRole('textbox', { name: '新剧本结果预览', exact: true })).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole('button', { name: '采用并编辑', exact: true })).toBeVisible();
  expect(data.writing.editing_script.content).toBe(before); expect(data.scriptWrites).toHaveLength(0); expect(data.errors).toEqual([]);
});

test('mobile settings and original drawers preserve inputs without horizontal overflow', async ({ page }) => {
  const data = await richFixture(page); await page.setViewportSize({ width: 390, height: 844 }); await page.goto(`${root}/source`);
  const editor = page.getByRole('textbox', { name: '本集剧本正文', exact: true }); await expect(editor).toBeEditable();
  await page.screenshot({ path: test.info().outputPath('novel-quill-390.png') });
  await page.getByRole('button', { name: '展开设置', exact: true }).click();
  const prompt = page.getByRole('textbox', { name: '创作要求', exact: true }); await expect(prompt).toBeVisible(); await prompt.fill('手机输入的改编要求');
  expect(await prompt.evaluate(node => getComputedStyle(node).height)).toBe('180px');
  await page.keyboard.press('Escape'); await expect(prompt).not.toBeVisible();
  await page.getByRole('button', { name: '展开设置', exact: true }).click();
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  const original = page.getByRole('dialog', { name: '本集小说原文', exact: true });
  await expect(original).toBeVisible();
  await expect.poll(async () => {
    const box = (await original.boundingBox())!;
    return { x: Math.round(box.x), y: Math.round(box.y), width: Math.round(box.width), height: Math.round(box.height) };
  }).toEqual({ x: 0, y: 0, width: 390, height: 844 });
  await page.screenshot({ path: test.info().outputPath('writing-original-390.png') });
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: '展开设置', exact: true })).toBeFocused();
  await page.getByRole('button', { name: '展开设置', exact: true }).click(); await expect(prompt).toHaveValue('手机输入的改编要求');
  const model = (await page.locator('.novel-text-model').boundingBox())!;
  const send = (await page.getByRole('button', { name: '生成剧本', exact: true }).boundingBox())!;
  expect(model.width).toBe(144); expect(model.height).toBe(44);
  expect(send.width).toBe(44); expect(send.height).toBe(44);
  expect(Math.abs(model.y - send.y)).toBeLessThanOrEqual(1);
  expect(model.x + model.width).toBeLessThanOrEqual(send.x);
  await page.screenshot({ path: test.info().outputPath('writing-settings-390.png') });
  await page.setViewportSize({ width: 320, height: 844 });
  const narrowModel = (await page.locator('.novel-text-model').boundingBox())!;
  const narrowSend = (await page.getByRole('button', { name: '生成剧本', exact: true }).boundingBox())!;
  expect(narrowModel.width).toBe(144); expect(narrowModel.height).toBe(44);
  expect(narrowSend.width).toBe(44); expect(narrowSend.height).toBe(44);
  expect(Math.abs(narrowModel.y - narrowSend.y)).toBeLessThanOrEqual(1);
  expect(narrowModel.x + narrowModel.width).toBeLessThanOrEqual(narrowSend.x);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  await page.screenshot({ path: test.info().outputPath('writing-settings-320.png') });
  expect(data.errors).toEqual([]);
});

test('compact text models and the novel source support keyboard selection and original editing', async ({ page }) => {
  const data = await richFixture(page);
  data.writing.novel.content = '';
  await page.route(/\/api\/v1\/ai-model-configs(?:\?.*)?$/, route => route.fulfill({ json: {
    items: ['77', '78'].map((id, index) => ({ id, name: index ? '备用文本模型' : '本地验收模型', model_key: index ? 'fixture-backup-text' : 'fixture-text', service_type: 'text', provider: '测试替身', base_url: 'https://fixture.invalid/v1', enabled: 1, is_default: index ? 0 : 1, is_deleted: 0, row_version: '1', has_api_key: true })),
    total: 2, offset: 0, limit: 100,
  } }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${root}/source`);
  const settings = page.locator('.novel-adaptation-settings');
  await expect(settings.locator(':scope > header').getByRole('button', { name: '查看原文', exact: true })).toHaveCount(0);
  const originalEntry = settings.getByRole('button', { name: '查看原文', exact: true });
  await expect(originalEntry).toContainText('小说原文 · 0 字');
  await expect(originalEntry).toHaveAttribute('aria-haspopup', 'dialog');
  await originalEntry.focus();
  await originalEntry.press('Enter');
  const original = page.getByRole('dialog', { name: '本集小说原文', exact: true });
  await expect(original.getByRole('button', { name: '导入小说', exact: true })).toBeVisible();
  const novel = original.getByRole('textbox', { name: '本集小说正文', exact: true });
  await expect(novel).toHaveValue('');
  await novel.fill('通过来源素材入口添加的小说原文');
  await expect.poll(() => data.writing.novel.content).toBe('通过来源素材入口添加的小说原文');
  await page.keyboard.press('Escape');
  await expect(originalEntry).toBeFocused();
  await originalEntry.press('Space');
  await expect(novel).toHaveValue('通过来源素材入口添加的小说原文');
  await page.keyboard.press('Escape');

  const model = settings.getByRole('combobox', { name: '文本模型', exact: true });
  const trigger = settings.locator('.novel-text-model');
  await expect(model).toBeEnabled();
  await expect(trigger.locator('.ant-select-selection-item')).toHaveText('本地验收模型');
  expect((await trigger.boundingBox())!.width).toBe(144);
  await expect(trigger.locator(':scope > svg, .ant-select-arrow, .ant-select-clear')).toHaveCount(0);
  await trigger.locator('.ant-select-selector').click();
  const menu = page.locator('.creation-model-popup:visible');
  await expect(menu.locator('.ant-select-item-option')).toHaveCount(2);
  expect((await menu.boundingBox())!.width).toBe(240);
  await model.press('ArrowDown');
  await model.press('Enter');
  await expect(trigger.locator('.ant-select-selection-item')).toContainText('备用文本模型');
  await expect.poll(() => data.draft.model_config_id).toBe('78');

  expect(data.submissions).toHaveLength(0); expect(data.scriptWrites).toHaveLength(0);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('a delayed generation receipt cannot clear another account attempt or replace its generation state', async ({ page }) => {
  const data = await richFixture(page);
  data.draft.input_text = '保留主线，突出本集冲突';
  const account = (id: string) => ({ id, username: `writer-${id}`, display_name: `创作者 ${id}`, email: `writer-${id}@example.test`, email_verified: true });
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: account('101') } }));
  await page.route('**/api/v1/auth/login', route => route.fulfill({ json: { user: account('102') } }));
  let submitted = false, release = () => {};
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/ai/generations/text', async route => { submitted = true; await held; await route.fallback(); });
  await page.goto(`${root}/source`);
  const nextScope = 'generation-attempt:user:102:novel-script:10:20';
  const previousScope = 'creation-recovery:user:101:novel-adaptation-attempt:10:20';
  await page.evaluate(key => sessionStorage.setItem(key, 'other-account-attempt'), nextScope);
  await page.getByRole('button', { name: '生成剧本', exact: true }).click();
  await expect.poll(() => submitted).toBe(true);
  await page.evaluate(() => window.dispatchEvent(new Event('session-expired')));
  const login = page.getByRole('dialog', { name: '重新登录，继续创作', exact: true });
  await login.getByLabel('密码', { exact: true }).fill('fixture-password');
  const loginResponse = page.waitForResponse('**/api/v1/auth/login');
  await login.getByRole('button', { name: '登录并返回编辑', exact: true }).click();
  expect((await (await loginResponse).json()).user.id).toBe('102');
  await expect(login).not.toBeVisible();
  release();
  await expect.poll(() => data.submissions.length).toBe(1);
  await expect(page.getByRole('button', { name: '生成剧本', exact: true })).toBeEnabled();
  expect(await page.evaluate(key => sessionStorage.getItem(key), nextScope)).toBe('other-account-attempt');
  expect(await page.evaluate(key => localStorage.getItem(key), previousScope)).not.toBeNull();
  expect(data.errors).toEqual([]);
});

test('rich paste preserves supported formats and blank lines while TXT import and export keep plain text', async ({ page }) => {
  const data = await richFixture(page); await page.goto(`${root}/source`);
  const editor = page.getByRole('textbox', { name: '本集剧本正文', exact: true }); await expect(editor).toBeEditable();
  await editor.click(); await page.keyboard.press('Control+A');
  await editor.evaluate(node => {
    const clipboardData = new DataTransfer();
    clipboardData.setData('text/html', '<h2>第一场 雨夜</h2><p><strong>保留加粗</strong>与<em>斜体</em></p><p><br></p><blockquote>旧信里的台词</blockquote><p><br></p><p>尾声</p>');
    clipboardData.setData('text/plain', '第一场 雨夜\n保留加粗与斜体\n\n旧信里的台词\n\n尾声');
    node.dispatchEvent(new ClipboardEvent('paste', { clipboardData, bubbles: true, cancelable: true }));
  });
  await expect.poll(() => data.scriptWrites.length).toBe(1);
  const saved = data.scriptWrites[0];
  expect(saved.content).toBe('第一场 雨夜\n保留加粗与斜体\n\n旧信里的台词\n\n尾声');
  for (const format of ['bold', 'italic', 'blockquote', 'header']) expect(saved.editor_document.ops.some((op: any) => op.attributes?.[format])).toBe(true);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出', exact: true }).click();
  const exported = await download, stream = await exported.createReadStream(), chunks = [];
  for await (const chunk of stream!) chunks.push(chunk);
  expect(Buffer.concat(chunks).toString('utf8')).toBe(saved.content);
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  const sourceDialog = page.getByRole('dialog', { name: '本集小说原文', exact: true });
  await sourceDialog.evaluate(async element => { await Promise.all(element.getAnimations().map(animation => animation.finished)); });
  const novel = sourceDialog.getByRole('textbox', { name: '本集小说正文', exact: true });
  const novelSize = await novel.boundingBox();
  await page.getByLabel('导入小说文件', { exact: true }).setInputFiles({ name: 'chapter.pdf', mimeType: 'application/pdf', buffer: Buffer.from('invalid') });
  await expect(sourceDialog.locator('.studio-message')).toContainText('请选择 .txt 文件');
  expect(await novel.boundingBox()).toEqual(novelSize);
  await expect(sourceDialog.locator('.ant-alert')).toHaveCount(0);
  const original = '导入的 UTF-8 小说原文\n\n结尾保留空行\n';
  await page.getByLabel('导入小说文件', { exact: true }).setInputFiles({ name: 'chapter.txt', mimeType: 'text/plain', buffer: Buffer.from(original) });
  await expect(page.getByRole('dialog', { name: '确认操作', exact: true })).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: '本集小说正文', exact: true })).toHaveValue(original);
  await expect.poll(() => data.writing.novel.content).toBe(original);
  await page.getByRole('button', { name: '返回剧本', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '创作要求', exact: true })).toBeVisible();
  await page.reload(); await expect(editor.locator('blockquote')).toHaveText('旧信里的台词');
  await expect(editor.locator('strong')).toHaveText('保留加粗'); await expect(editor.locator('em')).toHaveText('斜体');
  await page.screenshot({ path: test.info().outputPath('novel-quill-1440.png') });
  expect(data.writing.editing_script.content).toBe(saved.content); expect(data.errors).toEqual([]);
});

for (const scenario of [
  { name: 'desktop', width: 1440, height: 900, status: 404 },
  { name: 'narrow desktop', width: 1000, height: 800, status: 404 },
  { name: 'mobile conflict', width: 390, height: 844, status: 409 },
  { name: 'small mobile', width: 320, height: 740, status: 404 },
]) {
  test(`adaptation draft feedback preserves editor dimensions and recovery actions on ${scenario.name}`, async ({ page }) => {
    const data = await richFixture(page);
    await page.route(`**/api/v1${root}/drafts**`, route => route.request().method() === 'GET'
      ? route.fallback()
      : route.fulfill({ status: scenario.status, json: { error: { code: scenario.status === 409 ? 'creation_draft_version_conflict' : 'NOT_FOUND' } } }));
    await page.setViewportSize({ width: scenario.width, height: scenario.height });
    await page.goto(`${root}/source`);
    if (scenario.width <= 700) await page.getByRole('button', { name: '展开设置', exact: true }).click();
    const settings = page.locator('.novel-adaptation-settings');
    const prompt = settings.getByRole('textbox', { name: '创作要求', exact: true });
    const input = '保留原著的悬念，让林晚在集尾发现信的主人。';
    await expect(prompt).toBeEnabled();
    const promptBox = await prompt.boundingBox();
    await prompt.fill(input);
    await expect(page.getByRole('button', { name: /^保存状态：(需处理|待同步)/ })).toBeVisible();
    if (scenario.width <= 700) {
      await page.getByRole('button', { name: '收起剧本设置', exact: true }).hover();
      await expect(page.getByRole('tooltip', { name: '收起剧本设置', exact: true })).toBeVisible();
    }
    const details = await openSaveDetails(page);
    await expect(details).toContainText(scenario.status === 409
      ? '生成设置版本冲突，本机输入已保留，请载入服务端版本后核对。'
      : '配置不存在或已被删除，请刷新列表。');
    expect(await prompt.boundingBox()).toEqual(promptBox);
    await expect(settings.getByRole('alert')).toHaveCount(0);
    const actions = details.locator('.novel-draft-actions');
    await expect(actions.getByRole('button', { name: '下载草稿', exact: true })).toBeVisible();
    await expect(actions.getByRole('button', { name: '使用服务端设置', exact: true })).toBeVisible();
    await expect(actions.getByRole('button', { name: '重试保存', exact: true })).toHaveCount(scenario.status === 409 ? 0 : 1);
    for (const button of await actions.getByRole('button').all()) await expect(button).toBeInViewport();
    await expect(prompt).toHaveValue(input);
    expect(await settings.locator('.novel-adaptation-body').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    await details.screenshot({ path: test.info().outputPath(`adaptation-draft-feedback-${scenario.width}.png`) });
    expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
  });
}

test('novel layout follows the prototype settings header, paired fields and centered document', async ({ page }) => {
  const data = await richFixture(page);
  data.writing.editing_script.editor_document = { ops: data.writing.editing_script.content.split('\n').map((line: string) => ({ insert: `${line}\n`, ...(/^第[一二]场/.test(line) ? { attributes: { header: 3 } } : {}) })) };
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${root}/source`);
  const editor = page.getByRole('textbox', { name: '本集剧本正文', exact: true });
  await expect(editor).toBeEditable();
  const settings = page.locator('.novel-adaptation-settings');
  const header = settings.locator(':scope > header');
  const settingsBox = (await settings.boundingBox())!;
  const headerBox = (await header.boundingBox())!;
  expect(headerBox.width).toBeCloseTo(settingsBox.width, 0);
  expect(headerBox.height).toBe(44);
  expect(await page.locator('.episode-layout').evaluate(node => getComputedStyle(node).padding)).toBe('8px');
  expect(await page.locator('.standard-creation-work').evaluate(node => getComputedStyle(node).gap)).toBe('8px');
  expect(await settings.locator('.novel-adaptation-body').evaluate(node => getComputedStyle(node).padding)).toBe('10px');
  await expect(header.getByRole('button', { name: '查看原文', exact: true })).toHaveCount(0);
  await expect(page.locator('.novel-script-heading').getByRole('button', { name: '查看原文', exact: true })).toHaveCount(0);
  const aspect = (await settings.getByRole('combobox', { name: '本集画幅', exact: true }).locator('..').boundingBox())!;
  const visualStyle = (await settings.getByRole('combobox', { name: '视觉风格', exact: true }).locator('..').boundingBox())!;
  expect(aspect.y).toBeCloseTo(visualStyle.y, 0);
  const composer = settings.locator('.generation-composer');
  expect((await composer.boundingBox())!.height).toBe(236);
  expect((await settings.locator('.novel-text-model').boundingBox())!.width).toBe(144);
  expect(visualStyle.x).toBeGreaterThan(aspect.x + aspect.width);
  const styleInput = settings.getByRole('combobox', { name: '视觉风格', exact: true });
  expect(await styleInput.evaluate(node => getComputedStyle(node).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
  await styleInput.fill('国风水墨');
  await expect(styleInput).toBeFocused();
  expect(await styleInput.evaluate(node => getComputedStyle(node).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: /^国风水墨$/ }).click();
  await expect.poll(() => data.requests.some(request => request.method === 'PATCH' && request.path === root && request.body.style === '国风水墨')).toBe(true);
  await expect(styleInput).toHaveValue('国风水墨');
  const title = page.locator('.novel-script-heading');
  expect((await title.boundingBox())!.height).toBe(48);
  expect(await title.locator('h2').evaluate(node => getComputedStyle(node).fontSize)).toBe('16px');
  const panelBox = (await page.locator('.novel-script-panel').boundingBox())!;
  const documentBox = (await editor.boundingBox())!;
  expect(documentBox.width).toBe(760);
  expect(await page.locator('.novel-script-scroll').evaluate(node => getComputedStyle(node).padding)).toBe('16px 24px 24px');
  expect(documentBox.x + documentBox.width / 2).toBeCloseTo(panelBox.x + panelBox.width / 2, 0);
  expect(await editor.evaluate(node => getComputedStyle(node).lineHeight)).toBe('27.3px');
  expect(await editor.locator('p').first().evaluate(node => getComputedStyle(node).marginBottom)).toBe('16px');
  expect(await editor.locator('h3').first().evaluate(node => getComputedStyle(node).borderBottomWidth)).toBe('1px');
  expect(await editor.locator('h3').first().evaluate(node => getComputedStyle(node).marginTop)).toBe('10px');
  await expect(page.getByRole('button', { name: '定位到第一场 雨夜', exact: true })).toHaveText('场次 01');
  await settings.getByRole('button', { name: '查看原文', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '本集小说原文', exact: true });
  expect((await drawer.boundingBox())!.width).toBe(500);
  expect(await drawer.getByRole('textbox', { name: '本集小说正文', exact: true }).evaluate(node => getComputedStyle(node).borderWidth)).toBe('0px');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('mobile novel uses one heading row and a viewport-height script without scene shortcuts', async ({ page }) => {
  const data = await richFixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${root}/source`);
  await expect(page.getByRole('textbox', { name: '本集剧本正文', exact: true })).toBeEditable();
  const heading = page.locator('.novel-script-heading');
  expect((await heading.boundingBox())!.height).toBe(52);
  const panel = page.locator('.novel-script-panel');
  expect((await panel.boundingBox())!.height).toBe(768);
  await expect(page.locator('.novel-scene-shortcuts')).not.toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});
