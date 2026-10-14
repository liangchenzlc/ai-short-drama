import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';

async function layoutFixture(page: Page) {
  const state = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  state.shots[0].script = '中景，林晚停在雨夜中的站台。'.repeat(16);
  state.shots[0].title = '雨夜站台上等待来信主人的林晚'.repeat(5);
  state.shots[0].shot_size = 'medium_shot';
  state.shots[1].image.is_stale = true;
  return state;
}

const panel = (page: Page) => page.locator('.standard-storyboard-panel');

async function expectCardLayout(page: Page) {
  const bounds = await page.locator('.storyboard-shot-card').first().evaluate(card => {
    const summary = card.querySelector('.storyboard-summary')!.getBoundingClientRect();
    const media = card.querySelector('.storyboard-card-media')!.getBoundingClientRect();
    const copy = card.querySelector('.storyboard-card-copy')!.getBoundingClientRect();
    const heading = card.querySelector('.storyboard-card-heading')!.getBoundingClientRect();
    return { summary: { width: summary.width, height: summary.height }, media: { height: media.height, bottom: media.bottom }, copy: { width: copy.width, height: copy.height, top: copy.top, left: copy.left, right: copy.right }, heading: { left: heading.left, right: heading.right } };
  });
  expect(Math.abs(bounds.copy.height - 38)).toBeLessThanOrEqual(0.5);
  expect(Math.abs(bounds.copy.width - bounds.summary.width)).toBeLessThanOrEqual(0.5);
  expect(Math.abs(bounds.media.bottom - bounds.copy.top)).toBeLessThanOrEqual(0.5);
  expect(Math.abs(bounds.media.height + bounds.copy.height - bounds.summary.height)).toBeLessThanOrEqual(0.5);
  expect(Math.abs(bounds.heading.left - bounds.copy.left - 10)).toBeLessThanOrEqual(0.5);
  expect(Math.abs(bounds.copy.right - bounds.heading.right - 10)).toBeLessThanOrEqual(0.5);
}

