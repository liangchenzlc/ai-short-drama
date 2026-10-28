import { expect, test, type Page } from '@playwright/test';
import { fixture, image, root } from '../support/studio-fixture';

async function layoutFixture(page: Page) {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  state.shots[0].script = '中景，林晚停在雨夜中的站台。'.repeat(16);
  state.shots[0].title = '雨夜站台上等待来信主人的林晚'.repeat(5);
  state.shots[0].shot_size = 'medium_shot';
  const shot = state.shots[1];
  state.imageGroups.set(`shot:${shot.id}`, {
    id: '61102', kind: 'legacy', status: 'completed', target_kind: 'shot', target_id: shot.id,
    target_version: shot.row_version, is_current: true, expected_count: 1, primary_index: 1,
    primary_version: '1', notice: '创作内容已变化，请核对当前图片。', task_id: null,
    created_at: '2026-09-24T00:00:00Z',
    items: [{ id: '63102', media_id: shot.image.media_id, url: shot.image.url, output_index: 1,
      width: 1920, height: 1080, item_version: '1', status: 'ready', error: null,
      prompt: null, history: [] }],
  });
  return state;
}

async function referenceFixture(page: Page) {
  const state = await layoutFixture(page);
  state.shots[0].asset_ids = ['502', '501'];
  await page.route('**/api/v1/assets/502', route => route.fulfill({ json: {
    ...state.asset, id: '502', kind: 'scene', name: '雨夜车站', media_id: '9905', image: { media_id: '9905', url: image },
  } }));
  state.referenceState['shot/101'] = [
    { media_id: '9801', name: '共享示意图', url: image }, { media_id: '9901', name: '重复共享图', url: image },
  ];
  state.creationDrafts.set('101:storyboard_image', {
    id: '95001', project_id: '10', episode_id: '20', object_id: '101', purpose: 'storyboard_image', model_config_id: '77',
    input_text: '', image_settings: { aspect: '16:9', resolution: '2K', count: 1 }, video_settings: null,
    reference_media_ids: ['9905', '9902', '9903'],
    reference_images: ['9905', '9902', '9903'].map(media_id => ({ media_id, url: media_id === '9903' ? null : image })),
    row_version: '1', draft_version: '1', source_version: '1',
  });
  return state;
}

const panel = (page: Page) => page.locator('.standard-storyboard-panel');

for (const width of [1440, 1280]) test(`model capability feedback keeps storyboard geometry at ${width}px`, async ({ page }) => {
  const state = await layoutFixture(page);
  await page.setViewportSize({ width, height: 900 });
  let release!: () => void;
  let requests = 0;
  const ready = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/ai-model-configs/77/capabilities', async route => {
    requests++;
    await ready;
    await route.fulfill({ status: 503, json: { detail: '模型能力暂时不可用' } });
  });
  await page.goto(`${root}/storyboard?shot=101`);
  const settings = panel(page).locator('#storyboard-image-settings');
  const editor = settings.locator('.asset-prompt-input [contenteditable]');
  await expect(editor).toBeVisible({ timeout: 15_000 });
  await expect.poll(() => requests).toBeGreaterThan(0);
  await expect(settings.getByRole('combobox', { name: '分镜生图模型', exact: true })).toBeEnabled();
  await expect(page.locator('.studio-message')).toHaveCount(0);
  await expect(settings).not.toContainText('正在核对模型能力');
  const original = await editor.boundingBox();
  const settingsSize = await settings.boundingBox();
  release();
  await expect(page.locator('.studio-message').filter({ hasText: '图片模型能力读取失败' })).toBeVisible();
  await expect(page.locator('.studio-message')).toHaveCount(1);
  expect(await editor.boundingBox()).toEqual(original);
  expect(await settings.boundingBox()).toEqual(settingsSize);
  await expect(settings.locator('.ant-alert')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(state.errors).toEqual([]);
});

// Local 640×360 VP8 frames; embedded so browser checks need no media server or encoder.
const testVideo = Buffer.from('GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAAOwEU2bdLpNu4tTq4QVSalmU6yBoU27i1OrhBZUrmtTrIHWTbuMU6uEElTDZ1OsggEeTbuMU6uEHFO7a1OsggOa7AEAAAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsCrXsYMPQkBNgIxMYXZmNjAuMy4xMDBXQYxMYXZmNjAuMy4xMDBEiYhAj0AAAAAAABZUrmvDrgEAAAAAAAA614EBc8WIOPLCndScICqcgQAitZyDdW5kiIEAhoVWX1ZQOIOBASPjg4QdzWUA4IuwggKAuoIBaJqBAhJUw2f7c3OfY8CAZ8iZRaOHRU5DT0RFUkSHjExhdmY2MC4zLjEwMHNz1mPAi2PFiDjywp3UnCAqZ8igRaOHRU5DT0RFUkSHk0xhdmM2MC4zLjEwMCBsaWJ2cHhnyKJFo4hEVVJBVElPTkSHlDAwOjAwOjAxLjAwMDAwMDAwMAAAH0O2dUH254EAo0G6gQAAgJAzAJ0BKoACaAEARwiFhYiFhIgCAgJ08jo6tL7XtzZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcPZOcMgA/v7vul/O34g/UGCVJdLi0KO0gQH0AJEFAAEQEAAYABhYL/QX+gYGC/0DAAiABAAAhtBZjovcpNfnKsZFZkJPsxRhoLKgABxTu2uRu4+zgQC3iveBAfGCAZ7wgQM=', 'base64');
async function serveTestVideo(page: Page) {
  await page.route('**/test-shot-video/*.webm', route => route.fulfill({ contentType: 'video/webm', body: testVideo }));
}

function savedVideo(mediaId: string, name: string) {
  return { media_id: mediaId, media_asset_id: mediaId, url: `/test-shot-video/${name}.webm`, resolution: '720p', duration_ms: 3000, first_frame_media_id: '9101', is_stale: false };
}

