import { expect, test, type Page } from '@playwright/test';
import { fixture, image, root } from '../support/studio-fixture';

const time = '2026-10-03T00:00:00Z';
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}

async function safetyFixture(page: Page) {
  const base = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: '9007199254740993', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true } } }));
  const asset = (id: string, name: string) => ({
    id, kind: 'character', name, label: '主角', description: '深色风衣，神情坚定。',
    prompt: '人物肖像，深色风衣', tags: [], scene_time: '', state: 'confirmed', row_version: '1',
    media_id: `99${id}`, image: { media_id: `99${id}`, url: image }, reference_count: 1,
    link_id: `60${id}`, position: id === '501' ? 1 : 2,
  });
  const assets = [asset('501', '林晚'), asset('504', '沈川')];
  const conversation = {
    id: '301', project_id: '10', episode_id: null, title: '项目对话', row_version: '1',
    stage: null, subject_type: null, subject_id: null, task_type: null, scope_version: 2,
    archived: false, last_run_status: null, created_at: time, updated_at: time,
  };
  const state = {
    assets, conflicts: false, updates: [] as Record<string, unknown>[], sends: [] as Record<string, unknown>[],
  };
  let saving: ReturnType<typeof deferred> | null = null;
  let started: ReturnType<typeof deferred> | null = null;
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.slice('/api/v1'.length);
    const method = request.method();
    const body = method === 'POST' || method === 'PATCH' ? request.postDataJSON() : null;
    const reply = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path === `${root}/assets`) return reply({ items: assets, total: assets.length, offset: 0, limit: 20 });
    const current = assets.find(item => path === `/assets/${item.id}`);
    if (current) {
      if (method === 'PATCH') {
        state.updates.push({ id: current.id, ...body });
        started?.resolve();
        if (saving) await saving.promise;
        if (state.conflicts || body.row_version !== current.row_version) return reply({ error: { code: 'asset_version_conflict' } }, 409);
        Object.assign(current, body, { row_version: String(BigInt(current.row_version) + 1n) });
      }
      return reply(current);
    }
    if (assets.some(item => path === `/assets/${item.id}/image-candidates`)) return reply({ items: [], total: 0, offset: 0, limit: 20 });
    if (path === '/agent/status') return reply({ enabled: true, schema_ready: true });
    if (path === '/agent/models') return reply({
      items: [{ id: '71', name: '创作协作模型', model_key: 'fixture-agent', row_version: 1,
        protocol: 'chat', verified: true, tool_calling: true, tool_result_continuation: true,
        streaming: 'verified', preferred: true }], preferred_id: '71',
    });
    if (path === '/assistant/conversations') return reply({ items: [conversation], total: 1, offset: 0, limit: 20 });
    if (path === '/assistant/conversations/resolve') return reply(conversation);
    if (path === '/assistant/conversations/301') return reply(conversation);
    if (path === '/assistant/conversations/301/state') return reply({ conversation_id: '301', cursor: 0, resume_cursor: 0, active_run: null, queued_runs: [] });
    if (path === '/assistant/conversations/301/events') return route.fulfill({
      status: 200, contentType: 'text/event-stream', body: ': heartbeat\n\n',
    });
    if (path === '/assistant/conversations/301/runs') return reply({ items: [], total: 0, offset: 0, limit: 1 });
    if (path === '/assistant/conversations/301/attachments' && method === 'GET') return reply({ items: [], total: 0, offset: 0, limit: 50 });
    if (path === '/assistant/conversations/301/messages') {
      if (method === 'POST') {
        state.sends.push(body);
        return reply({
          message: { id: '2001', seq: 1, role: 'user', content: body.content, references: [], artifacts: [], created_at: time },
          run: { id: '901', conversation_id: '301', status: 'succeeded', phase: 'finished', row_version: 1,
            mode: 'discuss', model_config_id: '71', model_name: '创作协作模型', error: null,
            usage: {}, budget: {}, review: null, created_at: time, updated_at: time, finished_at: time }, cursor: 1,
        }, 201);
      }
      return reply({ items: [], total: 0, offset: 0, limit: 50 });
    }
    return route.fallback();
  });
  return { ...base, state, gateSave: () => {
    saving = deferred(); started = deferred();
    return { started: started.promise, release: saving.resolve };
  } };
}