async function expectMobileHeaderFits(page: Page) {
  const heading = panel(page).locator('.standard-object-heading');
  const bounds = (await heading.boundingBox())!;
  const actions = heading.locator('nav .ant-btn');
  await expect(actions).toHaveCount(4);
  for (const action of await actions.all()) {
    await expect(action).toBeInViewport({ ratio: 0.99 });
    const box = (await action.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(bounds.x);
    expect(box.x + box.width).toBeLessThanOrEqual(bounds.x + bounds.width);
  }
  const tabs = (await panel(page).getByRole('tablist').boundingBox())!;
  const navigation = (await heading.getByRole('navigation').boundingBox())!;
  expect(tabs.y).toBeGreaterThanOrEqual(navigation.y + navigation.height);
  const listHeading = page.locator('.standard-storyboard-list .storyboard-editor-heading');
  expect(Math.abs((await listHeading.boundingBox())!.height - 52)).toBeLessThanOrEqual(0.5);
}

test('fixed-height cards retain full accessible scripts and separate image and video states', async ({ page }, info) => {
  const state = await layoutFixture(page);
  await page.goto(`${root}/storyboard`);
  const cards = page.locator('.storyboard-shot-card');
  await expect(cards).toHaveCount(20);
  await expect(cards.first().locator('.storyboard-card-media img')).toBeVisible();
  await expect(cards.nth(3).getByText('尚无采用画面', { exact: true })).toBeVisible();
  await expect(cards.nth(3).getByText('待制作', { exact: true })).toBeVisible();
  const firstCardButton = cards.first().getByRole('button', { name: '选择分镜 01', exact: true });
  await expect(firstCardButton).toHaveAccessibleDescription(/中景，林晚停在雨夜中的站台。/);
  await expect(firstCardButton).toHaveAccessibleDescription(new RegExp(`${state.shots[0].duration_ms / 1000}\\s*秒`));
  await expect(firstCardButton).toHaveAccessibleDescription(/图片：已采用[\s\S]*视频：待制作/);
  await expect(cards.nth(1).getByRole('button', { name: '选择分镜 02', exact: true })).toHaveAccessibleDescription(/图片：需核对/);
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
  await expect(panel(page).getByText('创作内容已变化，请核对当前图片。', { exact: true })).toBeVisible();
  await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).click();
  await expect(cards.first().getByText('待制作', { exact: true })).toBeVisible();
  await expect(panel(page).getByText('这个镜头还没有视频', { exact: true })).toBeVisible();
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
  const count = panel(page).getByRole('combobox', { name: '生成张数', exact: true });
  await count.focus(); await page.keyboard.press('Enter');
  await page.locator('.ant-select-item-option').filter({ hasText: /^4 张$/ }).click();
  await panel(page).getByRole('tab', { name: '分镜图', exact: true }).press('ArrowRight');
  await expect(panel(page).getByRole('tab', { name: '分镜视频', exact: true })).toBeFocused();
  await expect(input).toHaveValue('固定机位，林晚收起旧信。');
  await expect(panel(page).getByRole('textbox', { name: '视频提示词', exact: true })).toBeVisible();
  await panel(page).getByRole('textbox', { name: '视频提示词', exact: true }).fill('保留个人视频输入');
  await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).press('ArrowLeft');
  await expect(panel(page).getByRole('tab', { name: '分镜图', exact: true })).toBeFocused();
  await expect(panel(page).getByRole('textbox', { name: '画面要求', exact: true })).toHaveValue('保留个人分镜图输入');
  await expect(panel(page).locator('.ant-select').filter({ has: page.getByRole('combobox', { name: '生成张数', exact: true }) })).toContainText('4 张');
  await expect.poll(() => state.shots[0].script).toBe('固定机位，林晚收起旧信。');
  await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).click();
  await expect(panel(page).getByRole('textbox', { name: '视频提示词', exact: true })).toHaveValue('保留个人视频输入');
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('batch selection is explicit and stays selected when a card opens', async ({ page }) => {
  const state = await layoutFixture(page);
  await page.goto(`${root}/storyboard`);
  await page.getByRole('button', { name: '分镜生成记录', exact: true }).click();
  const more = page.getByRole('button', { name: /更多操作$/ });
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

test('project assistant retains its draft while shot conflicts block navigation until explicitly resolved', async ({ page }) => {
  const state = await layoutFixture(page);
  page.on('console', message => { if (message.text().includes('Encountered two children with the same key')) state.errors.push(message.text()); });
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: '9007199254740993', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true } } }));
  const conversation = { id: '301', project_id: '10', episode_id: null, title: '项目对话', stage: null, subject_type: null, subject_id: null, task_type: null, scope_version: 2, archived: false, row_version: '1', last_run_status: null, created_at: '2026-10-06T00:00:00Z', updated_at: '2026-10-06T00:00:00Z' };
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
    if (path === '/conversations') return route.fulfill({ json: { items: conversations, total: conversations.length, offset: 0, limit: 20 } });
    if (/^\/conversations\/\d+$/.test(path)) return route.fulfill({ json: conversations.find(item => item.id === path.split('/').pop()) });
    if (path.endsWith('/state')) return route.fulfill({ json: { conversation_id: path.split('/')[2], cursor: 0, active_run: null, queued_runs: [] } });
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
  const stages = page.getByRole('navigation', { name: '分集制作流程' });
  await stages.getByRole('button', { name: /素材生成/ }).click();
  const confirmation = page.getByRole('dialog', { name: '未保存的修改', exact: true });
  await expect(confirmation).toBeVisible();
  await confirmation.getByRole('button', { name: '取消', exact: true }).click();
  await expect(script).toHaveValue('发生冲突仍保留的镜头草稿');
  await expect(page.locator('#storyboard-image-pane').getByText('镜头保存已暂停，输入保留。', { exact: true })).toBeVisible();
  await expect(composer).toHaveValue('保留这段项目对话草稿');
  await expect(page).toHaveURL(url => url.pathname.endsWith('/storyboard'));
  conflict = false;
  await page.getByRole('button', { name: '载入服务端镜头', exact: true }).click();
  await page.getByRole('button', { name: '确认继续', exact: true }).click();
  await expect(script).toHaveValue(state.shots[0].script);
  await script.fill('发生冲突仍保留的镜头草稿');
  await expect.poll(() => state.shots[0].script).toBe('发生冲突仍保留的镜头草稿');
  await stages.getByRole('button', { name: /素材生成/ }).click();
  await expect(page).toHaveURL(url => url.pathname.endsWith('/assets') && url.searchParams.get('conversation') === '301');
  await expect(composer).toHaveValue('保留这段项目对话草稿');
  expect(conversations).toHaveLength(1);
  expect(state.shots[0].script).toBe('发生冲突仍保留的镜头草稿');
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

