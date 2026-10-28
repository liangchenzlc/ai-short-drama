import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from '../support/studio-fixture';

async function openSaveDetails(page: Page) {
  const status = page.getByRole('button', { name: /^保存状态：/ });
  if (await status.getAttribute('aria-expanded') !== 'true') await status.click();
  return page.locator('.standard-save-details');
}

async function standardFixture(page: Page) {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const time = '2026-10-13T00:00:00Z';
  const conversation = { id: '301', project_id: '10', title: '创作讨论', scope_version: 3, stage: null, subject_type: null, subject_id: null, task_type: null, row_version: '1', status: 'active', archived: false, last_run_status: null, created_at: time, updated_at: time };
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: '9007199254740993', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true } } }));
  await page.route('**/api/v1/agent/status', route => route.fulfill({ json: { enabled: true, schema_ready: true } }));
  await page.route('**/api/v1/agent/models', route => route.fulfill({ json: { items: [{ id: '71', name: '创作讨论模型', row_version: '1', protocol: 'chat', preferred: true }], preferred_id: '71' } }));
  await page.route('**/api/v1/assistant/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/resolve') || /^.*\/conversations\/301$/.test(path)) return route.fulfill({ json: conversation });
    if (path.endsWith('/conversations')) return route.fulfill({ json: { items: [conversation], total: 1, offset: 0, limit: 20 } });
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (path.endsWith('/state')) return route.fulfill({ json: { conversation_id: '301', snapshot_cursor: 0, resume_cursor: 0, messages: { items: [], total: 0, offset: 0, limit: 50 }, tasks: [], runs: [], operations: [], inputs: [], has_more_tasks: false, has_more_operations: false } });
    return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 50 } });
  });
  return state;
}

const windows = [
  { width: 1280, height: 720 }, { width: 1366, height: 768 },
  { width: 1440, height: 900 }, { width: 1920, height: 1080 }, { width: 1440, height: 600 },
];
for (const viewport of windows) test('three creation stages fill the desktop at ' + viewport.width + 'x' + viewport.height, async ({ page }, info) => {
  test.setTimeout(60000);
  const state = await standardFixture(page);
  await page.setViewportSize(viewport);
  await page.goto(root + '/source');
  for (const stage of ['source', 'assets', 'storyboard']) {
    if (stage !== 'source') await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: stage === 'assets' ? '资产生成' : '分镜制作', exact: true }).click();
    const work = page.locator('.standard-creation-work');
    await expect(work).toBeVisible();
    const navigation = page.getByRole('navigation', { name: '创作流程' });
    await expect(navigation.getByRole('button')).toHaveCount(4);
    expect((await page.locator('.creation-top').boundingBox())!.height).toBe(60);
    await expect(page.locator('.creation-stage-select')).toHaveCount(0);
    if (stage === 'source') await expect(page.getByRole('textbox', { name: '剧本正文', exact: true })).toBeEditable();
    else await expect(page.getByRole('textbox', { name: '画面要求', exact: true })).toBeVisible();
    const before = (await work.boundingBox())!.width;
    await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
    const assistant = page.getByRole('complementary', { name: 'AI 创作助手区域', exact: true });
    const input = assistant.getByRole('textbox', { name: '给助手的消息', exact: true });
    await expect(input).toBeVisible(); await input.fill('保留当前未发送讨论');
    expect((await work.boundingBox())!.width).toBeLessThan(before);
    if (stage !== 'source') await expect(page.getByRole('textbox', { name: '画面要求', exact: true })).toBeVisible();
    await expect(page.getByRole('dialog', { name: /生成设置|AI 创作助手区域/ })).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1 && document.documentElement.scrollHeight <= innerHeight + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath(stage + '-desktop-' + viewport.width + '.png'), animations: 'disabled' });
    await page.getByRole('button', { name: '关闭助手', exact: true }).click();
    await expect(input).not.toBeVisible();
  }
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test.describe('desktop creation controls', () => {
  for (const width of [1280, 1440]) test('generation controls remain reachable at ' + width + 'px', async ({ page }, info) => {
    const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
    await page.setViewportSize({ width, height: 720 });
    for (const kind of ['asset', 'image', 'video']) {
      await page.goto(root + (kind === 'asset' ? '/assets?asset=501' : '/storyboard?shot=101'));
      if (kind === 'video') await page.getByRole('tab', { name: '分镜视频', exact: true }).click();
      const settings = page.locator('.standard-object-settings:visible');
      const send = settings.getByRole('button', { name: kind === 'video' ? '生成视频' : '生成图片', exact: true });
      await send.scrollIntoViewIfNeeded(); await expect(send).toBeInViewport({ ratio: 1 }); await expect(send).toBeEnabled();
      await expect(settings.getByRole('combobox').last()).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      await page.screenshot({ path: info.outputPath(kind + '-desktop-' + width + '.png'), animations: 'disabled' });
    }
    expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
  });
});

