import { expect, test, type Page } from '@playwright/test';
import { fixture, root, image } from './studio-fixture';

const origin = () => new URL(test.info().project.use.baseURL!).origin;
const setup = (page: Page) => fixture(page, true, origin());
const panel = (page: Page) => page.getByRole('complementary', { name: '分镜媒体设置', exact: true });

async function scopedAgent(page: Page) {
  const conversations: any[] = [];
  await page.route('**/api/v1/agent/**', async route => {
    const request = route.request(); const path = new URL(request.url()).pathname.slice('/api/v1/agent'.length);
    const reply = (json: any) => route.fulfill({ json });
    if (path === '/status') return reply({ enabled: true, schema_ready: true });
    if (path === '/models') return reply({ items: [], preferred_id: null });
    if (path === '/skills') return reply({ items: [], total: 0, offset: 0, limit: 50 });
    if (path === '/conversations/resolve') {
      const scope = request.postDataJSON();
      let conversation = conversations.find(item => ['stage', 'subject_type', 'subject_id', 'task_type'].every(key => item[key] === scope[key]));
      if (!conversation) { conversation = { ...scope, id: String(301 + conversations.length), project_id: '10', episode_id: '20', scope_version: 1, row_version: '1', archived: false, last_run_status: null, created_at: '2026-10-04T00:00:00Z', updated_at: '2026-10-04T00:00:00Z' }; conversations.push(conversation); }
      return reply(conversation);
    }
    if (path === '/conversations') return reply({ items: conversations, total: conversations.length, offset: 0, limit: 20 });
    if (/^\/conversations\/\d+$/.test(path)) return reply(conversations.find(item => item.id === path.split('/').pop()));
    if (path.endsWith('/state')) return reply({ conversation_id: path.split('/')[2], cursor: 0, active_run: null, queued_runs: [] });
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (/\/(messages|attachments|runs)$/.test(path)) return reply({ items: [], total: 0, offset: 0, limit: 50 });
    return route.fallback();
  });
  return conversations;
}