for (const width of [1440, 1024, 390, 320]) test(`cards and the selected-shot panel fit at ${width}px`, async ({ page }, info) => {
  const state = await layoutFixture(page);
  await page.setViewportSize({ width, height: 1000 });
  await page.goto(`${root}/storyboard`);
  await expect(page.locator('.storyboard-shot-card')).toHaveCount(20);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.locator('.storyboard-summary').first().click();
  await expectCardLayout(page);
  if (width <= 700) {
    await expectMobileHeaderFits(page);
    await page.getByRole('button', { name: '展开生成设置', exact: true }).click();
  }
  await expect(page.getByRole('textbox', { name: '分镜 1 脚本', exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath(`selected-shot-${width}.png`), fullPage: true });
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});


test('storyboard follows the prototype heading, media tabs and settings structure', async ({ page }) => {
  const state = await layoutFixture(page);
  state.shots[0].title = '雨夜站台';
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${root}/storyboard`);
  await page.locator('.storyboard-summary').first().click();
  const workspace = panel(page);
  const heading = workspace.locator('.standard-object-heading');
  await expect(heading.getByRole('heading', { name: '01 · 雨夜站台', exact: true })).toBeVisible();
  await expect(heading.getByText('当前采用', { exact: true })).toBeVisible();
  const tabs = workspace.getByRole('tablist', { name: '镜头制作内容', exact: true });
  await expect(heading.getByRole('tablist')).toHaveCount(0);
  const headingBox = (await heading.boundingBox())!;
  const tabsBox = (await tabs.boundingBox())!;
  expect(Math.abs(tabsBox.y - headingBox.y - headingBox.height)).toBeLessThanOrEqual(1);
  expect(Math.abs(tabsBox.width - headingBox.width)).toBeLessThanOrEqual(1);
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
  await expect(workspace.getByRole('button', { name: '缩小预览', exact: true })).toBeVisible();
  await expect(workspace.getByRole('button', { name: '放大预览', exact: true })).toBeVisible();
  await expect(workspace.getByRole('button', { name: '适合窗口', exact: true })).toBeVisible();
  await workspace.getByRole('button', { name: '图片生成记录', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '分镜图片生成记录', exact: true })).toBeVisible();
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
  const picker = page.getByRole('dialog', { name: '选择已采用的参考素材', exact: true });
  await expect(picker).toBeVisible();
  await picker.getByRole('checkbox', { name: '林晚', exact: true }).check();
  await picker.getByRole('button', { name: '确认选择', exact: true }).click();
  await expect(picker).toHaveCount(0);
  await expect.poll(() => state.creationDrafts.get('101:storyboard_image')?.reference_media_ids).toEqual(['9901']);
  expect(state.shots[0].asset_ids).toEqual([]);
  await expect(references.locator('.standard-reference-count')).toHaveText('1 / 16');
  await expect(references.getByRole('button', { name: '移除个人参考图片', exact: true })).toBeVisible();
  await references.getByRole('button', { name: '移除个人参考图片', exact: true }).click();
  await expect.poll(() => state.creationDrafts.get('101:storyboard_image')?.reference_media_ids).toEqual([]);
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
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
  await imagePopup.getByRole('button', { name: '画面比例 9:16', exact: true }).click();
  await imagePopup.locator('.standard-image-resolutions').getByRole('button').filter({ hasText: /^4K$/ }).click();
  await expect.poll(() => state.creationDrafts.get('101:storyboard_image')?.image_settings).toMatchObject({ aspect: '9:16', resolution: '4K', count: 1 });
  await imageOutput.click();
  await panel(page).getByRole('tab', { name: '分镜视频', exact: true }).click();
  const videoPane = panel(page).locator('#storyboard-video-pane');
  const videoOutput = videoPane.getByRole('button', { name: '视频比例与分辨率', exact: true });
  await expect(videoOutput).toBeEnabled(); await videoOutput.click();
  const videoPopup = page.getByRole('dialog', { name: '视频比例与分辨率设置', exact: true });
  await expect(videoPopup.getByRole('heading', { name: '比例与分辨率', exact: true })).toBeVisible();
  await expect(videoPopup.getByRole('button', { name: '关闭生成选项', exact: true })).toBeVisible();
  await videoPopup.getByRole('button', { name: '视频比例 4:3', exact: true }).click();
  await videoPopup.getByRole('button', { name: '视频分辨率 1080p', exact: true }).click();
  await videoPopup.getByRole('button', { name: '视频分辨率 1080p', exact: true }).press('Escape');
  await expect(videoOutput).toBeFocused(); await expect(videoPopup).toBeHidden();
  await videoOutput.click();
  await videoPopup.getByRole('button', { name: '关闭生成选项', exact: true }).click();
  await expect(videoOutput).toBeFocused(); await expect(videoPopup).toBeHidden();
  const duration = videoPane.getByRole('combobox', { name: '视频时长', exact: true });
  await duration.focus(); await page.keyboard.press('Enter');
  await page.locator('.ant-select-item-option').filter({ hasText: /^5秒$/ }).click();
  await expect.poll(() => state.creationDrafts.get('101:storyboard_video')?.video_settings).toMatchObject({ aspect: '4:3', resolution: '1080p', duration_ms: 5000 });
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
  const popup = page.getByRole('dialog', { name: '视频比例与分辨率设置', exact: true });
  await expect(popup.getByRole('button', { name: '视频分辨率 480p', exact: true })).toBeDisabled();
  await expect(popup.getByRole('button', { name: '视频分辨率 1080p', exact: true })).toBeDisabled();
  await expect(popup.getByRole('button', { name: '视频分辨率 720p', exact: true })).toBeEnabled();
  await expect(popup.getByRole('button', { name: '视频比例 2:3', exact: true })).toBeDisabled();
  await expect(popup.getByRole('button', { name: '视频比例 3:2', exact: true })).toBeDisabled();
  await popup.getByRole('button', { name: '使用模型默认视频分辨率', exact: true }).click();
  await expect.poll(() => state.creationDrafts.get('101:storyboard_video')?.video_settings?.resolution).toBeNull();
  await output.click(); await expect(output).toContainText('默认');
  const duration = videoPane.getByRole('combobox', { name: '视频时长', exact: true });
  await duration.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')).toHaveCount(2);
  expect(state.shots[0].video_settings).toEqual({ resolution: '720p', duration_ms: 3000 });
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});
