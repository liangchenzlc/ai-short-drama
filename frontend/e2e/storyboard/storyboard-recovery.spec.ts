import { expect, test, type Page } from '@playwright/test';
import { fixture, root, image } from '../support/studio-fixture';

const origin = () => new URL(test.info().project.use.baseURL!).origin;
const setup = (page: Page) => fixture(page, true, origin());
const panel = (page: Page) => page.locator('.standard-storyboard-panel');

for (const position of [20, 21, 50, 80]) test(`restores stable shot ${position} with separate detail and saves it`, async ({ page }) => {
  const state = await setup(page); const id = String(100 + position);
  await page.goto(`${root}/storyboard?shot=${id}`);
  await expect(page.locator('.storyboard-shot-card')).toHaveCount(20);
  const input = panel(page).getByRole('textbox', { name: `分镜 ${position} 脚本`, exact: true });
  await expect(input).toBeEditable(); await input.fill(`恢复第 ${position} 镜并保存`);
  await expect.poll(() => state.shots[position - 1].script).toBe(`恢复第 ${position} 镜并保存`);
  if (position > 20) expect(state.requests.some(request => request.path === `${root}/shots/${id}` && request.method === 'GET')).toBe(true);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('asset list failure leaves shot editing readable and retry keeps its draft', async ({ page }) => {
  const state = await setup(page); let fail = true;
  await page.route(`**/api/v1${root}/assets?**`, route => fail ? route.fulfill({ status: 503, json: { error: { code: 'OFFLINE', message: '素材读取暂时失败。' } } }) : route.fallback());
  await page.goto(`${root}/storyboard`);
  await expect(page.locator('.storyboard-shot-card')).toHaveCount(20);
  await page.locator('.storyboard-summary').first().click();
  const input = panel(page).getByRole('textbox', { name: '分镜 1 脚本', exact: true });
  await input.fill('素材失败时保留镜头输入');
  await expect(panel(page).getByRole('combobox', { name: '关联角色' })).toHaveCount(0);
  const assetIds = [...state.shots[0].asset_ids];
  fail = false; await page.getByRole('button', { name: '重新加载关联素材', exact: true }).click();
  await expect(input).toHaveValue('素材失败时保留镜头输入');
  await expect.poll(() => state.shots[0].script).toBe('素材失败时保留镜头输入');
  expect(state.shots[0].asset_ids).toEqual(assetIds);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('seventeen combined image references block submission and removing one allows sixteen', async ({ page }) => {
  const state = await setup(page);
  state.creationDrafts.set('101:storyboard_image', { id: '95001', project_id: '10', episode_id: '20', object_id: '101', purpose: 'storyboard_image', model_config_id: '77', input_text: '', image_settings: { aspect: '16:9', resolution: '2K', count: 1 }, video_settings: null, reference_media_ids: Array.from({ length: 16 }, (_, index) => String(2000 + index)), reference_images: Array.from({ length: 16 }, (_, index) => ({ media_id: String(2000 + index), url: image })), row_version: '1', draft_version: '1', source_version: '1' });
  await page.goto(`${root}/storyboard`); await page.locator('.storyboard-summary').first().click();
  await panel(page).getByRole('tab', { name: '分镜图', exact: true }).click();
  await expect(panel(page).locator('.standard-reference-count')).toHaveText('17 / 16');
  await expect(panel(page).getByRole('button', { name: '生成图片', exact: true })).toBeDisabled();
  expect(state.requests.filter(request => request.path === '/ai/generations/image' && request.method === 'POST')).toHaveLength(0);
  await panel(page).getByRole('button', { name: /^移除个人参考图片 / }).first().click();
  await expect(panel(page).locator('.standard-reference-count')).toHaveText('16 / 16');
  await expect(panel(page).getByRole('button', { name: '生成图片', exact: true })).toBeEnabled();
  await expect.poll(() => state.creationDrafts.get('101:storyboard_image').reference_media_ids.length).toBe(15);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('history displays failure instead of empty and then reviews complete candidate fields', async ({ page }) => {
  const state = await setup(page); let fail = true;
  await page.route('**/api/v1/ai/generations?**', route => {
    if (new URL(route.request().url()).searchParams.get('source_scene') === 'script_shots' && fail) return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } });
    return route.fallback();
  });
  await page.goto(`${root}/storyboard`); await page.getByRole('button', { name: '分镜生成记录', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '分镜生成记录', exact: true });
  await expect(dialog.getByRole('alert')).toBeVisible();
  await expect(dialog.getByText('暂无分镜生成记录，确认剧本后开始生成。', { exact: true })).toHaveCount(0);
  fail = false; await dialog.getByRole('button', { name: '重新加载生成记录', exact: true }).click();
  await dialog.getByRole('button', { name: '查看分镜', exact: true }).first().click();
  await expect(dialog.getByText('整批计划时长 165 秒，采用会包含全部 55 个镜头。', { exact: true })).toBeVisible();
  await expect(dialog.getByText('关联素材：角色 · 林晚', { exact: true }).first()).toBeVisible();
  await expect(dialog.getByText('计划时长 3 秒', { exact: true }).first()).toBeVisible();
  await expect(dialog.getByText('来源摘录：林晚走进车站，抬头寻找站台。', { exact: true }).first()).toBeVisible();
  await expect(dialog.getByText('情节节点：寻找信件主人', { exact: true }).first()).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('shot menu moves across the page boundary', async ({ page }) => {
  const state = await setup(page); let version = 1n;
  let releaseMove!: () => void; const pendingMove = new Promise<void>(resolve => { releaseMove = resolve; });
  await page.route(`**/api/v1${root}/shots**`, async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname.slice('/api/v1'.length);
    if (path.endsWith('/dialogue')) return route.fallback();
    const active = () => state.shots.filter(shot => !shot.deleted_at).sort((a, b) => a.position - b.position);
    if (path === `${root}/shots`) {
      const offset = Number(url.searchParams.get('offset') ?? '0');
      return route.fulfill({ json: { items: active().slice(offset, offset + 20), total: active().length, episode_id: '20', storyboard_version: String(version), offset, limit: 20 } });
    }
    const id = path.slice(root.length).split('/')[2]; const shot = state.shots.find(shot => shot.id === id)!;
    if (path.endsWith('/move')) {
      await pendingMove;
      const rows = active(); const index = rows.indexOf(shot); const other = rows[index + request.postDataJSON().direction];
      [shot.position, other.position] = [other.position, shot.position]; version++;
      return route.fulfill({ json: { storyboard_version: String(version) } });
    }
    return route.fulfill({ json: { shot, storyboard_version: String(version) } });
  });
  await page.goto(`${root}/storyboard?shot=120`);
  await expect(panel(page).getByRole('textbox', { name: '分镜 20 脚本', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '分镜生成记录', exact: true }).click();
  await page.getByRole('dialog', { name: '分镜生成记录', exact: true }).getByRole('button', { name: '分镜 20 更多操作', exact: true }).click(); await page.getByRole('menuitem', { name: '下移', exact: true }).click();
  await expect(panel(page).getByRole('textbox', { name: '分镜 20 脚本', exact: true })).toBeDisabled();
  await expect(panel(page).getByRole('textbox', { name: '镜头名称', exact: true })).toBeDisabled();
  releaseMove();
  await expect(panel(page).getByRole('textbox', { name: '分镜 21 脚本', exact: true })).toBeVisible();
  await expect(page).toHaveURL(url => url.searchParams.get('shot') === '120');
  await expect(page.locator('.standard-storyboard-list h3')).toContainText('80');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

async function deletionFixture(page: Page, state: Awaited<ReturnType<typeof setup>>, options: { beforeDelete?: () => Promise<void>; fail?: () => number | undefined; beforePatch?: () => Promise<void>; beforeReload?: () => Promise<void>; failReload?: () => boolean } = {}) {
  let version = '1';
  let deleted = false;
  const calls: { method: string; id: string; body: any }[] = [];
  await page.route(`**/api/v1${root}/shots**`, async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.slice('/api/v1'.length), method = request.method();
    const active = () => state.shots.filter(shot => !shot.deleted_at).sort((a, b) => a.position - b.position);
    if (path === `${root}/shots` && method === 'GET') {
      if (deleted) {
        await options.beforeReload?.();
        if (options.failReload?.()) return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } });
      }
      const offset = Number(url.searchParams.get('offset') ?? '0'), limit = Number(url.searchParams.get('limit') ?? '20');
      return route.fulfill({ json: { items: active().slice(offset, offset + limit), total: active().length, storyboard_version: version, offset, limit } });
    }
    const match = path.match(new RegExp(`^${root}/shots/(\\d+)$`));
    if (!match) return route.fallback();
    const shot = state.shots.find(shot => shot.id === match[1]);
    if (!shot) return route.fulfill({ status: 404, json: { error: { code: 'not_found' } } });
    if (method === 'PATCH' || method === 'DELETE') calls.push({ method, id: shot.id, body: request.postDataJSON() });
    if (method === 'PATCH') {
      await options.beforePatch?.();
      const body = request.postDataJSON();
      if (body.row_version !== shot.row_version) return route.fulfill({ status: 409, json: { error: { code: 'version_conflict' } } });
      Object.assign(shot, body, { row_version: String(BigInt(shot.row_version) + 1n) }); version = String(BigInt(version) + 1n);
    }
    if (method === 'DELETE') {
      await options.beforeDelete?.();
      const status = options.fail?.(), body = request.postDataJSON();
      if (status) return route.fulfill({ status, json: { error: { code: status === 409 ? 'storyboard_version_conflict' : 'OFFLINE' } } });
      if (body.row_version !== shot.row_version || body.storyboard_version !== version) return route.fulfill({ status: 409, json: { error: { code: 'storyboard_version_conflict' } } });
      shot.deleted_at = '2026-10-21T06:00:00Z';
      for (const item of active()) if (item.position > shot.position) { item.position--; item.row_version = String(BigInt(item.row_version) + 1n); }
      version = String(BigInt(version) + 1n); deleted = true;
      return route.fulfill({ json: { storyboard_version: version, deleted_id: shot.id } });
    }
    return route.fulfill({ json: { shot, storyboard_version: version } });
  });
  return calls;
}

test('card deletion menu preserves selection and cancelling keeps the shot', async ({ page }) => {
  const state = await setup(page), calls = await deletionFixture(page, state);
  await page.goto(`${root}/storyboard?shot=101`);
  const card = page.locator('#storyboard-shot-102');
  const more = card.getByRole('button', { name: '分镜 02 卡片更多操作', exact: true });
  await more.focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('menuitem', { name: '删除分镜', exact: true })).toBeVisible();
  await expect(page).toHaveURL(url => url.searchParams.get('shot') === '101');
  await page.keyboard.press('Escape'); await expect(more).toBeFocused();
  await more.click(); await page.getByRole('menuitem', { name: '删除分镜', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '删除分镜', exact: true });
  await expect(confirmation).toContainText('站台镜头 2');
  await confirmation.getByRole('button', { name: '取消', exact: true }).click();
  await expect(card).toBeVisible(); expect(calls.filter(call => call.method === 'DELETE')).toHaveLength(0);
  await expect(page).toHaveURL(url => url.searchParams.get('shot') === '101');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('card deletion saves pending input first and selects the next shot without late autosave', async ({ page }) => {
  const state = await setup(page);
  let releasePatch!: () => void, releaseDelete!: () => void;
  const patch = new Promise<void>(resolve => { releasePatch = resolve; }), deletion = new Promise<void>(resolve => { releaseDelete = resolve; });
  const calls = await deletionFixture(page, state, { beforePatch: () => patch, beforeDelete: () => deletion });
  await page.goto(`${root}/storyboard?shot=101`);
  const input = panel(page).getByRole('textbox', { name: '分镜 1 脚本', exact: true });
  await input.fill('删除前保存的镜头输入');
  await expect.poll(() => calls.filter(call => call.method === 'PATCH').length).toBe(1);
  await page.locator('#storyboard-shot-101').getByRole('button', { name: '分镜 01 卡片更多操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除分镜', exact: true }).click();
  await page.getByRole('dialog', { name: '删除分镜', exact: true }).getByRole('button', { name: '删除分镜', exact: true }).click();
  expect(calls.filter(call => call.method === 'DELETE')).toHaveLength(0);
  releasePatch(); await expect.poll(() => calls.filter(call => call.method === 'DELETE').length).toBe(1);
  expect(calls.find(call => call.method === 'DELETE')?.body).toEqual({ row_version: '2', storyboard_version: '2' });
  await expect(input).toBeDisabled(); await expect(page.locator('#storyboard-shot-101')).toHaveAttribute('aria-busy', 'true');
  releaseDelete(); await expect(page.locator('#storyboard-shot-101')).toHaveCount(0);
  await expect(page).toHaveURL(url => url.searchParams.get('shot') === '102');
  await expect(panel(page).getByRole('textbox', { name: '分镜 1 脚本', exact: true })).toHaveValue(state.shots[1].script);
  await panel(page).getByRole('textbox', { name: '镜头名称', exact: true }).fill('删除后的相邻分镜');
  await expect.poll(() => state.shots[1].title).toBe('删除后的相邻分镜');
  expect(calls.filter(call => call.id === '101' && call.method === 'PATCH')).toHaveLength(1);
  expect(state.shots[0].script).toBe('删除前保存的镜头输入');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

for (const status of [409, 503]) test(`card deletion ${status} retains card and input with persistent recovery`, async ({ page }) => {
  const state = await setup(page), calls = await deletionFixture(page, state, { fail: () => status });
  await page.goto(`${root}/storyboard?shot=101`);
  const input = panel(page).getByRole('textbox', { name: '分镜 1 脚本', exact: true });
  await input.fill('删除失败也保留的脚本');
  await expect.poll(() => state.shots[0].script).toBe('删除失败也保留的脚本');
  await page.locator('#storyboard-shot-101').getByRole('button', { name: '分镜 01 卡片更多操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除分镜', exact: true }).click();
  await page.getByRole('dialog', { name: '删除分镜', exact: true }).getByRole('button', { name: '删除分镜', exact: true }).click();
  await expect(page.getByText(status === 409 ? '分镜版本已变化，未执行删除。当前卡片和输入已保留。' : '删除结果未确认，当前卡片和输入已保留。请重新加载列表核对。', { exact: true })).toBeVisible();
  await expect(page.locator('#storyboard-shot-101')).toBeVisible(); await expect(input).toHaveValue('删除失败也保留的脚本');
  await expect(input).toBeEditable(); expect(calls.filter(call => call.method === 'DELETE')).toHaveLength(1);
  await page.getByRole('button', { name: '关闭提示', exact: true }).last().click();
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  await expect(page.locator('.studio-feedback-details').getByRole('button', { name: '重新加载列表核对', exact: true })).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('deleting another card preserves the current shot and deleting the last shot clears its route', async ({ page }) => {
  const state = await setup(page); state.shots.splice(2);
  const calls = await deletionFixture(page, state);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${root}/storyboard?shot=101`);
  for (const [id, position] of [['102', '02'], ['101', '01']]) {
    const more = page.locator(`#storyboard-shot-${id}`).getByRole('button', { name: `分镜 ${position} 卡片更多操作`, exact: true });
    await more.scrollIntoViewIfNeeded(); const box = (await more.boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(44); expect(box.height).toBeGreaterThanOrEqual(44);
    await more.click(); await page.getByRole('menuitem', { name: '删除分镜', exact: true }).click();
    await page.getByRole('dialog', { name: '删除分镜', exact: true }).getByRole('button', { name: '删除分镜', exact: true }).click();
    await expect(page.locator(`#storyboard-shot-${id}`)).toHaveCount(0);
    if (id === '102') await expect(page).toHaveURL(url => url.searchParams.get('shot') === '101');
  }
  await expect(page).toHaveURL(url => !url.searchParams.has('shot'));
  await expect(page.getByRole('button', { name: '新增第一个分镜', exact: true })).toBeVisible();
  expect(calls.filter(call => call.method === 'DELETE')).toHaveLength(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('deletion waits for renumbered list and off-page selection versions before editing resumes', async ({ page }) => {
  const state = await setup(page);
  let release!: () => void;
  const reloading = new Promise<void>(resolve => { release = resolve; });
  const calls = await deletionFixture(page, state, { beforeReload: () => reloading });
  await page.goto(`${root}/storyboard?shot=150`);
  await expect(panel(page).getByRole('textbox', { name: '分镜 50 脚本', exact: true })).toBeVisible();
  await page.locator('#storyboard-shot-101').getByRole('button', { name: '分镜 01 卡片更多操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除分镜', exact: true }).click();
  await page.getByRole('dialog', { name: '删除分镜', exact: true }).getByRole('button', { name: '删除分镜', exact: true }).click();
  await expect(page.getByRole('status', { name: '正在加载分镜', exact: true })).toBeVisible();
  await expect(panel(page).getByRole('textbox', { name: '镜头名称', exact: true })).toHaveCount(0);
  await expect(page).toHaveURL(url => url.searchParams.get('shot') === '150');
  release();
  await expect(panel(page).getByRole('textbox', { name: '分镜 49 脚本', exact: true })).toHaveValue(state.shots[49].script);
  await panel(page).getByRole('textbox', { name: '镜头名称', exact: true }).fill('压紧编号后继续编辑');
  await expect.poll(() => state.shots[49].title).toBe('压紧编号后继续编辑');
  expect(calls.find(call => call.method === 'PATCH' && call.id === '150')?.body.row_version).toBe('2');
  await expect(page.locator('#storyboard-shot-102').getByRole('button', { name: '选择分镜 01', exact: true })).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('an accepted deletion with a failed list reload blocks stale editing and retries only reads', async ({ page }) => {
  const state = await setup(page);
  let failReload = true;
  const calls = await deletionFixture(page, state, { failReload: () => failReload });
  let release!: () => void, detailReads = 0;
  const staleDetail = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/api/v1${root}/shots/150`, async route => {
    if (route.request().method() !== 'GET' || detailReads++ > 0) return route.fallback();
    const shot = structuredClone(state.shots[49]);
    await staleDetail;
    return route.fulfill({ json: { shot, storyboard_version: '1' } });
  });
  await page.goto(`${root}/storyboard?shot=150`);
  await page.locator('#storyboard-shot-101').getByRole('button', { name: '分镜 01 卡片更多操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除分镜', exact: true }).click();
  await page.getByRole('dialog', { name: '删除分镜', exact: true }).getByRole('button', { name: '删除分镜', exact: true }).click();
  await expect(page.getByText('分镜已删除，列表尚未刷新。请重新加载列表后继续编辑。', { exact: true })).toBeVisible();
  release();
  await expect(panel(page).locator('.studio-empty')).toHaveText('选择左侧镜头开始制作');
  await expect(panel(page).getByRole('textbox', { name: '镜头名称', exact: true })).toHaveCount(0);
  expect(calls.filter(call => call.method === 'DELETE')).toHaveLength(1);
  failReload = false;
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  await page.locator('.studio-feedback-details').getByRole('button', { name: '重新加载列表核对', exact: true }).click();
  await page.keyboard.press('Escape');
  await expect(page.locator('.storyboard-shot-card')).toHaveCount(20);
  await expect(panel(page).getByRole('textbox', { name: '分镜 49 脚本', exact: true })).toBeEditable();
  expect(calls.filter(call => call.method === 'DELETE')).toHaveLength(1);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('creating after eighty shots selects the returned shot and scrolls its card into view', async ({ page }) => {
  const state = await setup(page); let version = '1'; const offsets: number[] = [];
  await page.route(`**/api/v1${root}/shots?**`, async route => {
    const request = route.request(); const query = new URL(request.url()).searchParams;
    const offset = Number(query.get('offset') ?? '0'); offsets.push(offset);
    return route.fulfill({ json: { items: state.shots.slice(offset, offset + 20), total: state.shots.length, episode_id: '20', storyboard_version: version, offset, limit: 20 } });
  });
  await page.route(`**/api/v1${root}/shots`, async route => {
    if (route.request().method() !== 'POST') return route.fallback();
    const shot = { ...state.shots[0], ...route.request().postDataJSON(), id: '181', position: 81, image: null, asset_ids: [], script: '' };
    state.shots.push(shot); version = '2';
    return route.fulfill({ status: 201, json: { shot, storyboard_version: version } });
  });
  await page.goto(`${root}/storyboard`); await expect(page.locator('.storyboard-shot-card')).toHaveCount(20);
  await page.getByRole('button', { name: '新增分镜', exact: true }).click();
  await expect(panel(page).getByRole('textbox', { name: '分镜 81 脚本', exact: true })).toBeVisible();
  const card = page.locator('#storyboard-shot-181');
  await expect(card.getByRole('button', { name: '选择分镜 81', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(card).toBeInViewport();
  await expect(page).toHaveURL(url => url.searchParams.get('shot') === '181');
  expect(offsets).toContain(80); expect(offsets).not.toContain(21);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('replacing all shots clears the previous stable shot scope after adoption', async ({ page }) => {
  const state = await setup(page);
  let applies = 0;
  await page.route(`**/api/v1${root}/storyboard-results/*/apply`, async route => {
    expect(route.request().postDataJSON()).toMatchObject({ mode: 'replace', confirm_replace: true });
    applies++;
    if (applies === 1) return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE', message: '分镜采用暂时失败，当前结果已保留。' } } });
    state.shots.splice(0, state.shots.length, { ...state.shots[0], id: '301', position: 1, title: '采用后的新分镜', script: '采用后的新分镜' });
    return route.fallback();
  });
  await page.goto(`${root}/storyboard?shot=150`);
  await expect(panel(page).getByRole('textbox', { name: '分镜 50 脚本', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '分镜生成记录', exact: true }).click();
  const history = page.getByRole('dialog', { name: '分镜生成记录', exact: true });
  await history.getByRole('button', { name: '查看分镜', exact: true }).first().click();
  const preview = history.locator('.storyboard-result-preview');
  await expect(preview.getByRole('heading', { name: '分镜预览（共 55 镜）' })).toBeVisible();
  const previewBox = await preview.boundingBox();
  await history.getByRole('button', { name: '替换当前分镜', exact: true }).click();
  await page.getByRole('button', { name: '确认继续', exact: true }).click();
  await expect(history.getByRole('alert')).toHaveText(/服务暂时不可用/);
  expect(await preview.boundingBox()).toEqual(previewBox);
  await history.getByRole('button', { name: '关闭提示', exact: true }).click();
  await history.getByRole('button', { name: /^提示详情：/ }).click();
  await expect(history.locator('.studio-feedback-details')).toContainText('服务暂时不可用');
  await page.keyboard.press('Escape');
  await expect(history.locator('.studio-feedback-popup')).toBeHidden();
  await expect(history).toBeVisible();
  await history.getByRole('button', { name: '替换当前分镜', exact: true }).click();
  await page.getByRole('button', { name: '确认继续', exact: true }).click();
  await expect(history).toHaveCount(0);
  await expect(page).toHaveURL(url => !url.searchParams.has('shot'));
  await expect(page.locator('.storyboard-shot-card')).toHaveCount(1);
  await expect(page.locator('.storyboard-shot-card')).toContainText('采用后的新分镜');
  await expect(page.locator('.storyboard-summary[aria-pressed="true"]')).toHaveCount(0);
  expect(applies).toBe(2);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('history shows a pending response before the empty state', async ({ page }) => {
  const state = await setup(page); let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/ai/generations?**', async route => {
    if (new URL(route.request().url()).searchParams.get('source_scene') !== 'script_shots') return route.fallback();
    await pending; return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 20 } });
  });
  await page.goto(`${root}/storyboard`); await page.getByRole('button', { name: '分镜生成记录', exact: true }).click();
  const history = page.getByRole('dialog', { name: '分镜生成记录', exact: true });
  await expect(history.getByText('正在加载分镜生成记录…', { exact: true })).toBeVisible();
  await expect(history.getByText('暂无分镜生成记录，编写或采用剧本后开始生成。', { exact: true })).toHaveCount(0);
  release(); await expect(history.getByText('暂无分镜生成记录，编写或采用剧本后开始生成。', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('off-page autosave accepts its version before the initial list response arrives', async ({ page }) => {
  const state = await setup(page); let release!: () => void; let reads = 0; let version = '1'; const savedVersions: string[] = [];
  const pending = new Promise<void>(resolve => { release = resolve; });
  const staleRows = structuredClone(state.shots.slice(0, 20));
  await page.route(`**/api/v1${root}/shots?**`, async route => {
    if (new URL(route.request().url()).searchParams.get('limit') === '100') return route.fulfill({ json: { items: state.shots, total: 80, episode_id: '20', storyboard_version: version, offset: 0, limit: 100 } });
    reads++;
    if (!savedVersions.length) { await pending; return route.fulfill({ json: { items: staleRows, total: 80, episode_id: '20', storyboard_version: '1', offset: 0, limit: 20 } }); }
    return route.fulfill({ json: { items: state.shots.slice(0, 20), total: 80, episode_id: '20', storyboard_version: version, offset: 0, limit: 20 } });
  });
  await page.route(`**/api/v1${root}/shots/150`, async route => {
    const shot = state.shots[49];
    if (route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON(); savedVersions.push(body.row_version);
      if (body.row_version !== shot.row_version) return route.fulfill({ status: 409, json: { error: { code: 'version_conflict' } } });
      version = String(BigInt(version) + 1n); Object.assign(shot, body, { row_version: version, context_hash: version.padStart(64, 'a') });
    }
    return route.fulfill({ json: { shot, storyboard_version: version } });
  });
  await page.goto(`${root}/storyboard?agent_subject_storyboard=150`);
  const input = panel(page).getByRole('textbox', { name: '分镜 50 脚本', exact: true });
  await expect(page).toHaveURL(url => url.searchParams.get('shot') === '150' && !url.searchParams.has('agent_subject_storyboard'));
  await expect(input).toBeEditable(); await expect(page.locator('.storyboard-shot-card')).toHaveCount(0);
  await input.fill('列表迟到前保存第一版');
  await expect.poll(() => state.shots[49].script).toBe('列表迟到前保存第一版');
  await expect(page.locator('.storyboard-shot-card')).toHaveCount(20);
  release(); await input.fill('使用已接收版本保存第二版');
  await expect.poll(() => state.shots[49].script).toBe('使用已接收版本保存第二版');
  expect(savedVersions).toEqual(['1', '2']);
  await expect(input).toHaveValue('使用已接收版本保存第二版');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('shot video editing exposes dialogue review and a failed save retains its draft', async ({ page }) => {
  const state = await setup(page);
  let fail = true; const dialogue = { row_version: 1, mode: 'native', document: { lines: [], reviewed: false }, characters: [{ id: '501', name: '林晚' }], voices: [] };
  await page.route(`**/api/v1${root}/shots/101/dialogue`, route => {
    if (route.request().method() === 'PUT') {
      if (fail) return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } });
      Object.assign(dialogue, { document: route.request().postDataJSON().document, row_version: 2 });
    }
    return route.fulfill({ json: dialogue });
  });
  await page.goto(`${root}/storyboard?shot=101`);
  await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).click();
  await panel(page).locator('.storyboard-dialogue-settings > summary').click();
  await panel(page).getByRole('button', { name: '编辑并确认对白', exact: true }).click();
  const review = page.getByRole('dialog', { name: '分镜对白与声音表演', exact: true });
  const save = review.getByRole('button', { name: /保存分镜对白$/ });
  await review.getByRole('checkbox').check(); await expect(save).toBeEnabled(); await save.click();
  await expect(review.getByRole('alert')).toBeVisible(); await expect(review.getByRole('checkbox')).toBeChecked();
  fail = false; await expect(save).toBeEnabled(); await save.click();
  await expect(review).toHaveCount(0); await expect(panel(page).getByText('0 句已确认对白', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