for (const position of [21, 50, 80]) test(`restores off-page shot ${position} with separate detail and saves it`, async ({ page }) => {
  const state = await setup(page); const id = String(100 + position);
  await page.goto(`${root}/storyboard?agent_subject_storyboard=${id}`);
  await expect(page.locator('.storyboard-shot-card')).toHaveCount(20);
  const input = panel(page).getByRole('textbox', { name: `分镜 ${position} 脚本`, exact: true });
  await expect(input).toBeEditable(); await input.fill(`恢复第 ${position} 镜并保存`);
  await expect.poll(() => state.shots[position - 1].script).toBe(`恢复第 ${position} 镜并保存`);
  expect(state.requests.some(request => request.path === `${root}/shots/${id}` && request.method === 'GET')).toBe(true);
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
  await expect(panel(page).getByRole('combobox', { name: '关联角色' })).toBeDisabled();
  fail = false; await page.getByRole('button', { name: '重新加载关联素材', exact: true }).click();
  await expect(panel(page).getByRole('combobox', { name: '关联角色' })).toBeEnabled();
  await expect(input).toHaveValue('素材失败时保留镜头输入');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('seventeen combined image references block submission and removing one allows sixteen', async ({ page }) => {
  const state = await setup(page);
  state.referenceState['shot/101'] = Array.from({ length: 16 }, (_, index) => ({ media_id: String(2000 + index), name: `manual ${index}`, url: image }));
  await page.goto(`${root}/storyboard`); await page.locator('.storyboard-summary').first().click();
  await panel(page).getByRole('tab', { name: '分镜图', exact: true }).click();
  await expect(panel(page).getByText(/去重合计 17\/16 张/)).toBeVisible();
  await expect(panel(page).getByRole('button', { name: '生成图片', exact: true })).toBeDisabled();
  expect(state.requests.filter(request => request.path === '/ai/generations/image' && request.method === 'POST')).toHaveLength(0);
  await panel(page).getByRole('button', { name: '移除', exact: true }).first().click();
  await expect(panel(page).getByText(/去重合计 16\/16 张/)).toBeVisible();
  await expect(panel(page).getByRole('button', { name: '生成图片', exact: true })).toBeEnabled();
  expect(state.referenceState['shot/101']).toHaveLength(15);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('history displays failure instead of empty and then reviews complete candidate fields', async ({ page }) => {
  const state = await setup(page); let fail = true;
  await page.route('**/api/v1/ai/generations?**', route => {
    if (new URL(route.request().url()).searchParams.get('source_scene') === 'script_shots' && fail) return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } });
    return route.fallback();
  });
  await page.goto(`${root}/storyboard`); await page.getByRole('button', { name: '历史记录', exact: true }).click();
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

test('shot menu moves across the page boundary and archive clears the scope', async ({ page }) => {
  const state = await setup(page); let version = 1n;
  let releaseMove!: () => void; const pendingMove = new Promise<void>(resolve => { releaseMove = resolve; });
  await page.route(`**/api/v1${root}/shots**`, async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname.slice('/api/v1'.length); const method = request.method();
    if (path.endsWith('/dialogue')) return route.fallback();
    const active = () => state.shots.filter(shot => !shot.deleted_at).sort((a, b) => a.position - b.position);
    if (path === `${root}/shots`) {
      const offset = Number(url.searchParams.get('offset') ?? '0');
      return route.fulfill({ json: { items: active().slice(offset, offset + 20), total: active().length, episode_id: '20', storyboard_version: String(version), offset, limit: 20 } });
    }
    const id = path.split('/')[6]; const shot = state.shots.find(shot => shot.id === id)!;
    if (path.endsWith('/move')) {
      await pendingMove;
      const rows = active(); const index = rows.indexOf(shot); const other = rows[index + request.postDataJSON().direction];
      [shot.position, other.position] = [other.position, shot.position]; version++;
      return route.fulfill({ json: { storyboard_version: String(version) } });
    }
    if (method === 'DELETE') { shot.deleted_at = '2026-10-04T00:00:00Z'; for (const item of active()) if (item.position > shot.position) item.position--; version++; return route.fulfill({ status: 204 }); }
    return route.fulfill({ json: { shot, storyboard_version: String(version) } });
  });
  await page.goto(`${root}/storyboard?agent_subject_storyboard=120`);
  await expect(panel(page).getByRole('textbox', { name: '分镜 20 脚本', exact: true })).toBeVisible();
  await panel(page).getByRole('button', { name: '分镜 20 更多操作', exact: true }).click(); await page.getByRole('menuitem', { name: '下移', exact: true }).click();
  await expect(panel(page).getByRole('textbox', { name: '分镜 20 脚本', exact: true })).toBeDisabled();
  await expect(panel(page).getByRole('spinbutton', { name: '分镜 20 时长', exact: true })).toBeDisabled();
  releaseMove();
  await expect(panel(page).getByRole('textbox', { name: '分镜 21 脚本', exact: true })).toBeVisible();
  await panel(page).getByRole('button', { name: '分镜 21 更多操作', exact: true }).click(); await page.getByRole('menuitem', { name: '归档', exact: true }).click();
  await page.getByRole('button', { name: '确认继续', exact: true }).click();
  await expect(page).toHaveURL(url => !url.searchParams.has('agent_subject_storyboard'));
  await expect(page.locator('.storyboard-stage-meta')).toContainText('本集共 79 镜');
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
  await expect(page).toHaveURL(url => url.searchParams.get('agent_subject_storyboard') === '181');
  expect(offsets).toContain(80); expect(offsets).not.toContain(21);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('replacing all shots clears the previous stable shot scope after adoption', async ({ page }) => {
  const state = await setup(page);
  await page.route(`**/api/v1${root}/storyboard-results/*/apply`, async route => {
    expect(route.request().postDataJSON()).toMatchObject({ mode: 'replace', confirm_replace: true });
    state.shots.splice(0, state.shots.length, { ...state.shots[0], id: '301', position: 1, script: '采用后的新分镜' });
    return route.fallback();
  });
  await page.goto(`${root}/storyboard?agent_subject_storyboard=150`);
  await expect(panel(page).getByRole('textbox', { name: '分镜 50 脚本', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '历史记录', exact: true }).click();
  const history = page.getByRole('dialog', { name: '分镜生成记录', exact: true });
  await history.getByRole('button', { name: '查看分镜', exact: true }).first().click();
  await history.getByRole('button', { name: '替换当前分镜', exact: true }).click();
  await page.getByRole('button', { name: '确认继续', exact: true }).click();
  await expect(history).toHaveCount(0);
  await expect(page).toHaveURL(url => !url.searchParams.has('agent_subject_storyboard'));
  await expect(page.locator('.storyboard-shot-card')).toHaveCount(1);
  await expect(page.locator('.storyboard-shot-card')).toContainText('采用后的新分镜');
  await expect(page.locator('.storyboard-summary[aria-pressed="true"]')).toHaveCount(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('history shows a pending response before the empty state', async ({ page }) => {
  const state = await setup(page); let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/ai/generations?**', async route => {
    if (new URL(route.request().url()).searchParams.get('source_scene') !== 'script_shots') return route.fallback();
    await pending; return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 20 } });
  });
  await page.goto(`${root}/storyboard`); await page.getByRole('button', { name: '历史记录', exact: true }).click();
  const history = page.getByRole('dialog', { name: '分镜生成记录', exact: true });
  await expect(history.getByText('正在加载分镜生成记录…', { exact: true })).toBeVisible();
  await expect(history.getByText('暂无分镜生成记录，确认剧本后开始生成。', { exact: true })).toHaveCount(0);
  release(); await expect(history.getByText('暂无分镜生成记录，确认剧本后开始生成。', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('off-page autosave accepts its version before the initial list response arrives', async ({ page }) => {
  const state = await setup(page); let release!: () => void; let reads = 0; let version = '1'; const savedVersions: string[] = [];
  const pending = new Promise<void>(resolve => { release = resolve; });
  const staleRows = structuredClone(state.shots.slice(0, 20));
  await page.route(`**/api/v1${root}/shots?**`, async route => {
    if (++reads === 1) { await pending; return route.fulfill({ json: { items: staleRows, total: 80, episode_id: '20', storyboard_version: '1', offset: 0, limit: 20 } }); }
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

test('Agent information exposes dialogue review and a failed save retains its draft', async ({ page }) => {
  const state = await setup(page); await scopedAgent(page);
  let fail = true; const dialogue = { row_version: 1, mode: 'native', document: { lines: [], reviewed: false }, characters: [{ id: '501', name: '林晚' }], voices: [] };
  await page.route(`**/api/v1${root}/shots/101/dialogue`, route => {
    if (route.request().method() === 'PUT') {
      if (fail) return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } });
      Object.assign(dialogue, { document: route.request().postDataJSON().document, row_version: 2 });
    }
    return route.fulfill({ json: dialogue });
  });
  await page.goto(`${root}/storyboard?mode=agent&agent_subject_storyboard=101`);
  await page.getByRole('button', { name: '镜头信息', exact: true }).click();
  const information = page.getByRole('dialog', { name: '分镜 01 · 镜头信息', exact: true });
  await information.getByRole('button', { name: '编辑并确认对白', exact: true }).click();
  const review = page.getByRole('dialog', { name: '分镜对白与声音表演', exact: true });
  const save = review.getByRole('button', { name: /保存分镜对白$/ });
  await review.getByRole('checkbox').check(); await expect(save).toBeEnabled(); await save.click();
  await expect(review.getByRole('alert')).toBeVisible(); await expect(review.getByRole('checkbox')).toBeChecked();
  fail = false; await expect(save).toBeEnabled(); await save.click();
  await expect(review).toHaveCount(0); await expect(information.getByText('0 句已确认对白', { exact: true })).toBeVisible();
  await information.getByRole('button', { name: '完成', exact: true }).click(); await expect(information).toHaveCount(0);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
