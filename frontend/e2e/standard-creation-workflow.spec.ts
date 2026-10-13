import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';

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
  const state = await standardFixture(page);
  await page.setViewportSize(viewport);
  for (const stage of ['source', 'assets', 'storyboard']) {
    await page.goto(`${root}/${stage}${stage === 'assets' ? '?asset=501' : stage === 'storyboard' ? '?shot=101' : ''}`);
    const work = page.locator('.standard-creation-work');
    await expect(work).toBeVisible();
    await expect(page.locator('.episode-sidebar,.episode-step-footer')).toHaveCount(0);
    if (stage === 'source') await expect(page.getByRole('textbox', { name: '本集剧本正文', exact: true })).toBeVisible();
    if (stage === 'assets') await expect(page.locator('.episode-asset-detail')).toBeVisible();
    if (stage === 'storyboard') await expect(page.locator('.standard-storyboard-panel h3').first()).toContainText('站台镜头 1');
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    if (viewport.width > 700) {
      const bounds = await work.boundingBox();
      expect(bounds!.y + bounds!.height).toBeGreaterThanOrEqual(viewport.height - 17);
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height);
      expect((await page.locator('.episode-top').boundingBox())!.height).toBe(53);
      expect((await page.locator('.episode-top-step-nav').boundingBox())!.height).toBe(52);
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
    await page.screenshot({ path: info.outputPath(`${stage}-assistant-${viewport.width}x${viewport.height}.png`), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '关闭助手', exact: true }).click();
    await expect(message).not.toBeVisible();
  }
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
  await page.getByRole('combobox', { name: '图片数量', exact: true }).focus();
  await page.keyboard.press('Enter');
  await page.locator('.ant-select-item-option').filter({ hasText: /^4 张$/ }).click();
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await expect.poll(() => submissions.length).toBe(1);
  const imageDraft = state.creationDrafts.get('101:storyboard_image');
  expect(submissions[0].body.source).toMatchObject({ draft_id: imageDraft.id, draft_version: imageDraft.draft_version });
  expect(submissions[0].body.parameters.count).toBe(4); expect(imageDraft.input_text).toBe('私人分镜图要求');
  await page.getByRole('tab', { name: '分镜视频', exact: true }).click();
  await page.getByRole('textbox', { name: '视频提示词', exact: true }).fill('私人分镜视频要求');
  await page.getByRole('spinbutton', { name: '视频时长', exact: true }).fill('5');
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
