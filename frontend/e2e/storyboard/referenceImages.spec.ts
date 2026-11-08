import { expect, test } from '@playwright/test';
import { fixture as studioFixture, image, root } from '../support/studio-fixture';

const fixture = '/e2e/storyboard/fixtures/referenceImages.html';

test('关联素材和缺图占位共享一组编号，新增关联后按媒体重映射提示词', async ({ page }) => {
  await page.goto(fixture);
  const references = page.getByRole('region', { name: '参考图片', exact: true });
  await expect(references.locator('.standard-reference-count')).toHaveText('3 / 16');
  await expect(page.getByLabel('图片顺序')).toHaveText('9901,9801,9903');
  await expect(references.getByText('缺图', { exact: true })).toBeVisible();
  await expect(references.locator('figcaption')).toHaveText(['图片1', '图片2', '图片3']);
  await references.getByRole('button', { name: '添加参考图片', exact: true }).click();
  await page.getByRole('button', { name: '关联镜头素材', exact: true }).click();
  await page.getByRole('checkbox', { name: '分镜图参考：雨夜车站', exact: true }).check();
  await expect(page.getByLabel('关联回调')).toHaveText('502:true');
  await expect(page.getByLabel('图片顺序')).toHaveText('9901,9902,9801,9903');
  await expect(page.getByLabel('提示词')).toHaveText('关联 @图片1 共享 @图片3 个人 @图片4');
  await page.keyboard.press('Escape');
  await references.getByRole('button', { name: '移除分镜图参考：未确认道具', exact: true }).click();
  await expect(page.getByLabel('关联素材')).toHaveText('501,502');
  await expect(references.getByText('缺图', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('图片顺序')).toHaveText('9901,9902,9801,9903');
});

test('同一媒体的个人参考和关联素材各自移除，仍保留其他来源及引用', async ({ page }) => {
  await page.goto(fixture);
  const references = page.getByRole('region', { name: '参考图片', exact: true });
  await expect(references.locator('.standard-reference-count')).toHaveText('3 / 16');
  await references.getByRole('button', { name: '参考图片 9901 的来源', exact: true }).click();
  await page.getByRole('button', { name: '移除个人参考图片 9901', exact: true }).click();
  await expect(page.getByLabel('个人参考', { exact: true })).toHaveText('9903');
  await expect(page.getByLabel('图片顺序')).toHaveText('9901,9801,9903');
  await expect(page.getByLabel('提示词')).toHaveText('关联 @图片1 共享 @图片2 个人 @图片3');
  await references.getByRole('button', { name: '添加参考图片', exact: true }).click();
  await page.getByRole('button', { name: '添加个人参考图片', exact: true }).click();
  const picker = page.getByRole('dialog', { name: '选择素材主图作为参考', exact: true });
  await picker.getByRole('checkbox', { name: '林晚', exact: true }).check();
  await picker.getByRole('button', { name: '确认选择', exact: true }).click();
  await expect(page.getByLabel('个人参考', { exact: true })).toHaveText('9903,9901');
  await expect(references.locator('.standard-reference-count')).toHaveText('3 / 16');
  await references.getByRole('button', { name: '参考图片 9901 的来源', exact: true }).click();
  await page.getByRole('button', { name: '移除分镜图参考：林晚', exact: true }).click();
  await expect(page.getByLabel('关联回调')).toHaveText('501:false');
  await expect(page.getByLabel('图片顺序')).toHaveText('9801,9903,9901');
  await expect(page.getByLabel('提示词')).toHaveText('关联 @图片3 共享 @图片1 个人 @图片2');
});

test('同媒体的多个关联素材可单独移除', async ({ page }) => {
  await page.goto(fixture);
  const references = page.getByRole('region', { name: '参考图片', exact: true });
  await references.getByRole('button', { name: '添加参考图片', exact: true }).click();
  await page.getByRole('button', { name: '关联镜头素材', exact: true }).click();
  await page.getByRole('checkbox', { name: '分镜图参考：同图角色', exact: true }).check();
  await page.keyboard.press('Escape');
  await expect(references.locator('.standard-reference-count')).toHaveText('3 / 16');
  await references.getByRole('button', { name: '参考图片 9901 的来源', exact: true }).click();
  await page.getByRole('button', { name: '移除分镜图参考：同图角色', exact: true }).click();
  await expect(page.getByRole('button', { name: '移除个人参考图片 9901', exact: true })).toBeHidden();
  await expect(page.getByLabel('关联素材')).toHaveText('501,503');
  await expect(page.getByLabel('图片顺序')).toHaveText('9901,9801,9903');
  await expect(references.getByRole('button', { name: '移除个人参考图片 9801', exact: true })).toHaveCount(0);
});

test('同媒体保留共享手动来源，不随关联和个人来源删除', async ({ page }) => {
  await page.goto(`${fixture}?duplicateShared=1`);
  const references = page.getByRole('region', { name: '参考图片', exact: true });
  await expect(references.locator('.standard-reference-count')).toHaveText('3 / 16');
  await references.getByRole('button', { name: '参考图片 9901 的来源', exact: true }).click();
  await page.getByRole('button', { name: '移除个人参考图片 9901', exact: true }).click();
  await references.getByRole('button', { name: '移除分镜图参考：林晚', exact: true }).click();
  await expect(page.getByLabel('图片顺序')).toHaveText('9801,9901,9903');
  await expect(page.getByLabel('提示词')).toHaveText('关联 @图片2 共享 @图片1 个人 @图片3');
  await expect(references.locator('.standard-reference-count')).toHaveText('3 / 16');
  await expect(references.getByRole('button', { name: /移除.*9901/ })).toHaveCount(0);
});

test('达到上限时仍可添加已有媒体的其他来源和缺图关联', async ({ page }) => {
  await page.goto(`${fixture}?full=1`);
  const references = page.getByRole('region', { name: '参考图片', exact: true });
  await expect(references.locator('.standard-reference-count')).toHaveText('16 / 16');
  await references.getByRole('button', { name: '添加参考图片', exact: true }).click();
  await page.getByRole('button', { name: '关联镜头素材', exact: true }).click();
  const picker = page.getByRole('dialog', { name: '关联镜头素材', exact: true });
  await expect(picker.getByRole('checkbox', { name: '分镜图参考：雨夜车站', exact: true })).toBeDisabled();
  await picker.getByRole('checkbox', { name: '分镜图参考：同图角色', exact: true }).check();
  await picker.getByRole('checkbox', { name: '分镜图参考：无图布景', exact: true }).check();
  await picker.getByRole('button', { name: '完成', exact: true }).click();
  await expect(references.locator('.standard-reference-count')).toHaveText('16 / 16');
  await references.getByRole('button', { name: '添加参考图片', exact: true }).click();
  await page.getByRole('button', { name: '添加个人参考图片', exact: true }).click();
  const personal = page.getByRole('dialog', { name: '选择素材主图作为参考', exact: true });
  await expect(personal.getByRole('checkbox', { name: '可添加图片', exact: true })).toBeDisabled();
  await expect(personal.getByRole('button', { name: '上传本地参考图片', exact: true })).toBeDisabled();
  await personal.getByRole('checkbox', { name: '林晚', exact: true }).check();
  await personal.getByRole('button', { name: '确认选择', exact: true }).click();
  await expect(references.locator('.standard-reference-count')).toHaveText('16 / 16');
  await expect(page.getByLabel('个人参考', { exact: true })).toContainText('9901');
});

test('本地上传和移除只修改个人参考', async ({ page }) => {
  await page.route('**/api/v1/generation-references/uploads', route => route.fulfill({
    status: 201, json: { media_id: '9910', name: '本地图片', url: image },
  }));
  await page.goto(fixture);
  const references = page.getByRole('region', { name: '参考图片', exact: true });
  await references.getByRole('button', { name: '添加参考图片', exact: true }).click();
  await page.getByRole('button', { name: '添加个人参考图片', exact: true }).click();
  const picker = page.getByRole('dialog', { name: '选择素材主图作为参考', exact: true });
  await picker.locator('input[type=file]').setInputFiles({ name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from('fixture') });
  await expect(page.getByLabel('个人参考', { exact: true })).toHaveText('9901,9903,9910');
  await expect(page.getByLabel('关联素材')).toHaveText('501,503');
  await expect(page.getByLabel('图片顺序')).toHaveText('9901,9801,9903,9910');
  await references.getByRole('button', { name: '移除个人参考图片 9910', exact: true }).click();
  await expect(page.getByLabel('个人参考', { exact: true })).toHaveText('9901,9903');
  await expect(page.getByLabel('图片顺序')).toHaveText('9901,9801,9903');
});

test('正式分镜接线保留媒体顺序并只在新增关联时加入视频参考', async ({ page }, info) => {
  test.setTimeout(60_000);
  const state = await studioFixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const scene = { ...state.asset, id: '502', kind: 'scene', name: '雨夜车站', media_id: '9902', image: { media_id: '9902', url: image } };
  const missing = { ...state.asset, id: '503', kind: 'prop', name: '泛黄的旧信', state: 'unconfirmed', media_id: null, image: null };
  state.shots[0].asset_ids = ['501', '503'];
  state.referenceState['shot/101'] = [{ media_id: '9801', name: '共享示意图', url: image }];
  state.creationDrafts.set('101:storyboard_image', {
    id: '95001', project_id: '10', object_id: '101', purpose: 'storyboard_image', model_config_id: '77',
    input_text: '关联 @图片1 共享 @图片2 个人 @图片3', image_settings: { aspect: '16:9', resolution: '2K', count: 1 },
    video_settings: null, reference_media_ids: ['9901', '9903'],
    reference_images: [{ media_id: '9901', url: image }, { media_id: '9903', url: image }],
    row_version: '1', draft_version: '1', source_version: '1',
  });
  state.creationDrafts.set('101:storyboard_video', {
    id: '95002', project_id: '10', object_id: '101', purpose: 'storyboard_video', model_config_id: '77',
    input_text: '', image_settings: null, video_settings: { resolution: '720p', duration_ms: 3000 },
    reference_media_ids: [], video_references: [], row_version: '1', draft_version: '1', source_version: '1',
  });
  await page.route(`**/api/v1${root}/assets?**`, route => route.fulfill({ json: { items: [state.asset, scene, missing], total: 3, offset: 0, limit: 100 } }));
  await page.route('**/api/v1/assets/502', route => route.fulfill({ json: scene }));
  await page.route('**/api/v1/assets/503', route => route.fulfill({ json: missing }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${root}/storyboard?shot=101`);
  const workspace = page.getByRole('region', { name: '分镜工作区', exact: true });
  const references = workspace.getByRole('region', { name: '参考图片', exact: true });
  await expect(references.locator('.standard-reference-count')).toHaveText('3 / 16');
  await expect(references.getByText('缺图', { exact: true })).toBeVisible();
  await expect(workspace.locator('.shotImageReferences')).toHaveCount(0);
  for (const selector of ['.standard-storyboard-list .storyboard-list', '.storyboardView .storyboardDetails']) {
    const scrolling = await page.locator(selector).evaluate((element) => {
      const previous = element.scrollTop;
      element.scrollTop = 40;
      const result = { position: element.scrollTop, display: getComputedStyle(element, '::-webkit-scrollbar').display };
      element.scrollTop = previous;
      return result;
    });
    expect(scrolling).toEqual({ position: 40, display: 'none' });
  }
  await page.getByRole('button', { name: 'AI 创作助手', exact: true }).click();
  const assistant = page.getByRole('complementary', { name: 'AI 创作助手区域' });
  await expect(assistant).toBeVisible();
  await expect(assistant).not.toContainText('正在载入 AI 创作助手', { timeout: 15_000 });
  await references.scrollIntoViewIfNeeded();
  await expect(references).toBeInViewport();
  await page.screenshot({ path: info.outputPath('统一分镜参考区-1440-助手开.png'), animations: 'disabled' });
  await page.getByRole('button', { name: '关闭助手', exact: true }).click();
  await references.getByRole('button', { name: '添加参考图片', exact: true }).click();
  await page.getByRole('button', { name: '关联镜头素材', exact: true }).click();
  const picker = page.getByRole('dialog', { name: '关联镜头素材', exact: true });
  await picker.getByRole('checkbox', { name: '分镜图参考：雨夜车站', exact: true }).check();
  await expect.poll(() => state.shots[0].asset_ids).toEqual(['501', '503', '502']);
  await expect.poll(() => state.creationDrafts.get('101:storyboard_video')?.video_references).toEqual([{ type: 'asset', asset_id: '502' }]);
  await expect.poll(() => state.creationDrafts.get('101:storyboard_image')?.input_text).toBe('关联 @图片1 共享 @图片3 个人 @图片4');
  await picker.getByRole('button', { name: '完成', exact: true }).click();
  await expect(references.locator('.standard-reference-count')).toHaveText('4 / 16');
  await page.reload();
  await expect(workspace.getByRole('button', { name: '移除参考：雨夜车站', exact: true })).toBeVisible();
  await expect(references.locator('.standard-reference-count')).toHaveText('4 / 16');
  await workspace.getByRole('button', { name: '移除参考：雨夜车站', exact: true }).click();
  await expect.poll(() => state.creationDrafts.get('101:storyboard_video')?.video_references).toEqual([]);
  await workspace.getByRole('button', { name: '宫格展示', exact: true }).click();
  await workspace.getByRole('button', { name: '单卡展示', exact: true }).click();
  await expect(workspace.getByRole('button', { name: '移除参考：雨夜车站', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '选择分镜 02', exact: true }).click();
  await page.getByRole('button', { name: '选择分镜 01', exact: true }).click();
  await expect(workspace.getByRole('button', { name: '移除参考：雨夜车站', exact: true })).toHaveCount(0);
  await page.reload();
  await expect(workspace.getByRole('button', { name: '移除参考：雨夜车站', exact: true })).toHaveCount(0);
  await expect(workspace.getByRole('region', { name: '参考图片', exact: true }).locator('.standard-reference-count')).toHaveText('4 / 16');
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});
