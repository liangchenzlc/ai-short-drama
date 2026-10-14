import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

async function standardFixture(page: Page) {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const time = '2026-10-13T00:00:00Z';
  const conversation = { id: '301', project_id: '10', episode_id: null, title: '创作讨论', scope_version: 2, stage: null, subject_type: null, subject_id: null, task_type: null, row_version: '1', archived: false, last_run_status: null, created_at: time, updated_at: time };
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: '9007199254740993', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true } } }));
  await page.route('**/api/v1/agent/status', route => route.fulfill({ json: { enabled: true, schema_ready: true } }));
  await page.route('**/api/v1/agent/models', route => route.fulfill({ json: { items: [{ id: '71', name: '创作讨论模型', row_version: '1', protocol: 'chat', preferred: true }], preferred_id: '71' } }));
  await page.route('**/api/v1/assistant/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/resolve') || /^.*\/conversations\/301$/.test(path)) return route.fulfill({ json: conversation });
    if (path.endsWith('/conversations')) return route.fulfill({ json: { items: [conversation], total: 1, offset: 0, limit: 20 } });
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (path.endsWith('/state')) return route.fulfill({ json: { conversation_id: '301', cursor: 0, resume_cursor: 0, active_run: null, queued_runs: [] } });
    return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 50 } });
  });
  return state;
}