async function openFirstAsset(page: Page) {
  await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '林晚', exact: true }).click();
  const detail = page.locator('.episode-asset-detail');
  await expect(detail).toBeVisible();
  const expand = detail.getByRole('button', { name: '展开生成设置', exact: true });
  if (await expand.isVisible()) await expand.click();
  await expect(detail.getByRole('textbox', { name: '素材名称', exact: true })).toBeEnabled();
  return detail;
}

test('leaving inline material editing saves the draft before returning to the project', async ({ page }) => {
  const data = await safetyFixture(page);
  const detail = await openFirstAsset(page);
  await detail.getByRole('textbox', { name: '素材名称', exact: true }).fill('保存后离开的角色');
  await page.getByRole('button', { name: '返回项目详情', exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/10$/);
  await expect.poll(() => data.state.updates).toEqual([expect.objectContaining({ id: '501', name: '保存后离开的角色', row_version: '1' })]);
  expect(data.state.assets[0].name).toBe('保存后离开的角色');
  expect(data.errors).toEqual([]);
  expect(data.unexpected).toEqual([]);
});

test('a material version conflict allows leaving and retains the draft on return', async ({ page }) => {
  const data = await safetyFixture(page);
  data.state.conflicts = true;
  const detail = await openFirstAsset(page);
  await detail.getByRole('textbox', { name: '素材名称', exact: true }).fill('发生冲突仍保留的草稿');
  await page.getByRole('button', { name: '返回项目详情', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '未保存的修改', exact: true });
  await expect(confirmation).toHaveCount(0);
  await expect(page).toHaveURL(/\/projects\/10$/);
  await page.goBack();
  await expect(detail.getByRole('textbox', { name: '素材名称', exact: true })).toHaveValue('发生冲突仍保留的草稿');
  expect(data.state.updates).toHaveLength(1);
  expect(data.state.assets[0].name).toBe('林晚');
  expect(data.errors).toEqual([]);
});

test('switching materials retains both drafts and never blocks on conflict', async ({ page }) => {
  const data = await safetyFixture(page);
  const detail = await openFirstAsset(page);
  const name = detail.getByRole('textbox', { name: '素材名称', exact: true });
  await name.fill('先保存再切换的林晚');
  await page.getByRole('button', { name: '沈川', exact: true }).click();
  await expect(name).toHaveValue('沈川');
  await expect.poll(() => data.state.assets[0].name).toBe('先保存再切换的林晚');
  data.state.conflicts = true;
  await name.fill('冲突后保留的沈川');
  await page.getByRole('button', { name: '先保存再切换的林晚', exact: true }).click();
  await expect(name).toHaveValue('先保存再切换的林晚');
  await page.getByRole('button', { name: '冲突后保留的沈川', exact: true }).click();
  await expect(name).toHaveValue('冲突后保留的沈川');
  await expect(page).toHaveURL(url => url.searchParams.get('asset') === '504');
  expect(data.state.assets[1].name).toBe('沈川');
  expect(data.state.updates).toHaveLength(2);
  expect(data.errors).toEqual([]);
});

test('a pending material save retains newer input while switching immediately', async ({ page }) => {
  const data = await safetyFixture(page);
  const detail = await openFirstAsset(page);
  const gate = data.gateSave();
  const name = detail.getByRole('textbox', { name: '素材名称', exact: true });
  await name.fill('先保存林晚');
  await gate.started;
  const next = page.getByRole('button', { name: '沈川', exact: true });
  try {
    await expect(name).toBeEnabled();
    await name.fill('保存回执等待期间的新名称');
    await next.click();
    await expect(name).toHaveValue('沈川');
    await expect(page).toHaveURL(url => url.searchParams.get('asset') === '504');
    await expect(page.getByRole('dialog', { name: '未保存的修改', exact: true })).toHaveCount(0);
  } finally { gate.release(); }
  await expect(name).toHaveValue('沈川');
  await expect.poll(() => data.state.assets[0].name).toBe('保存回执等待期间的新名称');
  expect(data.state.updates).toEqual([
    expect.objectContaining({ id: '501', name: '先保存林晚', row_version: '1' }),
    expect.objectContaining({ id: '501', name: '保存回执等待期间的新名称', row_version: '2' }),
  ]);
  expect(data.errors).toEqual([]);
});

test('material saving conflicts retain editing and the project assistant draft', async ({ page }) => {
  const data = await safetyFixture(page);
  await page.goto(`${root}/assets?assistant=open&conversation=301`);
  const composer = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await expect(composer).toBeVisible();
  const message = '依据最新角色名称，讨论本集素材安排。';
  await composer.fill(message);
  data.state.conflicts = true;
  await page.getByRole('button', { name: '林晚', exact: true }).click();
  const detail = page.locator('.episode-asset-detail');
  await expect(detail).toBeVisible();
  const name = detail.getByRole('textbox', { name: '素材名称', exact: true });
  await name.fill('助手消息发送前的素材草稿');
  await expect(composer).toBeVisible();
  await expect(composer).toHaveValue(message);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(detail.getByRole('button', { name: '核对最新版本', exact: true })).toBeVisible();
  await expect(name).toHaveValue('助手消息发送前的素材草稿');
  expect(data.state.updates).toHaveLength(1);
  expect(data.state.sends).toEqual([]);
  await expect(composer).toBeVisible();
  await expect(composer).toHaveValue(message);
  data.state.conflicts = false;
  await detail.getByRole('button', { name: '核对最新版本', exact: true }).click();
  const review = page.getByRole('dialog', { name: '核对素材版本', exact: true });
  await expect(review).toBeVisible();
  await review.getByRole('button', { name: '已核对，保留草稿继续编辑', exact: true }).click();
  await expect(name).toHaveValue('助手消息发送前的素材草稿');
  expect(data.state.sends).toEqual([]);
  await expect.poll(() => data.state.assets[0].name).toBe('助手消息发送前的素材草稿');
  await expect(composer).toBeVisible();
  await expect(composer).toHaveValue(message);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect.poll(() => data.state.sends.length).toBe(1);
  expect(data.state.updates).toHaveLength(2);
  expect(data.state.assets[0].name).toBe('助手消息发送前的素材草稿');
  expect(data.errors).toEqual([]);
  expect(data.unexpected).toEqual([]);
});

for (const width of [1440, 1280]) {
  test(`sound editing stays in the assembly workspace and saves before leaving at ${width}px`, async ({ page }, info) => {
    const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(`${root}/assembly`);
    const rail = page.locator('.assembly-workspace');
    await rail.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
    const sound = rail.getByRole('region', { name: '原声、字幕和配乐' });
    await expect(sound).toBeVisible();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await sound.getByRole('tab', { name: '配乐与混音' }).click();
    await sound.getByLabel('原视频声音音量', { exact: true }).fill('0.65');
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath(`assembly-sound-expanded-${width}.png`), fullPage: true, animations: 'disabled' });
    await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: /剧本创作/ }).click();
    await expect(page).toHaveURL(url => url.pathname.endsWith('/source'));
    await expect.poll(() => data.requests.filter(request => request.path === `${root}/sound` && request.method === 'PUT').length).toBe(1);
    const saves = data.requests.filter(request => request.path === `${root}/sound` && request.method === 'PUT');
    expect(saves).toHaveLength(1);
    expect(saves[0].body.document.original_volume).toBe(0.65);
    expect(data.errors).toEqual([]);
    expect(data.unexpected).toEqual([]);
  });
}

