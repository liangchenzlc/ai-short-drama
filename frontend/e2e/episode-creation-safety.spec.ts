import { expect, test, type Page } from '@playwright/test';
import { fixture, image, root } from './studio-fixture';

const time = '2026-10-03T00:00:00Z';
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}

async function safetyFixture(page: Page) {
  const base = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const asset = (id: string, name: string) => ({
    id, kind: 'character', name, label: '主角', description: '深色风衣，神情坚定。',
    prompt: '人物肖像，深色风衣', tags: [], scene_time: '', state: 'confirmed', row_version: '1',
    media_id: `99${id}`, image: { media_id: `99${id}`, url: image }, reference_count: 1,
    link_id: `60${id}`, position: id === '501' ? 1 : 2,
  });
  const assets = [asset('501', '林晚'), asset('504', '沈川')];
  const conversation = {
    id: '301', project_id: '10', episode_id: '20', title: '素材准备：归来的旅人', row_version: 1,
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
        if (state.conflicts) return reply({ error: { code: 'asset_version_conflict' } }, 409);
        Object.assign(current, body, { row_version: String(BigInt(current.row_version) + 1n) });
      }
      return reply(current);
    }
    if (path === '/agent/status') return reply({ enabled: true, schema_ready: true });
    if (path === '/agent/models') return reply({
      items: [{ id: '71', name: '创作协作模型', model_key: 'fixture-agent', row_version: 1,
        protocol: 'chat', verified: true, tool_calling: true, tool_result_continuation: true,
        streaming: 'verified', preferred: true }], preferred_id: '71',
    });
    if (path === '/agent/conversations') return reply({ items: [conversation], total: 1, offset: 0, limit: 20 });
    if (path === '/agent/conversations/301') return reply(conversation);
    if (path === '/agent/conversations/301/events') return route.fulfill({
      status: 200, contentType: 'text/event-stream', body: ': heartbeat\n\n',
    });
    if (path === '/agent/conversations/301/runs') return reply({ items: [], total: 0, offset: 0, limit: 1 });
    if (path === '/agent/conversations/301/attachments' && method === 'GET') return reply({ items: [], total: 0, offset: 0, limit: 50 });
    if (path === '/agent/conversations/301/messages') {
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

async function openFirstAsset(page: Page, agent = false) {
  await page.goto(`${root}/assets${agent ? '?mode=agent&conversation=301&conversation_stage=assets&conversation_assets=301' : ''}`);
  await page.getByRole('button', { name: '林晚', exact: true }).click();
  const detail = page.locator('.episode-asset-detail');
  await expect(detail).toBeVisible();
  return detail;
}

test('leaving inline material editing saves the draft before returning to the project', async ({ page }) => {
  const data = await safetyFixture(page);
  const detail = await openFirstAsset(page);
  await detail.getByLabel('名称', { exact: true }).fill('保存后离开的角色');
  await page.getByRole('button', { name: '返回项目详情', exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/10$/);
  expect(data.state.updates).toEqual([expect.objectContaining({ id: '501', name: '保存后离开的角色', row_version: '1' })]);
  expect(data.state.assets[0].name).toBe('保存后离开的角色');
  expect(data.errors).toEqual([]);
  expect(data.unexpected).toEqual([]);
});

test('a material version conflict keeps the draft when leaving is cancelled', async ({ page }) => {
  const data = await safetyFixture(page);
  data.state.conflicts = true;
  const detail = await openFirstAsset(page);
  await detail.getByLabel('名称', { exact: true }).fill('发生冲突仍保留的草稿');
  await page.getByRole('button', { name: '返回项目详情', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '未保存的修改', exact: true });
  await expect(confirmation).toBeVisible();
  await confirmation.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${root}/assets$`));
  await expect(detail.getByLabel('名称', { exact: true })).toHaveValue('发生冲突仍保留的草稿');
  expect(data.state.updates).toHaveLength(1);
  expect(data.state.assets[0].name).toBe('林晚');
  expect(data.errors).toEqual([]);
});

test('switching materials explicitly checks an unsaved draft and cancelling retains the current object', async ({ page }) => {
  const data = await safetyFixture(page);
  const detail = await openFirstAsset(page);
  await detail.getByLabel('名称', { exact: true }).fill('尚未保存的林晚');
  await page.getByRole('button', { name: '沈川', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '未保存的修改', exact: true });
  await expect(confirmation).toBeVisible();
  await confirmation.getByRole('button', { name: '取消', exact: true }).click();
  await expect(detail.getByLabel('名称', { exact: true })).toHaveValue('尚未保存的林晚');
  expect(data.state.updates).toEqual([]);
  await page.getByRole('button', { name: '沈川', exact: true }).click();
  await confirmation.getByRole('button', { name: '放弃修改', exact: true }).click();
  await expect(detail.getByLabel('名称', { exact: true })).toHaveValue('沈川');
  expect(data.errors).toEqual([]);
});

test('a pending material save locks object switching until its response has been applied', async ({ page }) => {
  const data = await safetyFixture(page);
  const detail = await openFirstAsset(page);
  await detail.getByLabel('名称', { exact: true }).fill('先保存林晚');
  const gate = data.gateSave();
  await detail.getByRole('button', { name: '保存修改', exact: true }).click();
  await gate.started;
  const next = page.getByRole('button', { name: '沈川', exact: true });
  try {
    await expect(next).toBeDisabled();
    await expect(detail.getByLabel('名称', { exact: true })).toHaveValue('先保存林晚');
    await expect(page.getByRole('dialog', { name: '未保存的修改', exact: true })).toHaveCount(0);
  } finally { gate.release(); }
  await expect(detail.getByText('所有修改已保存', { exact: true })).toBeVisible();
  await expect(next).toBeEnabled();
  await next.click();
  await expect(detail.getByLabel('名称', { exact: true })).toHaveValue('沈川');
  expect(data.state.assets[0].name).toBe('先保存林晚');
  expect(data.state.updates).toHaveLength(1);
  expect(data.errors).toEqual([]);
});

test('returning to Agent preserves its draft and stays in editing when material saving conflicts', async ({ page }) => {
  const data = await safetyFixture(page);
  await page.goto(`${root}/assets?mode=agent&conversation=301&conversation_stage=assets&conversation_assets=301`);
  const composer = page.getByRole('textbox', { name: '创作要求', exact: true });
  await expect(composer).toBeVisible();
  const message = '依据最新角色名称，讨论本集素材安排。';
  await composer.fill(message);
  data.state.conflicts = true;
  await page.getByRole('button', { name: '林晚', exact: true }).click();
  const detail = page.locator('.episode-asset-detail');
  await expect(detail).toBeVisible();
  await detail.getByLabel('名称', { exact: true }).fill('Agent 请求前的素材草稿');
  await expect(composer).toBeHidden();
  await detail.getByRole('button', { name: '返回素材列表', exact: true }).click();
  let confirmation = page.getByRole('dialog', { name: '未保存的修改', exact: true });
  await confirmation.getByRole('button', { name: '取消', exact: true }).click();
  await expect(detail.getByLabel('名称', { exact: true })).toHaveValue('Agent 请求前的素材草稿');
  await detail.getByRole('button', { name: '返回素材列表', exact: true }).click();
  await confirmation.getByRole('button', { name: '保存并返回', exact: true }).click();
  await expect(confirmation).toHaveCount(0);
  await expect(detail).toBeVisible();
  await expect(detail.getByLabel('名称', { exact: true })).toHaveValue('Agent 请求前的素材草稿');
  expect(data.state.updates).toHaveLength(1);
  expect(data.state.sends).toEqual([]);
  await expect(composer).toBeHidden();
  data.state.conflicts = false;
  await detail.getByRole('button', { name: '返回素材列表', exact: true }).click();
  await confirmation.getByRole('button', { name: '保存并返回', exact: true }).click();
  await expect(detail).toHaveCount(0);
  await expect(composer).toBeVisible();
  await expect(composer).toHaveValue(message);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect.poll(() => data.state.sends.length).toBe(1);
  expect(data.state.updates).toHaveLength(2);
  expect(data.state.assets[0].name).toBe('Agent 请求前的素材草稿');
  expect(data.errors).toEqual([]);
  expect(data.unexpected).toEqual([]);
});

for (const width of [1440, 390]) {
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
    await page.getByRole('navigation', { name: '分集制作流程' }).getByRole('button', { name: /小说改编/ }).click();
    await expect(page).toHaveURL(url => url.pathname.endsWith('/source'));
    const saves = data.requests.filter(request => request.path === `${root}/sound` && request.method === 'PUT');
    expect(saves).toHaveLength(1);
    expect(saves[0].body.document.original_volume).toBe(0.65);
    expect(data.errors).toEqual([]);
    expect(data.unexpected).toEqual([]);
  });
}

test('sound version conflicts preserve the inline draft and block navigation', async ({ page }) => {
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
  await page.getByRole('navigation', { name: '分集制作流程' }).getByRole('button', { name: /小说改编/ }).click();
  await expect(sound.getByRole('alert').filter({ hasText: '数据已被修改' })).toBeVisible();
  await expect(page).toHaveURL(url => url.pathname.endsWith('/assembly'));
  await expect(volume).toHaveValue('0.65');
  expect(data.errors).toEqual([]);
  expect(data.unexpected).toEqual([]);
});

test('closing inline material editing restores keyboard focus to its entry', async ({ page }) => {
  const data = await safetyFixture(page);
  await page.goto(`${root}/assets`);
  const entry = page.getByRole('button', { name: '林晚', exact: true });
  await entry.focus();
  await page.keyboard.press('Enter');
  const close = page.locator('.episode-asset-detail').getByRole('button', { name: '返回素材列表', exact: true });
  await close.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.episode-asset-detail')).toHaveCount(0);
  await expect(entry).toBeFocused();
  expect(data.errors).toEqual([]);
});

test('closing a saved inline material restores focus after its card reloads', async ({ page }) => {
  const data = await safetyFixture(page);
  const detail = await openFirstAsset(page);
  await detail.getByLabel('名称', { exact: true }).fill('保存后的林晚');
  await detail.getByRole('button', { name: '保存修改', exact: true }).click();
  await expect(detail.getByText('所有修改已保存', { exact: true })).toBeVisible();
  const entry = page.getByRole('button', { name: '保存后的林晚', exact: true });
  await expect(entry).toBeVisible();
  await detail.getByRole('button', { name: '返回素材列表', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(detail).toHaveCount(0);
  await expect(entry).toBeFocused();
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
