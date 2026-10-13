import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';

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

test('Quill format-only edits save one version, survive refresh, and retain undo and redo', async ({ page }) => {
  const data = await richFixture(page); await page.goto(`${root}/source`);
  const editor = page.getByRole('textbox', { name: '本集剧本正文', exact: true });
  await expect(editor).toBeEditable();
  await expect(page.getByRole('button', { name: '确认当前剧本', exact: true })).toHaveCount(0);
  await editor.click(); await page.keyboard.press('Control+A');
  await page.getByRole('button', { name: '加粗', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认当前剧本', exact: true })).toBeVisible();
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
  await expect(page.locator('.novel-script-toolbar').getByText('2 场', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '定位到第二场 车站', exact: true }).click();
  expect(await editor.evaluate(node => getComputedStyle(node).outlineStyle)).toBe('none');
  expect(data.errors).toEqual([]);
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

test('candidate preview occupies the script panel read-only and adoption remains separate from confirmation', async ({ page }) => {
  const data = await richFixture(page); await page.goto(`${root}/source`);
  const original = data.writing.editing_script.content;
  await page.getByRole('button', { name: '生成记录', exact: true }).click();
  await page.getByRole('button', { name: '预览全文', exact: true }).click();
  const preview = page.getByRole('textbox', { name: '新剧本结果预览', exact: true });
  await expect(preview).toHaveAttribute('contenteditable', 'false');
  await expect(page.getByRole('dialog', { name: '候选剧本预览', exact: true })).toHaveCount(0);
  expect(data.writing.editing_script.content).toBe(original);
  await page.getByRole('button', { name: '采用并编辑', exact: true }).click();
  await page.getByRole('dialog', { name: '确认操作', exact: true }).getByRole('button', { name: '确认继续', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '本集剧本正文', exact: true })).toContainText('候选剧本');
  await expect(page.getByRole('button', { name: '确认当前剧本', exact: true })).toBeVisible();
  expect(data.writing.editing_script.state).toBe('unconfirmed'); expect(data.errors).toEqual([]);
});

test('unknown generation survives refresh and lookup reuses the original key without another POST', async ({ page }) => {
  const data = await richFixture(page); data.controls.loseSubmit = true;
  await page.goto(`${root}/source`); const prompt = page.getByRole('textbox', { name: '改编要求', exact: true });
  await prompt.fill('让第一场冲突更清晰');
  await page.getByRole('button', { name: '生成剧本', exact: true }).click();
  await expect.poll(() => data.submissions.length).toBe(1);
  expect(data.submissions[0].body.source.draft_id).toBe('801');
  expect(data.submissions[0].body.source.draft_version).toBe(data.draft.draft_version);
  await expect(page.getByRole('button', { name: '核对生成请求', exact: true })).toBeVisible();
  await page.reload(); await expect(prompt).toHaveValue('让第一场冲突更清晰');
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
  const prompt = page.getByRole('textbox', { name: '改编要求', exact: true }); await expect(prompt).toBeVisible(); await prompt.fill('手机输入的改编要求');
  expect(await prompt.evaluate(node => getComputedStyle(node).height)).toBe('180px');
  await page.keyboard.press('Escape'); await expect(prompt).not.toBeVisible();
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '本集小说原文', exact: true })).toBeVisible(); await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '展开设置', exact: true }).click(); await expect(prompt).toHaveValue('手机输入的改编要求');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); expect(data.errors).toEqual([]);
});

test('a delayed generation receipt cannot clear another account attempt or replace its generation state', async ({ page }) => {
  const data = await richFixture(page);
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
  await login.getByRole('button', { name: '登录并返回编辑', exact: true }).click();
  await expect(page.getByRole('link', { name: '账号中心：创作者 102', exact: true })).toBeVisible();
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
  const original = '导入的 UTF-8 小说原文\n\n结尾保留空行\n';
  await page.getByLabel('导入小说文件', { exact: true }).setInputFiles({ name: 'chapter.txt', mimeType: 'text/plain', buffer: Buffer.from(original) });
  await page.getByRole('dialog', { name: '确认操作', exact: true }).getByRole('button', { name: '确认继续', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '本集小说正文', exact: true })).toHaveValue(original);
  await expect.poll(() => data.writing.novel.content).toBe(original);
  await page.getByRole('button', { name: '返回剧本', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '改编要求', exact: true })).toBeVisible();
  await page.reload(); await expect(editor.locator('blockquote')).toHaveText('旧信里的台词');
  await expect(editor.locator('strong')).toHaveText('保留加粗'); await expect(editor.locator('em')).toHaveText('斜体');
  await page.screenshot({ path: test.info().outputPath('novel-quill-1440.png') });
  expect(data.writing.editing_script.content).toBe(saved.content); expect(data.errors).toEqual([]);
});