async function expectCardLayout(page: Page) {
  const bounds = await page.locator('.storyboard-shot-card').first().evaluate(card => {
    const summary = card.querySelector('.storyboard-summary')!.getBoundingClientRect();
    const media = card.querySelector('.storyboard-card-media')!.getBoundingClientRect();
    const copy = card.querySelector('.storyboard-card-copy')!.getBoundingClientRect();
    const heading = card.querySelector('.storyboard-card-heading')!.getBoundingClientRect();
    return { summary: { width: summary.width, height: summary.height }, media: { height: media.height, bottom: media.bottom }, copy: { width: copy.width, height: copy.height, top: copy.top, left: copy.left, right: copy.right }, heading: { left: heading.left, right: heading.right } };
  });
  expect(Math.abs(bounds.copy.height - 34)).toBeLessThanOrEqual(0.5);
  expect(Math.abs(bounds.copy.width - bounds.summary.width)).toBeLessThanOrEqual(0.5);
  expect(Math.abs(bounds.media.bottom - bounds.copy.top)).toBeLessThanOrEqual(0.5);
  expect(Math.abs(bounds.media.height + bounds.copy.height - bounds.summary.height)).toBeLessThanOrEqual(0.5);
  expect(Math.abs(bounds.heading.left - bounds.copy.left - 10)).toBeLessThanOrEqual(0.5);
  expect(Math.abs(bounds.copy.right - bounds.heading.right - 39)).toBeLessThanOrEqual(0.5);
  const actions = (await page.locator('.storyboard-shot-card').first().getByRole('button', { name: '分镜 01 卡片更多操作', exact: true }).boundingBox())!;
  expect(actions.x).toBeGreaterThanOrEqual(bounds.heading.right);
}