test('sound version conflicts preserve the inline draft while navigation remains immediate', async ({ page }) => {
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.route(`**/api/v1${root}/sound`, async route => {
    if (route.request().method() !== 'PUT') return route.fallback();
    await route.fulfill({ status: 409, json: { error: { code: 'VERSION_CONFLICT', message: '声音版本已更新，请保留本地草稿。' } } });
  });
  await page.goto(`${root}/assembly`);
  const rail = page.locator('.assembly-workspace');
  await rail.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  const sound = rail.getByRole('region', { name: '原声、字幕和配乐' });
  await sound.getByRole('tab', { name: '配乐与混音' }).click();
  const volume = sound.getByLabel('原视频声音音量', { exact: true });
  await volume.fill('0.65');
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: /剧本创作/ }).click();
  await expect(page).toHaveURL(url => url.pathname.endsWith('/source'));
  await expect(page.getByRole('dialog', { name: /确认|未保存/ })).toHaveCount(0);
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: /成片预览/ }).click();
  await expect(rail).toBeVisible();
  if (!await sound.isVisible()) await rail.getByRole('button', { name: '声音、字幕和配乐', exact: true }).click();
  await sound.getByRole('tab', { name: '配乐与混音' }).click();
  await expect(sound.getByRole('alert').filter({ hasText: '服务端版本已变化' })).toBeVisible();
  await expect(volume).toHaveValue('0.65');
  expect(data.errors).toEqual([]);
  expect(data.unexpected).toEqual([]);
});