test('detailed model menu searches long names and selects the final virtual row', async ({ page }, info) => {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.route('**/api/v1/ai-model-configs?**', route => {
    const kind = new URL(route.request().url()).searchParams.get('service_type') ?? 'text';
    return route.fulfill({ json: { items: Array.from({ length: 24 }, (_, index) => ({ id: String(77 + index), name: `模型 ${index + 1} · 长名称用于核对菜单完整内容`, model_key: `fixture-model-${index + 1}`, service_type: kind, provider: 'fixture', enabled: 1, is_default: index === 0 ? 1 : 0, is_deleted: 0, row_version: '1', has_api_key: true })), total: 24, offset: 0, limit: 100 } });
  });
  await page.setViewportSize({ width: 1280, height: 844 });
  await page.goto(`${root}/assets?asset=501`);
  const select = page.getByRole('combobox', { name: '生图模型', exact: true });
  const trigger = page.locator('.generation-composer-model .ant-select-selector').filter({ visible: true });
  await trigger.click();
  const popup = page.locator('.creation-model-popup').filter({ visible: true });
  await expect(popup).toBeVisible();
  expect((await popup.boundingBox())!.width).toBeLessThanOrEqual(1280);
  await page.screenshot({ path: info.outputPath('model-menu-desktop-1280.png'), animations: 'disabled' });
  await popup.locator('.rc-virtual-list-holder').evaluate(node => { node.scrollTop = node.scrollHeight; });
  await popup.getByText('模型 24 · 长名称用于核对菜单完整内容', { exact: true }).click();
  await expect.poll(() => state.creationDrafts.get('501:asset_image')?.model_config_id).toBe('100');
  await trigger.click(); await select.fill('fixture-model-24');
  await expect(popup.locator('.creation-model-option strong')).toHaveText('模型 24 · 长名称用于核对菜单完整内容');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('textbox', { name: '画面要求', exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
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
  await page.getByRole('button', { name: '比例与分辨率', exact: true }).click();
  await page.getByRole('dialog', { name: '生成选项', exact: true }).getByRole('button', { name: '生成 4 张图片', exact: true }).click();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await expect.poll(() => submissions.length).toBe(1);
  const imageDraft = state.creationDrafts.get('101:storyboard_image');
  expect(submissions[0].body.source).toMatchObject({ draft_id: imageDraft.id, draft_version: imageDraft.draft_version });
  expect(submissions[0].body.parameters.count).toBe(4); expect(imageDraft.input_text).toBe('私人分镜图要求');
  await page.getByRole('tab', { name: '分镜视频', exact: true }).click();
  await page.getByRole('textbox', { name: '视频提示词', exact: true }).fill('私人分镜视频要求');
  await page.getByRole('button', { name: '视频比例与分辨率', exact: true }).click();
  await page.getByRole('dialog', { name: '视频生成选项', exact: true }).getByRole('button', { name: '视频时长 5秒', exact: true }).click();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '生成视频', exact: true }).click();
  await expect.poll(() => submissions.length).toBe(2);
  const videoDraft = state.creationDrafts.get('101:storyboard_video');
  expect(submissions[1].body.source).toMatchObject({ draft_id: videoDraft.id, draft_version: videoDraft.draft_version, reference_media_id: shared.image.media_id });
  expect(submissions[1].body.parameters.duration_ms).toBe(5000); expect(videoDraft.input_text).toBe('私人分镜视频要求');
  expect(state.shots[0].duration_ms).toBe(shared.duration_ms); expect(state.shots[0].video_settings).toEqual(shared.video_settings);
  expect(state.shots[0].video_prompt).toBe(shared.video_prompt); expect(state.shots[0].asset_ids).toEqual(shared.asset_ids);
  await page.getByRole('tab', { name: '分镜图', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '画面要求', exact: true })).toHaveText('私人分镜图要求');
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
  await expect.poll(() => keys.length).toBe(1);
  await openSaveDetails(page);
  const check = page.getByRole('button', { name: kind === 'image' ? '核对原图片请求' : '核对原视频请求', exact: true });
  await expect(check).toBeVisible();
  page.removeAllListeners('dialog');
  page.on('dialog', dialog => { void dialog.accept(); });
  await page.reload();
  if (kind === 'video') await page.getByRole('tab', { name: '分镜视频', exact: true }).click();
  await openSaveDetails(page);
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
  await expect(page.getByRole('button', { name: /^保存状态：需处理/ })).toBeVisible();
  await expect(await openSaveDetails(page)).toContainText('镜头保存已暂停，输入保留。');
  await page.getByRole('button', { name: /^保存状态：/ }).click();
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
  await expect(page.locator('.generation-config-select').filter({ has: model }).getByText('本地验收模型', { exact: true })).toBeVisible();
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

test('shot reload keeps a local backup and locks editing without confirmation', async ({ page }) => {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.route(`**/api/v1${root}/shots/101`, route => route.request().method() === 'PATCH'
    ? route.fulfill({ status: 409, json: { error: { code: 'version_conflict' } } }) : route.fallback());
  await page.goto(`${root}/storyboard?shot=101`);
  const title = page.getByRole('textbox', { name: '镜头名称', exact: true });
  await title.fill('确认载入前的本机名称');
  await expect(page.getByRole('button', { name: /^保存状态：需处理/ })).toBeVisible();
  await expect(await openSaveDetails(page)).toContainText('镜头保存已暂停，输入保留。');
  let releaseRead!: () => void;
  const readReady = new Promise<void>(resolve => { releaseRead = resolve; });
  let readStarted = false;
  await page.route(`**/api/v1${root}/shots/101`, async route => {
    if (route.request().method() !== 'GET') return route.fallback();
    readStarted = true; await readReady; return route.fallback();
  });
  await page.locator('.standard-save-details').getByRole('button', { name: '载入服务端镜头', exact: true }).first().click();
  await expect(page.getByRole('button', { name: '确认继续', exact: true })).toHaveCount(0);
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

test('desktop storyboard settings and assistant coexist without trapping focus and preserve drafts', async ({ page }) => {
  const state = await standardFixture(page);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto(root + '/storyboard?shot=101');
  const settings = page.locator('.standard-object-settings:visible');
  const prompt = settings.getByRole('textbox', { name: '画面要求', exact: true });
  await prompt.fill('保留分镜图要求');
  const output = settings.getByRole('button', { name: '比例与分辨率', exact: true });
  await output.click();
  const options = page.getByRole('dialog', { name: '生成选项', exact: true });
  await expect(options).toBeVisible(); await page.keyboard.press('Escape');
  await expect(options).toBeHidden(); await expect(output).toBeFocused();
  await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
  const assistant = page.getByRole('complementary', { name: 'AI 创作助手区域', exact: true });
  const input = assistant.getByRole('textbox', { name: '给助手的消息', exact: true });
  await input.fill('保留助手讨论');
  await expect(settings).toBeVisible(); await expect(prompt).toHaveText('保留分镜图要求');
  await prompt.focus(); await expect(prompt).toBeFocused();
  await page.keyboard.press('Escape'); await expect(settings).toBeVisible(); await expect(assistant).toBeVisible();
  await settings.getByRole('button', { name: '关闭分镜生成设置', exact: true }).click();
  const toggle = page.getByRole('button', { name: '展开生成设置', exact: true });
  await expect(toggle).toBeFocused(); await toggle.press('Enter');
  await expect(prompt).toHaveText('保留分镜图要求'); await expect(input).toHaveValue('保留助手讨论');
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
  await expect(page.getByRole('button', { name: /^保存状态：(待同步|需处理)/ })).toBeVisible();
  await openSaveDetails(page);
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
  await expect(page.getByRole('button', { name: '确认继续', exact: true })).toHaveCount(0);
  await expect.poll(() => reads).toBe(1);
  if (action === 'reload') await expect(prompt).toBeDisabled();
  expect(reads).toBe(1); releaseRead();
  await expect(prompt).toBeEnabled();
  await expect(retry).toHaveCount(0);
  if (action === 'retry') {
    await expect.poll(() => state.creationDrafts.get('101:storyboard_image')?.input_text).toBe('核对期间保留的私人输入');
    await expect(prompt).toHaveText('核对期间保留的私人输入');
  }
  // A timed-out create can require another scoped lookup after the locked read.
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const viewport of windows) test('desktop panels retain geometry and reachable controls at ' + viewport.width + 'x' + viewport.height, async ({ page }) => {
  const state = await standardFixture(page);
  await page.setViewportSize(viewport);
  for (const stage of ['assets', 'storyboard']) {
    await page.goto(root + '/' + stage);
    const layout = page.locator('.standard-creation-layout');
    const list = page.locator('.creation-work-stage');
    const panel = page.locator('.creation-main-panel');
    const settings = page.locator('.standard-object-settings:visible');
    await expect(settings).toBeVisible();
    for (const open of [false, true]) {
      if (open) await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
      const listBox = (await list.boundingBox())!, panelBox = (await panel.boundingBox())!, layoutBox = (await layout.boundingBox())!;
      expect(listBox.x + listBox.width).toBeLessThanOrEqual(panelBox.x);
      expect(panelBox.x + panelBox.width).toBeLessThanOrEqual(layoutBox.x + layoutBox.width);
      expect(layoutBox.y + layoutBox.height).toBeLessThanOrEqual(viewport.height + 1);
      expect(listBox.width).toBeGreaterThanOrEqual(200);
      expect(panelBox.width).toBeGreaterThan(400);
      await settings.getByRole('button', { name: '生成图片', exact: true }).scrollIntoViewIfNeeded();
      await expect(settings.getByRole('button', { name: '生成图片', exact: true })).toBeInViewport({ ratio: 1 });
      await expect(page.getByRole('navigation', { name: '创作流程' }).getByRole('button')).toHaveCount(4);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    }
  }
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('image groups retain each image size and full preview without group zoom controls', async ({ page }) => {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.goto(`${root}/storyboard?shot=101`);
  const group = page.locator('#storyboard-image-pane .standard-image-group');
  await expect(group.locator('.image-subject')).toHaveCount(1);
  await expect(group.locator('.image-subject-size')).toHaveText('1920 × 1080');
  await expect(group.locator('.image-group-caption')).toHaveCount(0);
  await expect(group.locator('.image-subject-label .image-primary-badge')).toHaveText('主图');
  await expect(page.getByRole('group', { name: '图片预览工具', exact: true })).toHaveCount(0);
  await group.locator('.image-subject-open').click();
  await expect(page.getByRole('dialog', { name: '图片预览', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '图片预览', exact: true })).toHaveCount(0);
  await expect(group).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