async function expectDesktopHeaderFits(page: Page) {
  const heading = panel(page).locator('.standard-object-heading');
  const bounds = (await heading.boundingBox())!;
  const actions = heading.locator('nav .ant-btn');
  await expect(actions).toHaveCount(5);
  for (const action of await actions.all()) {
    await expect(action).toBeInViewport({ ratio: 0.99 });
    const box = (await action.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(bounds.x);
    expect(box.x + box.width).toBeLessThanOrEqual(bounds.x + bounds.width);
  }
  const tabs = (await heading.getByRole('tablist', { name: '镜头制作内容', exact: true }).boundingBox())!;
  const navigation = (await heading.getByRole('navigation').boundingBox())!;
  expect(tabs.x).toBeGreaterThanOrEqual(bounds.x);
  expect(tabs.x + tabs.width).toBeLessThanOrEqual(bounds.x + bounds.width);
  expect(tabs.y).toBeGreaterThanOrEqual(bounds.y);
  expect(tabs.y + tabs.height).toBeLessThanOrEqual(bounds.y + bounds.height);
  const overlapWidth = Math.max(0, Math.min(tabs.x + tabs.width, navigation.x + navigation.width) - Math.max(tabs.x, navigation.x));
  const overlapHeight = Math.max(0, Math.min(tabs.y + tabs.height, navigation.y + navigation.height) - Math.max(tabs.y, navigation.y));
  expect(overlapWidth * overlapHeight).toBeLessThanOrEqual(1);
  await expect(panel(page).locator('.standard-object-tabs,.storyboard-editor-tabs')).toHaveCount(0);
  const listHeading = page.locator('.standard-storyboard-list .storyboard-editor-heading');
  expect(Math.abs((await listHeading.boundingBox())!.height - 44)).toBeLessThanOrEqual(0.5);
}

test('initial desktop selection opens the first shot preview and preserves an explicit off-page route', async ({ page }) => {
  const state = await layoutFixture(page);
  await page.setViewportSize({ width: 1280, height: 640 });
  await page.goto(`${root}/storyboard`);
  await expect(page.locator('.storyboard-summary').first()).toHaveAttribute('aria-pressed', 'true');
  await expect(page).toHaveURL(url => url.searchParams.get('shot') === '101');
  const settings = panel(page).locator('.standard-object-settings:visible');
  await expect(settings).toBeVisible(); await expect(page.getByRole('button', { name: '收起生成设置', exact: true })).toHaveAttribute('aria-expanded', 'true');
  await page.goto(`${root}/storyboard?shot=150`);
  await expect(panel(page).locator('.standard-object-heading h3')).toContainText('50 ·');
  await expect(page.locator('.storyboard-summary[aria-pressed="true"]')).toHaveCount(0);
  await expect(settings).toBeVisible(); await expect(page.getByRole('button', { name: '收起生成设置', exact: true })).toHaveAttribute('aria-expanded', 'true');
  await expect(page).toHaveURL(url => url.searchParams.get('shot') === '150');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('fixed-height cards retain full accessible scripts and separate image and video states', async ({ page }, info) => {
  const state = await layoutFixture(page);
  await page.goto(`${root}/storyboard`);
  const cards = page.locator('.storyboard-shot-card');
  await expect(cards).toHaveCount(20);
  await expect(cards.first().locator('.storyboard-card-media img')).toBeVisible();
  await expect(cards.nth(3).getByText('尚无主图', { exact: true })).toBeVisible();
  await expect(cards.nth(3).getByText('待制作', { exact: true })).toBeVisible();
  const firstCardButton = cards.first().getByRole('button', { name: '选择分镜 01', exact: true });
  await expect(firstCardButton).toHaveAccessibleDescription(/中景，林晚停在雨夜中的站台。/);
  await expect(firstCardButton).toHaveAccessibleDescription(new RegExp(`${state.shots[0].duration_ms / 1000}\\s*秒`));
  await expect(firstCardButton).toHaveAccessibleDescription(/图片：已生图[\s\S]*视频：待制作/);
  await expect(cards.nth(1).getByRole('button', { name: '选择分镜 02', exact: true })).toHaveAccessibleDescription(/图片：已生图[\s\S]*视频：待制作/);
  await expect(cards.first().locator('.storyboard-card-heading strong')).toHaveAttribute('title', state.shots[0].title);
  await expect(cards.first().locator('.storyboard-card-heading')).toContainText('中景');
  await expect(cards.first().locator('.storyboard-summary-script,.storyboard-card-states')).toHaveCount(0);
  const name = await cards.first().locator('.storyboard-card-heading strong').evaluate(element => ({
    height: element.clientHeight, width: element.clientWidth, scroll: element.scrollWidth,
    wrap: getComputedStyle(element).whiteSpace, overflow: getComputedStyle(element).textOverflow,
  }));
  expect(name.wrap).toBe('nowrap'); expect(name.overflow).toBe('ellipsis'); expect(name.scroll).toBeGreaterThan(name.width);
  await expectCardLayout(page);
  const firstHeight = (await cards.first().boundingBox())!.height;
  await firstCardButton.click();
  expect(Math.abs((await cards.first().boundingBox())!.height - firstHeight)).toBeLessThanOrEqual(0.5);
  expect(Math.abs((await cards.nth(3).boundingBox())!.height - firstHeight)).toBeLessThanOrEqual(0.5);
  await cards.nth(1).getByRole('button', { name: '选择分镜 02', exact: true }).click();
  await expect(page.getByRole('button', { name: /^保存状态：需处理/ })).toBeVisible();
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  await expect(page.locator('.studio-feedback-details').getByText('创作内容已变化，请核对当前图片。', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).click();
  await expect(cards.first().getByText('待制作', { exact: true })).toBeVisible();
  await expect(panel(page).getByText('还没有生成视频', { exact: true })).toBeVisible();
  await expect(page.locator('.native-sound-mode')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('storyboard-cards.png'), fullPage: true });
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('selection and adjacent navigation preserve private input across the two media tabs', async ({ page }) => {
  const state = await layoutFixture(page);
  await page.goto(`${root}/storyboard`);
  await page.locator('.storyboard-summary').first().click();
  await expect(page.locator('.storyboard-summary').first()).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.storyboard-expanded')).toHaveCount(0);
  const input = panel(page).getByRole('textbox', { name: '分镜 1 脚本', exact: true });
  await input.fill('固定机位，林晚收起旧信。');
  await panel(page).getByRole('button', { name: '下一个分镜', exact: true }).click();
  await expect(panel(page).getByRole('textbox', { name: '分镜 2 脚本', exact: true })).toBeVisible();
  await panel(page).getByRole('button', { name: '上一个分镜', exact: true }).click();
  await expect(input).toHaveValue('固定机位，林晚收起旧信。');
  await expect(panel(page).getByRole('tab')).toHaveCount(2);
  await expect(panel(page).getByRole('button', { name: '生成图片', exact: true })).toBeVisible();
  await panel(page).getByRole('textbox', { name: '画面要求', exact: true }).fill('保留个人分镜图输入');
  const imageOutput = panel(page).getByRole('button', { name: '比例与分辨率', exact: true });
  await imageOutput.click();
  const imageOptions = page.getByRole('dialog', { name: '生成选项', exact: true });
  await imageOptions.getByRole('button', { name: '生成 4 张图片', exact: true }).click();
  await page.keyboard.press('Escape');
  await panel(page).getByRole('tab', { name: '分镜图', exact: true }).press('ArrowRight');
  await expect(panel(page).getByRole('tab', { name: '分镜视频', exact: true })).toBeFocused();
  await expect(input).toHaveValue('固定机位，林晚收起旧信。');
  await expect(panel(page).getByRole('textbox', { name: '视频提示词', exact: true })).toBeVisible();
  await panel(page).getByRole('textbox', { name: '视频提示词', exact: true }).fill('保留个人视频输入');
  await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).press('ArrowLeft');
  await expect(panel(page).getByRole('tab', { name: '分镜图', exact: true })).toBeFocused();
  await expect(panel(page).getByRole('textbox', { name: '画面要求', exact: true })).toHaveText('保留个人分镜图输入');
  await expect(imageOutput).toContainText('4张');
  await expect.poll(() => state.shots[0].script).toBe('固定机位，林晚收起旧信。');
  await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).click();
  await expect(panel(page).getByRole('textbox', { name: '视频提示词', exact: true })).toHaveValue('保留个人视频输入');
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('batch selection is explicit and stays selected when a card opens', async ({ page }) => {
  const state = await layoutFixture(page);
  await page.goto(`${root}/storyboard`);
  await page.getByRole('button', { name: '分镜生成记录', exact: true }).click();
  const more = page.getByRole('dialog', { name: '分镜生成记录', exact: true }).getByRole('button', { name: /更多操作$/ });
  await expect(more).toBeVisible();
  await expect(page.locator('.batch-item-select')).toHaveCount(0);
  await more.click(); await page.getByRole('menuitem', { name: '批量操作', exact: true }).click();
  const card = page.locator('.storyboard-shot-card').first();
  await card.getByRole('checkbox', { name: '批量选择分镜 1', exact: true }).check();
  await expect(page.getByRole('checkbox', { name: '选择已加载项', exact: true })).toBeChecked({ indeterminate: true });
  await card.locator('.storyboard-summary').click();
  await expect(card.getByRole('checkbox')).toBeChecked();
  await page.getByRole('button', { name: '退出批量操作', exact: true }).click();
  await page.getByRole('button', { name: '分镜生成记录', exact: true }).click();
  await more.click(); await page.getByRole('menuitem', { name: '批量操作', exact: true }).click();
  await expect(card.getByRole('checkbox')).toBeChecked();
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('project assistant and shot input survive navigation while shot saving is conflicting', async ({ page }) => {
  const state = await layoutFixture(page);
  page.on('console', message => { if (message.text().includes('Encountered two children with the same key')) state.errors.push(message.text()); });
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: '9007199254740993', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true } } }));
  const conversation = { id: '301', project_id: '10', episode_id: null, title: '项目对话', stage: null, subject_type: null, subject_id: null, task_type: null, scope_version: 3, status: 'active', archived: false, row_version: '1', last_run_status: null, created_at: '2026-10-06T00:00:00Z', updated_at: '2026-10-06T00:00:00Z' };
  const conversations = [conversation];
  await page.route(/\/api\/v1\/(agent|assistant)\//, async route => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api\/v1\/(agent|assistant)/, '');
    if (path === '/status') return route.fulfill({ json: { enabled: true, schema_ready: true } });
    if (path === '/models') return route.fulfill({ json: { items: [], preferred_id: null } });
    if (path === '/skills') return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 50 } });
    if (path === '/conversations/resolve') {
      expect(route.request().postDataJSON()).toEqual({ project_id: '10' });
      return route.fulfill({ json: conversation });
    }
    if (path === '/global/conversations') return route.fulfill({ json: { items: conversations, total: conversations.length, offset: 0, limit: 20 } });
    if (/^\/global\/conversations\/\d+$/.test(path)) return route.fulfill({ json: conversations.find(item => item.id === path.split('/').pop()) });
    if (path.endsWith('/state')) return route.fulfill({ json: { conversation_id: '301', snapshot_cursor: 0, resume_cursor: 0, messages: { items: [], total: 0, offset: 0, limit: 50 }, tasks: [], runs: [], operations: [], inputs: [], has_more_tasks: false, has_more_operations: false } });
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (/\/(messages|attachments|runs)$/.test(path)) return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 50 } });
    return route.fallback();
  });
  let conflict = true;
  await page.route(`**/api/v1${root}/shots/101`, async route => {
    if (route.request().method() === 'PATCH' && conflict) return route.fulfill({ status: 409, json: { error: { code: 'version_conflict' } } });
    return route.fallback();
  });
  await page.goto(`${root}/storyboard?assistant=open&conversation=301`);
  const composer = page.getByRole('textbox', { name: '给助手的消息', exact: true });
  await expect(composer).toBeVisible();
  await composer.fill('保留这段项目对话草稿');
  await page.locator('.storyboard-summary').first().click();
  await expect(composer).toBeVisible();
  await expect(composer).toHaveValue('保留这段项目对话草稿');
  const script = page.getByRole('textbox', { name: '分镜 1 脚本', exact: true });
  await script.fill('发生冲突仍保留的镜头草稿');
  const stages = page.getByRole('navigation', { name: '创作流程' });
  await stages.getByRole('button', { name: /资产生成/ }).click();
  await expect(page).toHaveURL(url => url.pathname.endsWith('/assets') && url.searchParams.get('conversation') === '301');
  await expect(page.getByRole('dialog', { name: '未保存的修改', exact: true })).toHaveCount(0);
  await expect(composer).toHaveValue('保留这段项目对话草稿');
  await stages.getByRole('button', { name: '分镜制作', exact: true }).click();
  await page.locator('.storyboard-summary').first().click();
  await expect(script).toHaveValue('发生冲突仍保留的镜头草稿');
  await expect(page.getByRole('button', { name: /^保存状态：需处理/ })).toBeVisible();
  await expect(composer).toHaveValue('保留这段项目对话草稿');
  await expect(page).toHaveURL(url => url.pathname.endsWith('/storyboard'));
  conflict = false;
  const saveStatus = page.getByRole('button', { name: /^保存状态：/ });
  if (await saveStatus.getAttribute('aria-expanded') !== 'true') await saveStatus.click();
  await page.locator('.standard-save-details').getByRole('button', { name: '载入服务端镜头', exact: true }).first().click();
  await expect(page.getByRole('dialog', { name: '确认操作', exact: true })).toHaveCount(0);
  await expect(script).toHaveValue(state.shots[0].script);
  await script.fill('发生冲突仍保留的镜头草稿');
  await expect.poll(() => state.shots[0].script).toBe('发生冲突仍保留的镜头草稿');
  await stages.getByRole('button', { name: /资产生成/ }).click();
  await expect(page).toHaveURL(url => url.pathname.endsWith('/assets') && url.searchParams.get('conversation') === '301');
  await expect(composer).toHaveValue('保留这段项目对话草稿');
  expect(conversations).toHaveLength(1);
  expect(state.shots[0].script).toBe('发生冲突仍保留的镜头草稿');
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