test('collapsing desktop material settings by keyboard restores focus to its entry', async ({ page }) => {
  const data = await safetyFixture(page);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto(root + '/assets');
  const detail = page.locator('.episode-asset-detail');
  const settings = detail.locator('.standard-object-settings');
  await expect(settings).toBeVisible();
  const close = settings.getByRole('button', { name: '关闭生成设置', exact: true });
  await close.focus(); await close.press('Enter');
  const entry = detail.getByRole('button', { name: '展开生成设置', exact: true });
  await expect(settings).toBeHidden(); await expect(entry).toBeFocused();
  await entry.press('Enter'); await expect(settings).toBeVisible();
  await expect(detail.getByRole('region', { name: '素材图片预览', exact: true })).toBeVisible();
  expect(data.errors).toEqual([]);
});

test('closing saved material settings restores focus while the refreshed card and deep link retain the asset', async ({ page }) => {
  const data = await safetyFixture(page);
  await page.setViewportSize({ width: 1280, height: 844 });
  const detail = await openFirstAsset(page);
  const name = detail.getByRole('textbox', { name: '素材名称', exact: true });
  await name.fill('保存后的林晚');
  await expect.poll(() => data.state.assets[0].name).toBe('保存后的林晚');
  await expect(page.getByRole('button', { name: '保存后的林晚', exact: true })).toBeVisible();
  await detail.getByRole('button', { name: '关闭生成设置', exact: true }).click();
  const entry = detail.getByRole('button', { name: '展开生成设置', exact: true });
  await expect(entry).toBeFocused();
  await expect(page).toHaveURL(url => url.searchParams.get('asset') === '501');
  await entry.click();
  await expect(name).toHaveValue('保存后的林晚');
  await expect(page).toHaveURL(url => url.searchParams.get('asset') === '501');
  await expect(page.locator('.episode-save-state')).toHaveAttribute('aria-label', '保存状态：已保存');
  await page.reload();
  await expect(name).toHaveValue('保存后的林晚');
  expect(data.state.updates).toHaveLength(1);
  expect(data.errors).toEqual([]);
});

test('collapsing inline sound editing restores keyboard focus to its entry', async ({ page }) => {
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.goto(`${root}/assembly`);
  const rail = page.locator('.assembly-workspace');
  const entry = rail.getByRole('button', { name: '声音、字幕和配乐', exact: true });
  await entry.focus();
  await page.keyboard.press('Enter');
  const close = rail.getByRole('button', { name: '收起声音编辑', exact: true });
  await close.focus();
  await page.keyboard.press('Enter');
  await expect(rail.getByRole('region', { name: '原声、字幕和配乐' })).toHaveCount(0);
  await expect(entry).toBeFocused();
  expect(data.errors).toEqual([]);
});

for (const width of [1440, 1280]) {
  test(`save status stays quiet and opens nonmodal details by keyboard at ${width}px`, async ({ page }, info) => {
    const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${root}/source`);
    await expect(page.getByRole('textbox', { name: '本集剧本正文', exact: true })).toBeEditable();
    const status = page.getByRole('button', { name: '保存状态：已保存', exact: true });
    await expect(status).toBeVisible(); await expect(status).toHaveText('');
    await expect(page.getByRole('button', { name: '确认当前剧本', exact: true })).toHaveCount(0);
    await status.focus(); await page.keyboard.press('Enter');
    await expect(page.locator('.standard-save-details')).toBeVisible();
    await expect.poll(async () => {
      const box = await page.locator('.standard-save-details').boundingBox();
      return !!box && box.x >= 8 && box.x + box.width <= width - 8;
    }).toBe(true);
    await expect(page.getByRole('dialog')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath(`quiet-save-details-${width}.png`), animations: 'disabled' });
    expect(data.errors).toEqual([]);
  });
}