const windows = [
  { width: 390, height: 844 }, { width: 1024, height: 768 },
  { width: 1280, height: 800 }, { width: 1440, height: 900 },
  { width: 1920, height: 1080 }, { width: 1440, height: 600 },
];
for (const viewport of windows) test(`three creation stages fill and adapt at ${viewport.width}x${viewport.height}`, async ({ page }, info) => {
  test.setTimeout(60000);
  const state = await standardFixture(page);
  await page.setViewportSize(viewport);
  for (const stage of ['source', 'assets', 'storyboard']) {
    if (stage === 'source') {
      await page.goto(`${root}/source`);
    } else {
      await page.getByRole('navigation', { name: '分集制作流程' })
        .getByRole('button', { name: stage === 'assets' ? /^素材生成，/ : /^分镜制作，/ }).click();
      await expect(page).toHaveURL(url => url.pathname === `${root}/${stage}`);
      await page.getByTestId(`episode-stage-${stage}`)
        .getByRole('button', { name: stage === 'assets' ? '林晚' : '选择分镜 01', exact: true }).click();
      await expect(page).toHaveURL(url => url.pathname === `${root}/${stage}`
        && url.searchParams.get(stage === 'assets' ? 'asset' : 'shot') === (stage === 'assets' ? '501' : '101'));
    }
    const work = page.locator('.standard-creation-work');
    await expect(work).toBeVisible({ timeout: 15000 });
    await expect(page.locator('.episode-sidebar,.episode-step-footer')).toHaveCount(0);
    if (stage === 'source') await expect(page.getByRole('textbox', { name: '本集剧本正文', exact: true })).toBeVisible();
    if (stage === 'assets') await expect(page.locator('.episode-asset-detail')).toBeVisible();
    if (stage === 'storyboard') await expect(page.locator('.standard-storyboard-panel h3').first()).toContainText('站台镜头 1');
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    if (viewport.width > 700) {
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollHeight <= innerHeight + 1)).toBe(true);
      const bounds = await work.boundingBox();
      expect(bounds!.y + bounds!.height).toBeGreaterThanOrEqual(viewport.height - 17);
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height);
      expect(Math.abs((await page.locator('.episode-top').boundingBox())!.height - 53)).toBeLessThanOrEqual(0.5);
      expect(Math.abs((await page.locator('.episode-top-step-nav').boundingBox())!.height - 52)).toBeLessThanOrEqual(0.5);
    }
    await page.screenshot({ path: info.outputPath(`${stage}-${viewport.width}x${viewport.height}.png`), fullPage: true, animations: 'disabled' });
    if (viewport.width <= 700 && stage !== 'source') {
      await page.getByRole('button', { name: '展开生成设置', exact: true }).click();
      const settings = page.getByRole('dialog', { name: stage === 'assets' ? '素材生成设置' : '分镜生成设置', exact: true });
      await expect(settings).toBeVisible();
      await page.screenshot({ path: info.outputPath(`${stage}-settings-${viewport.width}.png`), fullPage: true, animations: 'disabled' });
      await page.keyboard.press('Escape'); await expect(settings).not.toBeVisible();
      await expect(page.getByRole('button', { name: '展开生成设置', exact: true })).toBeFocused();
    }
    const before = await work.boundingBox();
    await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
    const message = page.getByRole('textbox', { name: '给助手的消息', exact: true });
    await expect(message).toBeVisible(); await message.fill('保留当前未发送讨论');
    if (viewport.width >= 1000) {
      expect((await work.boundingBox())!.width).toBeLessThan(before!.width);
      await expect(page.getByRole('textbox', { name: stage === 'source' ? '改编要求' : '画面要求', exact: true })).toBeVisible();
      if (stage === 'storyboard') await page.getByRole('button', { name: '选择分镜 02', exact: true }).click();
      await expect(message).toHaveValue('保留当前未发送讨论');
    }
    if (viewport.width > 700) await expect.poll(() => page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThanOrEqual(viewport.height + 1);
    await page.screenshot({ path: info.outputPath(`${stage}-assistant-${viewport.width}x${viewport.height}.png`), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '关闭助手', exact: true }).click();
    await expect(message).not.toBeVisible();
  }
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const assistantOpen of [false, true]) test(`short asset settings keep the composer reachable with assistant ${assistantOpen ? 'open' : 'closed'}`, async ({ page }, info) => {
  const state = await standardFixture(page);
  await page.setViewportSize({ width: 1440, height: 600 });
  await page.goto(`${root}/assets?asset=501`);
  if (assistantOpen) await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
  const settings = page.locator('.standard-asset-settings-content');
  const prompt = settings.getByRole('textbox', { name: '画面要求', exact: true });
  const send = settings.getByRole('button', { name: '生成图片', exact: true });
  await expect(send).toBeEnabled();
  await prompt.scrollIntoViewIfNeeded(); await expect(prompt).toBeInViewport({ ratio: 0.99 });
  await send.scrollIntoViewIfNeeded(); await expect(send).toBeInViewport({ ratio: 0.99 });
  await expect(settings.getByRole('combobox', { name: '生图模型', exact: true })).toBeInViewport({ ratio: 0.99 });
  await page.screenshot({ path: info.outputPath(`assets-short-composer-${assistantOpen ? 'open' : 'closed'}.png`), fullPage: true, animations: 'disabled' });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('image and video drafts freeze their own parameters without changing shared shot fields', async ({ page }) => {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const shared = structuredClone(state.shots[0]);
  const submissions: { kind: string; body: any }[] = [];
  await page.route('**/api/v1/ai/generations/image', route => { submissions.push({ kind: 'image', body: route.request().postDataJSON() }); return route.fulfill({ json: { generation_id: '97001', service_type: 'image', status: 'queued' } }); });
  await page.route('**/api/v1/ai/generations/video', route => { submissions.push({ kind: 'video', body: route.request().postDataJSON() }); return route.fulfill({ json: { generation_id: '97002', service_type: 'video', status: 'queued' } }); });
  await page.goto(`${root}/storyboard?shot=101`);
  await page.getByRole('textbox', { name: '画面要求', exact: true }).fill('私人分镜图要求');
  await page.getByRole('combobox', { name: '生成张数', exact: true }).focus();
  await page.keyboard.press('Enter');
  await page.locator('.ant-select-item-option').filter({ hasText: /^4 张$/ }).click();
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await expect.poll(() => submissions.length).toBe(1);
  const imageDraft = state.creationDrafts.get('101:storyboard_image');
  expect(submissions[0].body.source).toMatchObject({ draft_id: imageDraft.id, draft_version: imageDraft.draft_version });
  expect(submissions[0].body.parameters.count).toBe(4); expect(imageDraft.input_text).toBe('私人分镜图要求');
  await page.getByRole('tab', { name: '分镜视频', exact: true }).click();
  await page.getByRole('textbox', { name: '视频提示词', exact: true }).fill('私人分镜视频要求');
  await page.getByRole('combobox', { name: '视频时长', exact: true }).focus();
  await page.keyboard.press('Enter');
  await page.locator('.ant-select-item-option').filter({ hasText: /^5秒$/ }).click();
  await page.getByRole('button', { name: '生成视频', exact: true }).click();
  await expect.poll(() => submissions.length).toBe(2);
  const videoDraft = state.creationDrafts.get('101:storyboard_video');
  expect(submissions[1].body.source).toMatchObject({ draft_id: videoDraft.id, draft_version: videoDraft.draft_version, reference_media_id: shared.image.media_id });
  expect(submissions[1].body.parameters.duration_ms).toBe(5000); expect(videoDraft.input_text).toBe('私人分镜视频要求');
  expect(state.shots[0].duration_ms).toBe(shared.duration_ms); expect(state.shots[0].video_settings).toEqual(shared.video_settings);
  expect(state.shots[0].video_prompt).toBe(shared.video_prompt); expect(state.shots[0].asset_ids).toEqual(shared.asset_ids);
  await page.getByRole('tab', { name: '分镜图', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '画面要求', exact: true })).toHaveValue('私人分镜图要求');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const kind of ['image', 'video'] as const) test(`unknown shot ${kind} uses readonly lookup after reload`, async ({ page }) => {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const keys: string[] = []; const lookups: string[] = [];
  await page.route(`**/api/v1/ai/generations/${kind}`, route => { keys.push(route.request().headers()['idempotency-key']); return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE', message: '受理回执未确认' } } }); });
  await page.route('**/api/v1/ai/generations/by-idempotency-key?**', route => { const query = new URL(route.request().url()).searchParams; expect(query.get('service_type')).toBe(kind); lookups.push(query.get('idempotency_key')!); return route.fulfill({ json: null }); });
  await page.goto(`${root}/storyboard?shot=101`);
  if (kind === 'video') await page.getByRole('tab', { name: '分镜视频', exact: true }).click();
  const send = page.getByRole('button', { name: kind === 'image' ? '生成图片' : '生成视频', exact: true });
  await expect(send).toBeEnabled(); await send.click();
  const check = page.getByRole('button', { name: kind === 'image' ? '核对原图片请求' : '核对原视频请求', exact: true });
  await expect(check).toBeVisible();
  page.removeAllListeners('dialog');
  page.on('dialog', dialog => { void dialog.accept(); });
  await page.reload();
  if (kind === 'video') await page.getByRole('tab', { name: '分镜视频', exact: true }).click();
  await check.click(); await expect.poll(() => lookups.length).toBe(1);
  expect(lookups[0]).toBe(keys[0]); expect(keys).toHaveLength(1); await expect(send).toBeDisabled();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('shot conflict pauses autosave and preserves title and script after reload', async ({ page }) => {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  let saves = 0;
  await page.route(`**/api/v1${root}/shots/101`, route => {
    if (route.request().method() !== 'PATCH') return route.fallback();
    saves++; state.shots[0].row_version = '2';
    return route.fulfill({ status: 409, json: { error: { code: 'version_conflict' } } });
  });
  await page.goto(`${root}/storyboard?shot=101`);
  await page.getByRole('textbox', { name: '镜头名称', exact: true }).fill('恢复后仍保留的名称');
  await page.getByRole('textbox', { name: '分镜 1 脚本', exact: true }).fill('恢复后仍保留的脚本');
  await expect(page.locator('#storyboard-image-pane').getByText('镜头保存已暂停，输入保留。', { exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: '分镜 1 脚本', exact: true }).fill('冲突后继续保留的新脚本');
  page.removeAllListeners('dialog');
  page.once('dialog', async dialog => { expect(dialog.type()).toBe('beforeunload'); await dialog.accept(); });
  await page.reload();
  await expect(page.getByRole('textbox', { name: '镜头名称', exact: true })).toHaveValue('恢复后仍保留的名称');
  await expect(page.getByRole('textbox', { name: '分镜 1 脚本', exact: true })).toHaveValue('冲突后继续保留的新脚本');
  await expect(page.getByRole('button', { name: '生成图片', exact: true })).toBeDisabled();
  expect(saves).toBe(1); expect(state.errors).toEqual([]);
});

for (const kind of ['image', 'video'] as const) test(`resolved default ${kind} model is saved when the private draft loads late`, async ({ page }) => {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  let releaseDraft!: () => void;
  const draftReady = new Promise<void>(resolve => { releaseDraft = resolve; });
  let draftRequested = false;
  await page.route(`**/api/v1${root}/drafts/101/storyboard_${kind}`, async route => {
    if (route.request().method() !== 'GET') return route.fallback();
    draftRequested = true;
    await draftReady;
    return route.fallback();
  });
  const submissions: any[] = [];
  await page.route(`**/api/v1/ai/generations/${kind}`, route => {
    submissions.push(route.request().postDataJSON());
    return route.fulfill({ json: { generation_id: '97001', service_type: kind, status: 'queued' } });
  });
  await page.goto(`${root}/storyboard?shot=101`);
  if (kind === 'video') await page.getByRole('tab', { name: '分镜视频', exact: true }).click();
  await expect.poll(() => draftRequested).toBe(true);
  const model = page.getByRole('combobox', { name: kind === 'image' ? '分镜生图模型' : '视频模型', exact: true });
  await expect(model).toBeDisabled();
  await expect(page.locator('.generation-config-select').filter({ has: model }).getByText(/本地验收模型.*默认/)).toBeVisible();
  releaseDraft();
  const send = page.getByRole('button', { name: kind === 'image' ? '生成图片' : '生成视频', exact: true });
  await expect(send).toBeEnabled(); await send.click();
  await expect.poll(() => submissions.length).toBe(1);
  const saved = state.creationDrafts.get(`101:storyboard_${kind}`);
  expect(saved.model_config_id).toBe('77');
  expect(submissions[0].config_id).toBe('77');
  expect(submissions[0].source).toMatchObject({ draft_id: saved.id, draft_version: saved.draft_version });
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('confirmed shot reload locks editing during a delayed server response', async ({ page }) => {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.route(`**/api/v1${root}/shots/101`, route => route.request().method() === 'PATCH'
    ? route.fulfill({ status: 409, json: { error: { code: 'version_conflict' } } }) : route.fallback());
  await page.goto(`${root}/storyboard?shot=101`);
  const title = page.getByRole('textbox', { name: '镜头名称', exact: true });
  await title.fill('确认载入前的本机名称');
  await expect(page.locator('#storyboard-image-pane').getByText('镜头保存已暂停，输入保留。', { exact: true })).toBeVisible();
  let releaseRead!: () => void;
  const readReady = new Promise<void>(resolve => { releaseRead = resolve; });
  let readStarted = false;
  await page.route(`**/api/v1${root}/shots/101`, async route => {
    if (route.request().method() !== 'GET') return route.fallback();
    readStarted = true; await readReady; return route.fallback();
  });
  await page.getByRole('button', { name: '载入服务端镜头', exact: true }).click();
  await page.getByRole('button', { name: '确认继续', exact: true }).click();
  await expect.poll(() => readStarted).toBe(true);
  await expect(title).toBeDisabled();
  await expect(page.getByRole('textbox', { name: '分镜 1 脚本', exact: true })).toBeDisabled();
  releaseRead();
  await expect(title).toBeEnabled(); await expect(title).toHaveValue(state.shots[0].title);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('adjacent selection crosses the twenty-shot page boundary and retains the stable shot identity', async ({ page }) => {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.goto(`${root}/storyboard?shot=120`);
  const panel = page.locator('.standard-storyboard-panel');
  await expect(panel.getByRole('textbox', { name: '分镜 20 脚本', exact: true })).toBeVisible();
  await expect(page.locator('.storyboard-shot-card')).toHaveCount(20);
  await panel.getByRole('button', { name: '下一个分镜', exact: true }).click();
  await expect(panel.getByRole('textbox', { name: '分镜 21 脚本', exact: true })).toBeVisible();
  await expect(page).toHaveURL(url => url.searchParams.get('shot') === '121');
  await expect(page.locator('.storyboard-shot-card')).toHaveCount(40);
  await panel.getByRole('button', { name: '上一个分镜', exact: true }).click();
  await expect(panel.getByRole('textbox', { name: '分镜 20 脚本', exact: true })).toHaveValue(state.shots[19].script);
  await expect(page).toHaveURL(url => url.searchParams.get('shot') === '120');
  expect(state.requests.some(request => request.path === `${root}/shots` && request.query.get('offset') === '20' && request.query.get('limit') === '20')).toBe(true);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('mobile storyboard settings and assistant preserve input and trap and restore keyboard focus', async ({ page }) => {
  const state = await standardFixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${root}/storyboard?shot=101`);
  const settingsTrigger = page.getByRole('button', { name: '展开生成设置', exact: true });
  await settingsTrigger.click();
  const settings = page.getByRole('dialog', { name: '分镜生成设置', exact: true });
  const closeSettings = settings.getByRole('button', { name: '关闭分镜生成设置', exact: true });
  await expect(closeSettings).toBeFocused();
  const prompt = settings.getByRole('textbox', { name: '画面要求', exact: true });
  await prompt.fill('保留手机里的分镜图要求');
  await expect(settings.getByRole('button', { name: '生成图片', exact: true })).toBeEnabled();
  const outputOptions = settings.getByRole('button', { name: '比例与分辨率', exact: true });
  await outputOptions.click();
  const options = page.getByRole('dialog', { name: '生成选项', exact: true });
  await expect(options).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(options).not.toBeVisible();
  await expect(settings).toBeVisible();
  await expect(outputOptions).toBeFocused();
  await closeSettings.focus(); await page.keyboard.press('Shift+Tab');
  await expect(settings.getByRole('button', { name: '生成图片', exact: true })).toBeFocused();
  await page.keyboard.press('Tab'); await expect(closeSettings).toBeFocused();
  await page.keyboard.press('Escape'); await expect(settings).not.toBeVisible();
  await expect(settingsTrigger).toBeFocused();
  const assistantTrigger = page.getByRole('button', { name: 'AI 创作助手', exact: true });
  await assistantTrigger.click();
  const assistant = page.getByRole('dialog', { name: 'AI 创作助手区域', exact: true });
  const message = assistant.getByRole('textbox', { name: '给助手的消息', exact: true });
  await message.fill('保留手机助手讨论');
  await expect(settings).not.toBeVisible();
  const send = assistant.getByRole('button', { name: '发送', exact: true });
  const newConversation = assistant.getByRole('button', { name: '新对话', exact: true });
  await expect(send).toBeEnabled(); await expect(newConversation).toBeEnabled();
  const attachmentTools = assistant.getByRole('button', { name: '附件与技能', exact: true });
  await newConversation.focus(); await page.keyboard.press('Shift+Tab'); await expect(attachmentTools).toBeFocused();
  await page.keyboard.press('Tab'); await expect(newConversation).toBeFocused();
  await page.keyboard.press('Escape'); await expect(assistant).not.toBeVisible();
  await expect(assistantTrigger).toBeFocused();
  await settingsTrigger.click(); await expect(prompt).toHaveValue('保留手机里的分镜图要求');
  await expect(assistant).not.toBeVisible();
  await page.keyboard.press('Escape'); await assistantTrigger.click();
  await expect(message).toHaveValue('保留手机助手讨论');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const action of ['retry', 'reload'] as const) test(`private draft ${action} locks feedback actions through a delayed readonly check`, async ({ page }) => {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  let failSave = true;
  await page.route(`**/api/v1${root}/drafts`, route => route.request().method() === 'POST' && route.request().postDataJSON().purpose === 'storyboard_image' && failSave
    ? route.fulfill({ status: 503, json: { error: { code: 'OFFLINE', message: '个人设置保存暂时失败' } } }) : route.fallback());
  await page.goto(`${root}/storyboard?shot=101`);
  const prompt = page.getByRole('textbox', { name: '画面要求', exact: true });
  await prompt.fill('核对期间保留的私人输入');
  const retry = page.getByRole('button', { name: '重试保存设置', exact: true, includeHidden: true });
  const reload = page.getByRole('button', { name: '载入服务端设置', exact: true, includeHidden: true });
  await expect(retry).toBeVisible();
  let releaseRead!: () => void;
  const ready = new Promise<void>(resolve => { releaseRead = resolve; });
  let reads = 0;
  await page.route(`**/api/v1${root}/drafts/101/storyboard_image`, async route => {
    if (route.request().method() !== 'GET') return route.fallback();
    reads++; await ready; return route.fallback();
  });
  failSave = false;
  await (action === 'retry' ? retry : reload).click();
  await expect(retry).toBeDisabled(); await expect(reload).toBeDisabled();
  await expect(action === 'retry' ? retry : reload).toHaveClass(/ant-btn-loading/);
  if (action === 'reload') await page.getByRole('button', { name: '确认继续', exact: true }).click();
  await expect.poll(() => reads).toBe(1);
  if (action === 'reload') await expect(prompt).toBeDisabled();
  expect(reads).toBe(1); releaseRead();
  await expect(prompt).toBeEnabled();
  await expect(retry).toHaveCount(0);
  if (action === 'retry') {
    await expect.poll(() => state.creationDrafts.get('101:storyboard_image')?.input_text).toBe('核对期间保留的私人输入');
    await expect(prompt).toHaveValue('核对期间保留的私人输入');
  }
  expect(reads).toBe(1); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const viewport of windows) test(`creation layout matches the actual HTML prototype at ${viewport.width}x${viewport.height}`, async ({ page }) => {
  test.setTimeout(60000);
  const state = await standardFixture(page);
  await page.setViewportSize(viewport);
  const reference = await page.context().newPage();
  await reference.setViewportSize(viewport);
  await reference.goto(pathToFileURL(resolve('../docs/design/standard-creation-workflow/index.html')).href);
  const artifactRoot = resolve('.runtime/standard-reimplementation/comparison');
  await Promise.all([mkdir(resolve(artifactRoot, 'reference'), { recursive: true }), mkdir(resolve(artifactRoot, 'app'), { recursive: true })]);
  const close = (actual: number, expected: number, name: string) => expect(Math.abs(actual - expected), `${name}: app=${actual}, prototype=${expected}`).toBeLessThanOrEqual(1);
  const compareBox = async (actualSelector: string, referenceSelector: string, name: string) => {
    const actual = await page.locator(actualSelector).first().boundingBox();
    const expected = await reference.locator(referenceSelector).first().boundingBox();
    expect(actual, name).not.toBeNull(); expect(expected, name).not.toBeNull();
    for (const key of ['x', 'y', 'width', 'height'] as const) close(actual![key], expected![key], `${name}.${key}`);
  };
  try {
    for (const stage of ['source', 'assets', 'storyboard'] as const) {
      await page.goto(`${root}/${stage}${stage === 'assets' ? '?asset=501' : stage === 'storyboard' ? '?shot=101' : ''}`);
      await reference.locator(`button[data-stage="${stage}"]`).click();
      await expect(page.locator(stage === 'source' ? '.novel-script-heading' : '.standard-object-heading')).toBeVisible();
      await compareBox('.episode-top', '.project-bar', 'project bar');
      await compareBox('.episode-top-step-nav', '.workflow', 'workflow');
      if (viewport.width <= 1279) await expect(page.locator('.episode-mode-label')).toBeHidden();
      else await expect(page.locator('.episode-mode-label')).toBeVisible();
      await expect(page.locator('.episode-step-connector')).toHaveCount(3);
      const number = await page.locator('.episode-step-button[aria-current=step] .episode-step-number').evaluate(node => {
        const style = getComputedStyle(node); return { width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height, radius: style.borderRadius, background: style.backgroundColor };
      });
      const expectedNumber = await reference.locator('.workflow-step[aria-current=step] .step-number').evaluate(node => {
        const style = getComputedStyle(node); return { width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height, radius: style.borderRadius, background: style.backgroundColor };
      });
      expect(number).toEqual(expectedNumber);
      await compareBox(stage === 'source' ? '.episode-work-stage' : '.creation-main-panel', '#mainPanel', 'main panel');
      if (stage !== 'source') {
        await expect(page.locator(stage === 'assets' ? '.standard-assets-tools' : '.storyboard-heading-actions').getByRole('button')).toHaveCount(stage === 'assets' ? 3 : 2);
        await expect(page.locator('.standard-object-heading').getByRole('button')).toHaveCount(stage === 'assets' ? 2 : 4);
        await compareBox('.episode-work-stage', '#objectPanel', 'object panel');
        const expectedListHeadingHeight = await reference.locator('#objectPanel .object-title').evaluate(node =>
          node.getBoundingClientRect().bottom - node.parentElement!.getBoundingClientRect().top + Number.parseFloat(getComputedStyle(node).marginBottom));
        const actualListHeading = await page.locator(stage === 'assets' ? '.standard-assets-heading' : '.standard-storyboard-list .storyboard-editor-heading').boundingBox();
        expect(actualListHeading).not.toBeNull();
        close(actualListHeading!.height, expectedListHeadingHeight, 'object heading.height');
        await compareBox('.standard-object-heading', '.panel-heading', 'panel heading');
        await compareBox('.standard-object-tabs', '.view-tabs', 'media tabs');
        await compareBox(stage === 'assets' ? '.standard-asset-card' : '.storyboard-shot-card', '#objectPanel .object-card', 'first object card');
        await compareBox('.creation-image-preview', '#mainPanel .preview-viewport', 'image viewport');
      }
      if (viewport.width > 700) {
        await compareBox(stage === 'source' ? '.creation-main-panel' : '.standard-object-settings', stage === 'source' ? '#adaptationPanel' : '#generationSettings', 'settings panel');
        await compareBox(stage === 'source' ? '.novel-adaptation-settings > header' : stage === 'assets' ? '.standard-asset-settings-heading' : '.shot-settings-drawer-heading', stage === 'source' ? '#adaptationPanel .inspector-heading' : '#generationSettings .inspector-heading', 'settings heading');
        if (stage === 'storyboard') {
          for (const index of [1, 2]) {
            const actualField = `#storyboard-image-settings .storyboard-shot-information .writing-control:nth-child(${index})`;
            const expectedField = `#generationSettings .generation-context > .field:nth-child(${index})`;
            await compareBox(actualField, expectedField, `shot field ${index}`);
            const actualLineHeight = await page.locator(`${actualField} > span`).evaluate(node => getComputedStyle(node).lineHeight);
            const expectedLineHeight = await reference.locator(`${expectedField} > span`).evaluate(node => getComputedStyle(node).lineHeight);
            expect(actualLineHeight).toBe(expectedLineHeight);
          }
          const actualReferenceLineHeight = await page.locator('#storyboard-image-settings .standard-reference-heading h4').evaluate(node => getComputedStyle(node).lineHeight);
          const expectedReferenceLineHeight = await reference.locator('#generationSettings .generation-reference-heading > span').evaluate(node => getComputedStyle(node).lineHeight);
          expect(actualReferenceLineHeight).toBe(expectedReferenceLineHeight);
        }
        const actualComposer = page.locator('.generation-composer').filter({ visible: true }).first();
        const expectedComposer = reference.locator('.generation-composer').filter({ visible: true }).first();
        const actualComposerBox = await actualComposer.boundingBox();
        const expectedComposerBox = await expectedComposer.boundingBox();
        expect(actualComposerBox).not.toBeNull(); expect(expectedComposerBox).not.toBeNull();
        for (const key of ['x', 'y', 'width', 'height'] as const) close(actualComposerBox![key], expectedComposerBox![key], `generation composer.${key}`);
        const actual = await actualComposer.evaluate(node => { const style = getComputedStyle(node); return { radius: style.borderRadius, padding: style.padding, background: style.backgroundColor }; });
        const expected = await expectedComposer.evaluate(node => { const style = getComputedStyle(node); return { radius: style.borderRadius, padding: style.padding, background: style.backgroundColor }; });
        expect(actual).toEqual(expected);
      }
      for (const assistantOpen of [false, true]) {
        if (assistantOpen) {
          await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
          await reference.locator('#assistantToggle').click();
          await expect(page.getByRole('textbox', { name: '给助手的消息', exact: true })).toBeVisible();
          await compareBox('.episode-assistant-sidebar', '#inspector', 'assistant panel');
          await compareBox('.episode-assistant-sidebar .ai-assistant-header', '#inspector .inspector-heading', 'assistant heading');
          if (viewport.width >= 1000) await compareBox(stage === 'source' ? '.episode-work-stage' : '.creation-main-panel', '#mainPanel', 'main panel with assistant');
        }
        const name = `${stage}${assistantOpen ? '-assistant' : ''}-${viewport.width}x${viewport.height}.png`;
        await reference.screenshot({ path: resolve(artifactRoot, 'reference', name), fullPage: true, animations: 'disabled' });
        await page.screenshot({ path: resolve(artifactRoot, 'app', name), fullPage: true, animations: 'disabled' });
      }
      await reference.locator('#assistantToggle').click();
    }
  } finally { await reference.close(); }
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('preview zoom follows the prototype and full preview remains usable', async ({ page }) => {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.goto(`${root}/storyboard?shot=101`);
  const tools = page.getByRole('group', { name: '图片预览工具', exact: true });
  await expect(tools).toBeVisible();
  await tools.getByRole('button', { name: '放大预览', exact: true }).click();
  await expect(tools.getByText('110%', { exact: true })).toBeVisible();
  await expect(page.locator('#storyboard-image-pane .creation-image-preview img')).toHaveCSS('transform', 'matrix(1.1, 0, 0, 1.1, 0, 0)');
  await tools.getByRole('button', { name: '适合窗口', exact: true }).click();
  await expect(tools.getByText('100%', { exact: true })).toBeVisible();
  for (let index = 0; index < 5; index++) await tools.getByRole('button', { name: '缩小预览', exact: true }).click();
  await expect(tools.getByRole('button', { name: '缩小预览', exact: true })).toBeDisabled();
  await tools.getByRole('button', { name: '全屏预览', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '分镜图片预览', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(tools).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