for (const width of [1440, 1920, 1366, 1280]) test(`cards and the selected-shot panel fit at ${width}px`, async ({ page }, info) => {
  const state = await layoutFixture(page);
  await page.setViewportSize({ width, height: 1000 });
  await page.goto(`${root}/storyboard`);
  await expect(page.locator('.storyboard-shot-card')).toHaveCount(20);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.locator('.storyboard-summary').first().click();
  await expectCardLayout(page);
  await expectDesktopHeaderFits(page);
  await expect(page.getByRole('textbox', { name: '分镜 1 脚本', exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath(`selected-shot-${width}.png`), fullPage: true });
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});


test('storyboard keeps the media switch inside the heading and preserves settings structure', async ({ page }) => {
  const state = await layoutFixture(page);
  state.shots[0].title = '雨夜站台';
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${root}/storyboard`);
  await page.locator('.storyboard-summary').first().click();
  const workspace = panel(page);
  const heading = workspace.locator('.standard-object-heading');
  await expect(heading.getByRole('heading', { name: '01 · 雨夜站台', exact: true })).toBeVisible();
  await expect(heading.locator('.creation-preview-status')).toHaveCount(0);
  const tabs = workspace.getByRole('tablist', { name: '镜头制作内容', exact: true });
  await expect(heading.getByRole('tablist')).toHaveCount(1);
  const headingBox = (await heading.boundingBox())!;
  const tabsBox = (await tabs.boundingBox())!;
  expect(tabsBox.y).toBeGreaterThanOrEqual(headingBox.y);
  expect(tabsBox.y + tabsBox.height).toBeLessThanOrEqual(headingBox.y + headingBox.height);
  expect(tabsBox.width).toBeLessThan(headingBox.width / 2);
  await expect(workspace.locator('.standard-object-tabs,.storyboard-editor-tabs')).toHaveCount(0);
  const settings = workspace.locator('#storyboard-image-pane .standard-object-settings');
  await expect(settings.getByRole('heading', { name: '生成设置', exact: true })).toBeVisible();
  await expect(settings.getByRole('button', { name: '关闭分镜生成设置', exact: true })).toBeVisible();
  const firstCard = page.locator('.storyboard-shot-card').first();
  await expect(firstCard.locator('.standard-shot-number')).toHaveText('01');
  await expect(firstCard.locator('.standard-shot-duration')).toHaveText('3 秒');
  const number = (await firstCard.locator('.standard-shot-number').boundingBox())!;
  const duration = (await firstCard.locator('.standard-shot-duration').boundingBox())!;
  expect(duration.x).toBeGreaterThan(number.x);
  expect(duration.y).toBeGreaterThan(number.y);
  await expect(page.getByRole('button', { name: '全部镜头', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('button', { name: '待制作', exact: true })).toHaveAttribute('aria-pressed', 'false');
  await workspace.locator('.image-subject').first().hover();
  await workspace.getByRole('button', { name: '预览第 1 张图片', exact: true }).click();
  const preview = page.getByRole('dialog', { name: '图片预览', exact: true });
  await expect(preview.getByRole('button', { name: '放大镜', exact: true })).toBeEnabled();
  await preview.getByRole('button', { name: '原始尺寸', exact: true }).click();
  await expect(preview.getByRole('button', { name: '适应窗口', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(preview).toHaveCount(0);
  await workspace.getByRole('button', { name: '图片生成记录', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '分镜图片生成记录', exact: true })).toBeVisible();
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('unnamed shots submit image and video tasks without requiring a title', async ({ page }) => {
  const state = await layoutFixture(page);
  state.shots[0].title = '';
  const submissions: { kind: string; body: any }[] = [];
  await page.route(/\/api\/v1\/ai\/generations\/(image|video)$/, route => {
    const kind = new URL(route.request().url()).pathname.split('/').pop()!;
    submissions.push({ kind, body: route.request().postDataJSON() });
    return route.fulfill({ status: 202, json: { generation_id: kind === 'image' ? '97001' : '97002', service_type: kind, status: 'queued' } });
  });
  await page.goto(`${root}/storyboard`);
  for (const kind of ['image', 'video'] as const) {
    if (kind === 'video') await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).click();
    const pane = panel(page).locator(`#storyboard-${kind}-pane`);
    const send = pane.getByRole('button', { name: kind === 'image' ? '生成图片' : '生成视频', exact: true });
    await expect(pane.getByRole('textbox', { name: '镜头名称', exact: true })).toHaveValue('');
    await expect(pane.getByText('请先填写镜头名称。', { exact: true })).toHaveCount(0);
    await expect(send).toBeEnabled(); await send.click();
    await expect.poll(() => submissions.filter(item => item.kind === kind).length).toBe(1);
    expect(submissions.at(-1)!.body.source.shot_id).toBe('101');
    expect(state.shots[0].title).toBe('');
  }
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

for (const delaySharedSave of [false, true]) test(`completed shot videos refresh without adoption ${delaySharedSave ? 'after a delayed shared save' : 'and retain private input'}`, async ({ page }) => {
  const state = await layoutFixture(page);
  await serveTestVideo(page);
  const oldVideo = savedVideo('91001', 'old');
  const newVideo = savedVideo('91002', 'new');
  state.shots[0].video = oldVideo;
  let submitted = false;
  let completed = false;
  let completedObserved = false;
  let saving = false;
  let holdSharedSave = false;
  let releaseSharedSave: () => void = () => {};
  const sharedSave = new Promise<void>(resolve => { releaseSharedSave = resolve; });
  await page.route(`**/api/v1${root}/shots/101`, async route => {
    if (route.request().method() === 'PATCH' && holdSharedSave) { saving = true; await sharedSave; }
    return route.fallback();
  });
  const applyRequests: string[] = [];
  page.on('request', request => { if (/\/media-library\/items\/[^/]+\/apply$/.test(new URL(request.url()).pathname)) applyRequests.push(request.url()); });
  const source = { scene: 'shot_video', shot_id: '101', project_id: '10', episode_id: '20' };
  const time = '2026-10-20T00:00:00Z';
  const videoTask = () => ({ generation_id: '97002', service_type: 'video', status: completed ? 'succeeded' : 'running', source, config: { id: '77', name: '本地测试模型', provider: 'fixture', model_key: 'fixture-video' }, created_at: time, can_cancel: !completed, can_retry: false, can_resume: false });
  const videoAsset = (mediaId: string, name: string) => ({ asset_id: mediaId, generation_id: '97002', media_id: mediaId, media_type: 'video', name, source, url: `/test-shot-video/${name}.webm`, row_version: '1', duration_ms: 3000, created_at: time });
  await page.route('**/api/v1/ai/generations/video', route => { submitted = true; return route.fulfill({ status: 202, json: videoTask() }); });
  await page.route('**/api/v1/ai/generations?**', route => {
    const query = new URL(route.request().url()).searchParams;
    if (query.get('source_scene') !== 'shot_video') return route.fallback();
    if (completed) completedObserved = true;
    const items = submitted && (!query.get('status') || query.get('status') === videoTask().status) ? [videoTask()] : [];
    return route.fulfill({ json: { items, total: items.length, offset: 0, limit: 20 } });
  });
  await page.route('**/api/v1/ai/generations/97002', route => route.fulfill({ json: { ...videoTask(), result: { text: null, partial: false, assets: completed ? [videoAsset('91002', 'new')] : [] } } }));
  await page.route('**/api/v1/media-library/items?**', route => {
    const query = new URL(route.request().url()).searchParams;
    if (query.get('source_scene') !== 'shot_video') return route.fallback();
    const items = completed ? [videoAsset('91002', 'new'), videoAsset('91001', 'old')] : [videoAsset('91001', 'old')];
    return route.fulfill({ json: { items, total: items.length, offset: 0, limit: 20 } });
  });
  await page.goto(`${root}/storyboard`);
  await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).click();
  const pane = panel(page).locator('#storyboard-video-pane');
  const preview = pane.locator('.standard-object-preview video');
  await expect(preview).toHaveAttribute('src', oldVideo.url);
  await expect.poll(() => preview.evaluate(node => (node as HTMLVideoElement).videoWidth)).toBe(640);
  const prompt = pane.getByRole('textbox', { name: '视频提示词', exact: true });
  await prompt.fill('保留正在核对的个人视频要求');
  await pane.getByRole('button', { name: '生成视频', exact: true }).click();
  await expect.poll(() => submitted).toBe(true);
  await expect(preview).toHaveAttribute('src', oldVideo.url);
  const script = pane.getByRole('textbox', { name: '分镜 1 脚本', exact: true });
  if (delaySharedSave) {
    holdSharedSave = true;
    await script.fill('正在保存但不能被新视频完成响应覆盖的镜头脚本');
    await expect.poll(() => saving).toBe(true);
  }
  completed = true;
  Object.assign(state.shots[0], { video: newVideo, row_version: String(BigInt(state.shots[0].row_version) + 1n) });
  if (delaySharedSave) {
    await expect.poll(() => completedObserved, { timeout: 12000 }).toBe(true);
    await expect(pane.getByText('视频正在生成，离开页面后任务仍会继续。', { exact: true })).toHaveCount(0);
    await expect(preview).toHaveAttribute('src', oldVideo.url);
    await expect(script).toHaveValue('正在保存但不能被新视频完成响应覆盖的镜头脚本');
    releaseSharedSave();
  }
  await expect(preview).toHaveAttribute('src', newVideo.url, { timeout: 12000 });
  if (delaySharedSave) await expect(script).toHaveValue('正在保存但不能被新视频完成响应覆盖的镜头脚本');
  await expect(prompt).toHaveValue('保留正在核对的个人视频要求');
  await expect(pane.locator('.standard-candidate-actions')).toHaveCount(0);
  await expect(pane.getByRole('button', { name: /采用|查看当前作品/ })).toHaveCount(0);
  await expect(panel(page).locator('.standard-object-heading').getByText(/已采用|已生图|未采用|待采用/)).toHaveCount(0);
  await panel(page).getByRole('button', { name: '视频生成记录', exact: true }).click();
  const history = page.getByRole('dialog', { name: '分镜视频生成记录', exact: true });
  await expect(history).toBeVisible();
  await expect(history.locator('video')).toHaveCount(2);
  await expect(history.getByRole('button', { name: /采用|查看当前作品/ })).toHaveCount(0);
  expect(applyRequests).toEqual([]);
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1440, height: 600 }, { width: 1366, height: 768 }, { width: 1280, height: 720 }]) test(`saved video fills the preview and keeps its heading switch reachable at ${viewport.width}x${viewport.height}`, async ({ page }, info) => {
  const state = await layoutFixture(page);
  await serveTestVideo(page);
  state.shots[0].video = savedVideo('91001', 'layout');
  await page.setViewportSize(viewport);
  await page.goto(`${root}/storyboard`);
  await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).click();
  const pane = panel(page).locator('#storyboard-video-pane');
  const video = pane.locator('.standard-object-preview video');
  await expect.poll(() => video.evaluate(node => (node as HTMLVideoElement).videoWidth)).toBe(640);
  await expect.poll(() => video.evaluate(node => (node as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(1);
  expect(await video.evaluate(node => ({ controls: (node as HTMLVideoElement).controls, fit: getComputedStyle(node).objectFit }))).toEqual({ controls: true, fit: 'contain' });
  const previewBox = (await pane.locator('.standard-object-preview').boundingBox())!;
  const videoBox = (await video.boundingBox())!;
  for (const key of ['x', 'y', 'width', 'height'] as const) expect(Math.abs(videoBox[key] - previewBox[key]), `${key} fills the complete preview`).toBeLessThanOrEqual(1);
  await expect(panel(page).locator('.standard-object-heading').getByRole('tablist', { name: '镜头制作内容', exact: true })).toBeVisible();
  await expect(panel(page).locator('.standard-object-tabs,.storyboard-editor-tabs')).toHaveCount(0);
  await expectDesktopHeaderFits(page);
  await expect(pane.locator('.standard-candidate-actions')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await video.evaluate(async node => {
    const player = node as HTMLVideoElement;
    const painted = new Promise<void>(resolve => player.requestVideoFrameCallback(() => resolve()));
    await player.play(); await painted; player.pause();
  });
  await video.hover();
  await page.screenshot({ path: info.outputPath(`saved-video-${viewport.width}x${viewport.height}.png`), fullPage: false, animations: 'disabled' });
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('adopted material references are selected into the private draft without changing the shot', async ({ page }) => {
  const state = await layoutFixture(page);
  state.shots[0].asset_ids = [];
  await page.route(`**/api/v1${root}/assets?**`, route => route.fulfill({ json: { items: [state.asset], total: 1, offset: 0, limit: 100 } }));
  await page.goto(`${root}/storyboard`);
  await page.locator('.storyboard-summary').first().click();
  const references = panel(page).locator('#storyboard-image-pane').getByRole('region', { name: '个人参考图片', exact: true });
  await references.getByRole('button', { name: '添加参考图片', exact: true }).click();
  const picker = page.getByRole('dialog', { name: '选择素材主图作为参考', exact: true });
  await expect(picker).toBeVisible();
  await picker.getByRole('checkbox', { name: '林晚', exact: true }).check();
  await picker.getByRole('button', { name: '确认选择', exact: true }).click();
  await expect(picker).toHaveCount(0);
  await expect.poll(() => state.creationDrafts.get('101:storyboard_image')?.reference_media_ids).toEqual(['9901']);
  expect(state.shots[0].asset_ids).toEqual([]);
  await expect(references.locator('.standard-reference-count')).toHaveText('1 / 16');
  const figure = references.locator('figure').first();
  const tileBounds = (await figure.boundingBox())!;
  const captionBounds = (await figure.locator('figcaption').boundingBox())!;
  expect(captionBounds.y).toBeGreaterThanOrEqual(tileBounds.y);
  expect(captionBounds.y + captionBounds.height).toBeLessThanOrEqual(tileBounds.y + tileBounds.height);
  await expect(references.getByRole('button', { name: '移除个人参考图片 9901', exact: true })).toBeVisible();
  await references.getByRole('button', { name: '移除个人参考图片 9901', exact: true }).click();
  await expect.poll(() => state.creationDrafts.get('101:storyboard_image')?.reference_media_ids).toEqual([]);
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('shot image mentions follow persisted media order and remove private references in one save', async ({ page }) => {
  const state = await referenceFixture(page);
  const patches: any[] = [];
  await page.route(`**/api/v1${root}/drafts/101/storyboard_image`, route => {
    if (route.request().method() === 'PATCH') patches.push(route.request().postDataJSON());
    return route.fallback();
  });
  await page.goto(`${root}/storyboard?shot=101`);
  const prompt = panel(page).getByRole('textbox', { name: '画面要求', exact: true });
  const references = panel(page).getByRole('region', { name: '个人参考图片', exact: true });
  const add = references.getByRole('button', { name: '添加参考图片', exact: true });
  await expect(add).toHaveClass(/standard-reference-add-tile/); await expect(add).toHaveText('');
  expect((await add.boundingBox())!.width).toBe(64); expect((await add.boundingBox())!.height).toBe(58);
  await expect(references.locator('.standard-reference-count')).toHaveText('5 / 16');
  await expect(references.getByRole('button', { name: /^移除个人参考图片 / })).toHaveCount(2);
  await prompt.fill('主体 @');
  const menu = page.getByRole('listbox', { name: '引用参考图片', exact: true });
  await expect(menu.getByRole('option')).toHaveCount(5);
  await expect(menu.getByRole('option').nth(0)).toContainText('林晚');
  await expect(menu.getByRole('option').nth(1)).toContainText('雨夜车站');
  await expect(menu.getByRole('option').nth(2)).toContainText('共享示意图');
  await expect(menu.getByRole('option').last()).toContainText('图片5');
  await prompt.press('ArrowDown'); await prompt.press('Enter');
  await prompt.pressSequentially('与 @');
  await menu.getByRole('option').filter({ hasText: /^图片5/ }).click();
  await prompt.pressSequentially('及 @');
  await menu.getByRole('option').filter({ hasText: /^图片4/ }).click();
  await expect.poll(() => state.creationDrafts.get('101:storyboard_image')?.input_text).toBe('主体 @图片2 与 @图片5 及 @图片4 ');
  await expect(prompt.locator('.asset-prompt-chip')).toHaveCount(3);
  await page.reload(); await expect(prompt.locator('.asset-prompt-chip')).toHaveCount(3);
  patches.length = 0;
  await references.getByRole('button', { name: '移除个人参考图片 9902', exact: true }).click();
  const expected = '主体 @图片2 与 @图片4 及  ';
  await expect.poll(() => state.creationDrafts.get('101:storyboard_image')?.input_text).toBe(expected);
  expect(patches).toHaveLength(1);
  expect(patches[0]).toMatchObject({ input_text: expected, reference_media_ids: ['9905', '9903'] });
  await expect(prompt.locator('[data-asset-prompt-token="@图片4"]')).toHaveAttribute('data-asset-prompt-media-id', '9903');
  await expect(prompt.locator('.asset-prompt-chip')).toHaveCount(2);
  expect(state.shots[0].asset_ids).toEqual(['502', '501']);
  expect(state.requests.some(request => request.method === 'PATCH' && request.path === `${root}/shots/101`)).toBe(false);
  const model = panel(page).getByRole('combobox', { name: '分镜生图模型', exact: true });
  await model.focus(); await model.press('Enter');
  const models = page.locator('.creation-model-popup-compact:visible');
  await expect(models.locator('.creation-model-option strong')).toContainText('本地验收模型');
  expect((await models.boundingBox())!.width).toBeLessThanOrEqual(240);
  await model.press('Escape');
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('desktop shot mentions and video duration popovers preserve the settings panel', async ({ page }) => {
  test.setTimeout(60000);
  const state = await referenceFixture(page);
  await page.setViewportSize({ width: 1280, height: 640 });
  await page.goto(`${root}/storyboard?shot=101`);
  const toggle = page.getByRole('button', { name: '展开生成设置', exact: true });
  const settings = panel(page).locator('.standard-object-settings:visible');
  const prompt = settings.getByRole('textbox', { name: '画面要求', exact: true });
  await prompt.fill('桌面 @');
  const menu = page.getByRole('listbox', { name: '引用参考图片', exact: true });
  await expect(menu).toBeInViewport({ ratio: 1 });
  await prompt.press('ArrowUp'); await prompt.press('Tab');
  await expect.poll(() => state.creationDrafts.get('101:storyboard_image')?.input_text).toBe('桌面 @图片5 ');
  await prompt.pressSequentially('@'); await expect(menu).toBeVisible();
  await prompt.press('Escape'); await expect(menu).toBeHidden(); await expect(settings).toBeVisible(); await expect(prompt).toBeFocused();
  await prompt.press('Escape'); await expect(settings).toBeVisible(); await expect(prompt).toBeFocused();
  await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).click();
  const videoOutput = settings.getByRole('button', { name: '视频比例与分辨率', exact: true });
  await videoOutput.click();
  const options = page.getByRole('dialog', { name: '视频生成选项', exact: true });
  await expect(options).toBeInViewport({ ratio: 1 });
  await options.getByRole('button', { name: '视频时长 5秒', exact: true }).click();
  await page.keyboard.press('Escape');
  await expect(options).toBeHidden(); await expect(settings).toBeVisible(); await expect(videoOutput).toBeFocused();
  await expect.poll(() => state.creationDrafts.get('101:storyboard_video')?.video_settings?.duration_ms).toBe(5000);
  await page.keyboard.press('Escape'); await expect(settings).toBeVisible();
  expect(state.shots[0].duration_ms).toBe(3000);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('shared reference changes during image preparation stop submission and save remapped mentions', async ({ page }) => {
  const state = await referenceFixture(page);
  state.creationDrafts.get('101:storyboard_image').input_text = '已存 @图片3 和 @图片5 ';
  let reads = 0; const submissions: any[] = [];
  await page.route('**/api/v1/generation-references/shot/101', route => {
    reads++;
    const original = state.referenceState['shot/101'];
    const items = reads === 1 ? original : [{ media_id: '9800', name: '新增共享参考', url: image }, ...original];
    return route.fulfill({ json: { row_version: '1', items } });
  });
  await page.route('**/api/v1/ai/generations/image', route => {
    submissions.push(route.request().postDataJSON());
    return route.fulfill({ json: { generation_id: '97001', service_type: 'image', status: 'queued' } });
  });
  await page.goto(`${root}/storyboard?shot=101`);
  const prompt = panel(page).getByRole('textbox', { name: '画面要求', exact: true });
  await expect(prompt.locator('[data-asset-prompt-token="@图片3"]')).toHaveAttribute('data-asset-prompt-media-id', '9801');
  await expect(prompt.locator('[data-asset-prompt-token="@图片5"]')).toHaveAttribute('data-asset-prompt-media-id', '9903');
  const generate = panel(page).getByRole('button', { name: '生成图片', exact: true });
  await expect(generate).toBeEnabled(); await generate.click();
  await expect(page.getByText('参考图片已变化，请核对后重新点击生成。', { exact: true })).toBeVisible();
  await expect.poll(() => state.creationDrafts.get('101:storyboard_image')?.input_text).toBe('已存 @图片4 和 @图片6 ');
  await expect(prompt.locator('[data-asset-prompt-token="@图片4"]')).toHaveAttribute('data-asset-prompt-media-id', '9801');
  await expect(prompt.locator('[data-asset-prompt-token="@图片6"]')).toHaveAttribute('data-asset-prompt-media-id', '9903');
  expect(reads).toBe(2); expect(submissions).toEqual([]);
  expect(state.creationDrafts.get('101:storyboard_image')?.reference_media_ids).toEqual(['9905', '9902', '9903']);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test('prototype output popovers save image and video settings independently', async ({ page }) => {
  const state = await layoutFixture(page);
  const originalImageSettings = { ...state.shots[0].image_settings };
  const originalVideoSettings = { ...state.shots[0].video_settings };
  await page.goto(`${root}/storyboard?shot=101`);
  const imagePane = panel(page).locator('#storyboard-image-pane');
  const imageOutput = imagePane.getByRole('button', { name: '比例与分辨率', exact: true });
  await expect(imageOutput).toBeEnabled(); await imageOutput.click();
  const imagePopup = page.locator('.standard-image-options-popover:not(.ant-popover-hidden)');
  await expect(imagePopup.locator('.standard-image-ratios')).toHaveCSS('display', 'grid');
  await expect(imagePopup.locator('fieldset').first()).toHaveCSS('border-top-style', 'none');
  await imagePopup.getByRole('button', { name: '画面比例 9:16', exact: true }).click();
  await imagePopup.locator('.standard-image-resolutions').getByRole('button').filter({ hasText: /^4K$/ }).click();
  await expect.poll(() => state.creationDrafts.get('101:storyboard_image')?.image_settings).toMatchObject({ aspect: '9:16', resolution: '4K', count: 1 });
  await imageOutput.click();
  await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).click();
  const videoPane = panel(page).locator('#storyboard-video-pane');
  const videoOutput = videoPane.getByRole('button', { name: '视频比例与分辨率', exact: true });
  await expect(videoOutput).toBeEnabled(); await videoOutput.click();
  const videoPopup = page.getByRole('dialog', { name: '视频生成选项', exact: true });
  await expect(videoPopup.getByRole('heading', { name: '生成选项', exact: true })).toBeVisible();
  await expect(videoPopup.getByRole('button', { name: '关闭生成选项', exact: true })).toBeVisible();
  await videoPopup.getByRole('button', { name: '视频比例 4:3', exact: true }).click();
  await videoPopup.getByRole('button', { name: '视频分辨率 1080p', exact: true }).click();
  await videoPopup.getByRole('button', { name: '视频时长 5秒', exact: true }).click();
  await videoPopup.getByRole('button', { name: '视频分辨率 1080p', exact: true }).press('Escape');
  await expect(videoOutput).toBeFocused(); await expect(videoPopup).toBeHidden();
  await videoOutput.click();
  await videoPopup.getByRole('button', { name: '关闭生成选项', exact: true }).click();
  await expect(videoOutput).toBeFocused(); await expect(videoPopup).toBeHidden();
  await expect.poll(() => state.creationDrafts.get('101:storyboard_video')?.video_settings).toMatchObject({ aspect: '4:3', resolution: '1080p', duration_ms: 5000 });
  await expect(videoOutput).toContainText('5秒');
  await expect(videoPane.getByRole('combobox', { name: '视频时长', exact: true })).toHaveCount(0);
  await panel(page).getByRole('tab', { name: '分镜图', exact: true }).click();
  await expect(imageOutput).toContainText('9:16'); await expect(imageOutput).toContainText('4K');
  expect(state.shots[0].image_settings).toEqual(originalImageSettings);
  expect(state.shots[0].video_settings).toEqual(originalVideoSettings);
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('video output disables unsupported choices and saves the model default', async ({ page }) => {
  const state = await layoutFixture(page);
  await page.route('**/api/v1/ai-model-configs/77/capabilities', route => route.fulfill({ json: {
    known: true, parameters: ['aspect', 'resolution', 'count'], reference_images: true, first_frame: false, last_frame: false,
    video_input: { reference_images: true, duration_seconds: [3, 5], resolutions: ['720p'] },
  } }));
  await page.goto(`${root}/storyboard?shot=101`);
  await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).click();
  const videoPane = panel(page).locator('#storyboard-video-pane');
  const output = videoPane.getByRole('button', { name: '视频比例与分辨率', exact: true });
  await expect(output).toBeEnabled(); await output.click();
  const popup = page.getByRole('dialog', { name: '视频生成选项', exact: true });
  await expect(popup.getByRole('button', { name: '视频分辨率 480p', exact: true })).toBeDisabled();
  await expect(popup.getByRole('button', { name: '视频分辨率 1080p', exact: true })).toBeDisabled();
  await expect(popup.getByRole('button', { name: '视频分辨率 720p', exact: true })).toBeEnabled();
  await expect(popup.getByRole('button', { name: '视频比例 2:3', exact: true })).toBeDisabled();
  await expect(popup.getByRole('button', { name: '视频比例 3:2', exact: true })).toBeDisabled();
  await popup.getByRole('button', { name: '使用模型默认视频分辨率', exact: true }).click();
  await expect.poll(() => state.creationDrafts.get('101:storyboard_video')?.video_settings?.resolution).toBeNull();
  const durations = popup.getByRole('group', { name: '选择时长', exact: true });
  await expect(durations.getByRole('button')).toHaveCount(2);
  await expect(durations.getByRole('button', { name: '视频时长 3秒', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(durations.getByRole('button', { name: '视频时长 5秒', exact: true })).toBeEnabled();
  await page.keyboard.press('Escape'); await expect(output).toContainText('默认');
  expect(state.shots[0].video_settings).toEqual({ resolution: '720p', duration_ms: 3000 });
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});


test('main shot actions are direct and filtering preserves the selected draft', async ({ page }) => {
  const state = await layoutFixture(page);
  await page.goto(`${root}/storyboard?shot=101`);
  const more = panel(page).getByRole('button', { name: '分镜 01 更多操作', exact: true });
  await more.click();
  await expect(page.getByRole('menuitem', { name: '上移', exact: true })).toHaveAttribute('aria-disabled', 'true');
  await expect(page.getByRole('menuitem', { name: '下移', exact: true })).toBeVisible();
  await expect(page.getByRole('dialog', { name: '分镜生成记录', exact: true })).toHaveCount(0);
  await page.keyboard.press('Escape');
  const title = panel(page).getByRole('textbox', { name: '镜头名称', exact: true });
  const retained = await title.inputValue();
  await page.getByRole('textbox', { name: '搜索镜头名称', exact: true }).fill('没有这个镜头');
  await expect(page.getByText('已加载镜头中没有匹配项', { exact: true })).toBeVisible();
  await expect(title).toHaveValue(retained);
  await page.getByRole('button', { name: '清空筛选', exact: true }).click();
  await expect(page.locator('.storyboard-shot-card').first()).toBeVisible();
  await expect(title).toHaveValue(retained);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
