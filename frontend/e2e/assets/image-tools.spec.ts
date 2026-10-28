import { expect, test, type Locator } from '@playwright/test';
import { fixture, image, root } from '../support/studio-fixture';
import type { ImageGroup } from '../../src/api/modules/image-groups';

async function openToolDetails(dialog: Locator) {
  const trigger = dialog.getByRole('button', { name: /^提示详情：/ });
  if (await trigger.getAttribute('aria-expanded') !== 'true') await trigger.click();
  const details = dialog.locator('.studio-feedback-details');
  await expect(details).toBeVisible();
  return details;
}

function artwork(count: number, kind: 'asset' | 'shot' = 'asset'): ImageGroup {
  return { id: '600501', kind: 'generation', status: 'completed', target_kind: kind,
    target_id: kind === 'asset' ? '501' : '101', target_version: '1', is_current: true,
    expected_count: count, primary_index: 1, primary_version: '1', notice: null, task_id: null,
    created_at: '2026-10-16T00:00:00Z', items: Array.from({ length: count }, (_, index) => ({
      id: String(620501 + index), output_index: index + 1, media_id: String(9901 + index),
      url: image, width: 800, height: 600, item_version: '1', status: 'ready',
      error: null, prompt: '本人的图片提示词', history: [],
    })) };
}

for (const count of [1, 2, 4]) {
  test(`${count} images keep their own subjects, hover toolbars and immediate primary`, async ({ page }) => {
    const data = await fixture(page, true, String(test.info().project.use.baseURL));
    data.imageGroups.set('asset:501', artwork(count));
    await page.goto(`${root}/assets?asset=501`);
    const grid = page.locator('.episode-asset-detail .image-subject-grid');
    await expect(grid).toHaveAttribute('data-image-count', String(count));
    const subjects = grid.locator('.image-subject');
    await expect(subjects).toHaveCount(count);
    await expect(page.locator('.episode-asset-detail .image-group-caption')).toHaveCount(0);
    await page.locator('.standard-object-heading').hover();
    await expect(subjects.first().locator('.image-subject-toolbar')).toHaveCSS('opacity', '0');
    await subjects.first().hover();
    await expect(subjects.first().locator('.image-subject-toolbar')).toHaveCSS('opacity', '1');
    await expect(subjects.first().locator('.image-subject-open')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    await expect.poll(() => subjects.first().locator('img').evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0)).toBe(true);
    if (count === 4) await page.screenshot({ path: test.info().outputPath('desktop-four-images.png'), fullPage: true });
    await expect(subjects.first().locator('.image-primary-badge')).toHaveText('主图');
    if (count > 1) {
      await expect(subjects.nth(1).locator('.image-subject-toolbar')).toHaveCSS('opacity', '0');
      await subjects.nth(1).hover();
      await subjects.nth(1).getByRole('button', { name: '将第 2 张设为主图', exact: true }).click();
      await expect(subjects.nth(1).locator('.image-primary-badge')).toHaveText('主图');
      expect(data.asset.media_id).toBe('9902');
      expect(data.requests.filter(request => request.path.endsWith('/primary') && request.method === 'PUT')).toHaveLength(1);
    }
    expect(data.requests.some(request => request.path.endsWith('/confirm'))).toBe(false);
    expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
  });
}

test('changing count preserves artwork until admission then reserves four fixed positions', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  data.imageGroups.set('asset:501', artwork(1));
  await page.route('**/api/v1/ai/generations/image', route => {
    const group = artwork(4); group.status = 'generating'; group.primary_index = null;
    group.items = group.items.map(item => ({ ...item, media_id: null, url: null, status: 'generating' }));
    data.imageGroups.set('asset:501', group);
    return route.fulfill({ status: 202, json: { generation_id: '99001', service_type: 'image', status: 'queued', image_group_id: group.id } });
  });
  await page.goto(`${root}/assets?asset=501`);
  await page.getByRole('button', { name: '比例与分辨率', exact: true }).click();
  await page.getByRole('dialog', { name: '生成选项', exact: true }).getByRole('button', { name: '生成 4 张图片', exact: true }).click();
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.image_settings?.count).toBe(4);
  await page.keyboard.press('Escape');
  await expect(page.locator('.episode-asset-detail .image-subject')).toHaveCount(1);
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await expect(page.locator('.episode-asset-detail .image-subject')).toHaveCount(4);
  await expect(page.locator('.episode-asset-detail .image-group-state')).toHaveCount(0);
  await expect(page.getByText('第 4 张生成中…', { exact: true })).toBeVisible();
  expect(data.errors).toEqual([]);
});

for (const kind of ['asset', 'shot'] as const) {
  test(`empty ${kind} preview keeps its approved behavior before the first generation`, async ({ page }) => {
    const data = await fixture(page, true, String(test.info().project.use.baseURL));
    if (kind === 'shot') {
      data.shots[0].image = null;
      await page.goto(`${root}/storyboard`);
      await page.locator('.storyboard-shot-card').first().click();
    } else {
      Object.assign(data.asset, { image: null, media_id: null, state: 'unconfirmed' });
      await page.goto(`${root}/assets?asset=501`);
    }
    const grid = page.locator(kind === 'asset' ? '.episode-asset-detail .image-subject-grid' : '.shot-image-candidates .image-subject-grid');
    await expect(grid.locator('.image-subject')).toHaveCount(1);
    for (const count of [2, 4, 1]) {
      if (kind === 'asset') {
        await page.getByRole('button', { name: '比例与分辨率', exact: true }).click();
        await page.getByRole('dialog', { name: '生成选项', exact: true }).getByRole('button', { name: `生成 ${count} 张图片`, exact: true }).click();
        await expect.poll(() => data.creationDrafts.get('501:asset_image')?.image_settings?.count).toBe(count);
        await page.keyboard.press('Escape');
        await expect(grid).toHaveAttribute('data-image-count', '1');
        await expect(grid.locator('.image-subject-empty')).toHaveCount(1);
        await expect(grid.getByText('还没有生成图片', { exact: true })).toHaveCount(1);
        await expect(grid.locator('.image-subject-label, .image-subject-state p, .image-subject-state button, .image-subject-state svg')).toHaveCount(0);
      } else {
        await page.getByRole('button', { name: '比例与分辨率', exact: true }).click();
        await page.getByRole('dialog', { name: '生成选项', exact: true }).getByRole('button', { name: `生成 ${count} 张图片`, exact: true }).click();
        await expect.poll(() => data.creationDrafts.get('101:storyboard_image')?.image_settings?.count).toBe(count);
        await page.keyboard.press('Escape');
        await expect(grid).toHaveAttribute('data-image-count', '1');
        await expect(grid.locator('.image-subject-empty')).toHaveCount(1);
      }
    }
    expect(data.requests.some(request => request.method === 'POST' && request.path === '/ai/generations/image')).toBe(false);
    expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
  });
}

test('an asset preview retries its failed image in place without losing the other positions', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  const group = artwork(2);
  group.status = 'partial';
  group.items[1].url = `${String(test.info().project.use.baseURL)}/fixture-retry-image.png`;
  data.imageGroups.set('asset:501', group);
  let offline = true;
  let reads = 0;
  await page.route('**/fixture-retry-image.png', route => {
    reads++;
    return offline ? route.fulfill({ status: 503, body: '' }) : route.fulfill({ contentType: 'image/png', body: Buffer.from(image.split(',')[1], 'base64') });
  });
  await page.goto(`${root}/assets?asset=501`);
  const preview = page.locator('.episode-asset-detail .standard-image-group');
  await expect(preview.locator('.image-subject')).toHaveCount(2);
  await expect(preview.locator('.image-group-state')).toHaveCount(0);
  const saveStatus = page.getByRole('button', { name: /^保存状态：/ });
  await saveStatus.click();
  await expect(page.locator('.studio-feedback-details').getByText('图片部分完成', { exact: true })).toBeVisible();
  await saveStatus.click();
  const second = preview.locator('.image-subject').nth(1);
  await expect(second.getByText('预览加载失败', { exact: true })).toBeVisible();
  await expect(preview.locator('.image-subject').first().locator('img')).toBeVisible();
  offline = false;
  await second.getByRole('button', { name: '重新读取第 2 张图片', exact: true }).click();
  await expect.poll(() => second.locator('img').evaluate((node: HTMLImageElement) => node.complete && node.naturalWidth > 0)).toBe(true);
  await expect(preview.locator('.image-subject')).toHaveCount(2);
  await expect(preview.locator('.image-group-caption')).toHaveCount(0);
  expect(reads).toBeGreaterThanOrEqual(2);
  expect(data.requests.some(request => request.method === 'POST' && request.path === '/ai/generations/image')).toBe(false);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('image tools save one position with the original version and no model task for resize', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  const group = artwork(2); data.imageGroups.set('asset:501', group);
  let submitted: any = null; let executed = 0;
  await page.route('**/api/v1/image-operations', route => {
    submitted = route.request().postDataJSON();
    return route.fulfill({ status: 201, json: { id: '70001', ...submitted, status: 'pending' } });
  });
  await page.route('**/api/v1/image-operations/70001/execute', route => {
    executed++; group.items[1] = { ...group.items[1], width: 320, height: 240, item_version: '2', history: [{ media_id: '9902', url: image, width: 800, height: 600 }] };
    return route.fulfill({ json: { id: '70001', item_id: submitted.item_id, operation_type: 'resize', status: 'completed', task_id: null } });
  });
  await page.goto(`${root}/assets?asset=501`);
  const second = page.locator('.episode-asset-detail .image-subject').nth(1);
  await second.hover(); await second.getByRole('button', { name: '编辑第 2 张图片', exact: true }).click();
  await page.getByRole('menuitem', { name: '调整尺寸', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '调整尺寸', exact: true });
  await expect(dialog.getByRole('button', { name: '保存图片', exact: true })).toBeEnabled();
  await dialog.screenshot({ path: test.info().outputPath('desktop-resize-tool.png') });
  await dialog.getByRole('spinbutton', { name: '目标宽度', exact: true }).fill('320');
  await dialog.getByRole('button', { name: '保存图片', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(submitted).toMatchObject({ item_id: '620502', expected_item_version: '1', operation_type: 'resize', parameters: { output_width: 320, interpolation: 'high' } });
  expect(executed).toBe(1);
  expect(group.primary_index).toBe(1);
  expect(data.requests.some(request => request.method === 'POST' && request.path === '/ai/generations/image')).toBe(false);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('four-image shot preview is shared with asset tools and works at the minimum desktop width', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  data.imageGroups.set('shot:101', artwork(4, 'shot'));
  await page.setViewportSize({ width: 1280, height: 844 });
  await page.goto(`${root}/storyboard`);
  await page.locator('.storyboard-shot-card').first().click();
  const grid = page.locator('.shot-image-candidates .image-subject-grid');
  await expect(grid).toHaveAttribute('data-image-count', '4');
  await expect(grid.locator('.image-subject')).toHaveCount(4);
  await grid.locator('.image-subject').nth(1).focus();
  await expect(grid.locator('.image-subject').nth(1).locator('.image-subject-toolbar')).toHaveCSS('opacity', '1');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath('desktop-four-images.png'), fullPage: true });
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('an uncertain AI edit only reconciles the original key across a reload', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  data.imageGroups.set('asset:501', artwork(1));
  let submissions = 0; let lookups = 0;
  const operation = { id: '70001', item_id: '620501', operation_type: 'texture', status: 'prepared', task_id: null };
  await page.route('**/api/v1/image-operations', route => route.fulfill({ status: 201, json: operation }));
  await page.route('**/api/v1/image-operations/70001/execute', route => route.fulfill({ json: operation }));
  await page.route('**/api/v1/image-operations/70001', route => route.fulfill({ json: operation }));
  await page.route('**/api/v1/ai/generations/image', route => { submissions++; return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } }); });
  await page.route('**/api/v1/ai/generations/by-idempotency-key**', route => { lookups++; return route.fulfill({ json: null }); });
  async function openTexture() {
    const first = page.locator('.episode-asset-detail .image-subject').first();
    await first.hover(); await first.getByRole('button', { name: '编辑第 1 张图片', exact: true }).click();
    await page.getByRole('menuitem', { name: '质感', exact: true }).click();
    return page.getByRole('dialog', { name: '质感', exact: true });
  }
  await page.goto(`${root}/assets?asset=501`);
  let dialog = await openTexture();
  await expect(dialog.getByRole('button', { name: '开始处理', exact: true })).toBeEnabled();
  const originalWorkspace = await dialog.locator('.image-tools-workspace').boundingBox();
  await dialog.getByRole('button', { name: '开始处理', exact: true }).click();
  let details = await openToolDetails(dialog);
  await expect(dialog.getByRole('button', { name: '核对原处理', exact: true })).toBeVisible();
  await expect(dialog.getByRole('button', { name: '开始处理', exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: '核对原处理', exact: true }).click();
  await expect.poll(() => lookups).toBe(1);
  await expect(details.getByText('暂未查到原生成请求，请稍后再次核对；当前不会重新提交。', { exact: true })).toBeVisible();
  expect(await dialog.locator('.image-tools-workspace').boundingBox()).toEqual(originalWorkspace);
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.reload(); dialog = await openTexture();
  await expect(dialog.getByRole('button', { name: '开始处理', exact: true })).toBeDisabled();
  details = await openToolDetails(dialog);
  await dialog.getByRole('button', { name: '核对原处理', exact: true }).click();
  await expect.poll(() => lookups).toBe(2); expect(submissions).toBe(1);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('source read failure keeps resize disabled and can be retried', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  data.imageGroups.set('asset:501', artwork(1)); let offline = true;
  await page.route('**/api/v1/image-groups/media/9901/content', route => offline
    ? route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } })
    : route.fulfill({ contentType: 'image/png', body: Buffer.from(image.split(',')[1], 'base64') }));
  await page.goto(`${root}/assets?asset=501`);
  const first = page.locator('.episode-asset-detail .image-subject').first();
  await first.hover(); await first.getByRole('button', { name: '编辑第 1 张图片', exact: true }).click();
  await page.getByRole('menuitem', { name: '调整尺寸', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '调整尺寸', exact: true });
  await expect(dialog.getByText(/原图读取失败/)).toBeVisible();
  await expect(dialog.getByRole('button', { name: '保存图片', exact: true })).toBeDisabled();
  offline = false; await dialog.getByRole('button', { name: '重新读取原图', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '保存图片', exact: true })).toBeEnabled();
  await expect(dialog.getByText('原图 800 × 600', { exact: true })).toBeVisible();
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('crop and keyboard annotation save the chosen position and retain original history', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  const group = artwork(2); data.imageGroups.set('asset:501', group);
  const bodies: any[] = []; let annotationBytes: Buffer | null = null;
  await page.route('**/api/v1/image-operations', route => {
    bodies.push(route.request().postDataJSON());
    return route.fulfill({ status: 201, json: { id: '70001', ...bodies.at(-1), status: 'pending' } });
  });
  await page.route('**/api/v1/image-operations/70001/execute', route => {
    if (bodies.at(-1).operation_type === 'annotation') annotationBytes = route.request().postDataBuffer();
    group.items[1] = { ...group.items[1], item_version: String(bodies.length + 1), history: [{ media_id: '9902', url: image, width: 800, height: 600 }] };
    return route.fulfill({ json: { id: '70001', item_id: '620502', operation_type: bodies.at(-1).operation_type, status: 'completed', task_id: null } });
  });
  await page.goto(`${root}/assets?asset=501`);
  async function openTool(name: string) {
    const second = page.locator('.episode-asset-detail .image-subject').nth(1);
    await second.hover(); await second.getByRole('button', { name: '编辑第 2 张图片', exact: true }).click();
    await page.getByRole('menuitem', { name, exact: true }).click();
    const dialog = page.getByRole('dialog', { name, exact: true });
    await expect(dialog.getByRole('button', { name: '保存图片', exact: true })).toBeEnabled();
    return dialog;
  }
  let dialog = await openTool('裁切');
  await dialog.getByRole('spinbutton', { name: '选区宽度百分比', exact: true }).fill('50');
  await expect(dialog.getByText('裁切后 400 × 600；保留下次生成画幅设置。', { exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: '保存图片', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(bodies[0]).toMatchObject({ item_id: '620502', operation_type: 'crop', parameters: { width: .5, height: 1 } });
  dialog = await openTool('标注');
  await dialog.getByRole('button', { name: '在此添加笔点', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '撤销标注', exact: true })).toBeEnabled();
  await dialog.getByRole('button', { name: '撤销标注', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '重做标注', exact: true })).toBeEnabled();
  await dialog.getByRole('button', { name: '重做标注', exact: true }).click();
  await dialog.getByRole('button', { name: '保存图片', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(annotationBytes).not.toBeNull();
  expect(annotationBytes!.includes(Buffer.from('image/png'))).toBe(true);
  expect(bodies[1]).toMatchObject({ item_id: '620502', expected_item_version: '2', operation_type: 'annotation' });
  expect(group.primary_index).toBe(1);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('keyboard focus exposes image tools and opens crop on the desktop', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  data.imageGroups.set('asset:501', artwork(4));
  await page.goto(root + '/assets?asset=501');
  const subjects = page.locator('.episode-asset-detail .image-subject');
  await expect(subjects).toHaveCount(4);
  await subjects.nth(1).focus();
  await expect(subjects.nth(1).locator('.image-subject-toolbar')).toHaveCSS('opacity', '1');
  const edit = subjects.nth(1).getByRole('button', { name: '编辑第 2 张图片', exact: true });
  await edit.focus(); await edit.press('Enter');
  const crop = page.getByRole('menuitem', { name: '裁切', exact: true });
  await expect(crop).toBeVisible(); await crop.focus(); await crop.press('Enter');
  const dialog = page.getByRole('dialog', { name: '裁切', exact: true });
  await expect(dialog.getByRole('button', { name: '保存图片', exact: true })).toBeEnabled();
  await page.screenshot({ path: test.info().outputPath('keyboard-crop-tool.png'), fullPage: true });
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});


test('edit and more stay one level and historical groups omit editing', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  data.imageGroups.set('asset:501', artwork(2));
  await page.goto(`${root}/assets?asset=501`);
  const subject = page.locator('.episode-asset-detail .image-subject').first();
  await subject.hover();
  await subject.getByRole('button', { name: '编辑第 1 张图片', exact: true }).click();
  await expect(page.getByRole('menuitem')).toHaveCount(10);
  await expect(page.getByRole('menuitem', { name: '裁切', exact: true })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: '下载图片', exact: true })).toHaveCount(0);
  await page.keyboard.press('Escape');
  await subject.getByRole('button', { name: '第 1 张图片更多工具', exact: true }).click();
  await expect(page.getByRole('menuitem', { name: '下载图片', exact: true })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: '看图反推', exact: true })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: '裁切', exact: true })).toHaveCount(0);
  await page.keyboard.press('Escape');
  await page.locator('.episode-asset-detail').getByRole('button', { name: '生成记录', exact: true }).click();
  const history = page.locator('.asset-generation-history-dialog');
  const historicSubject = history.locator('.image-subject').first();
  await historicSubject.hover();
  await expect(historicSubject.getByRole('button', { name: '编辑第 1 张图片', exact: true })).toHaveCount(0);
  await historicSubject.getByRole('button', { name: '第 1 张图片更多工具', exact: true }).click();
  await expect(page.getByRole('menuitem', { name: '下载图片', exact: true })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: '看图反推', exact: true })).toHaveCount(0);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});


for (const viewport of [{ width: 1440, height: 600 }, { width: 1280, height: 720 }]) {
  test(`tool dialogs keep their footer available at ${viewport.width}px`, async ({ page }, info) => {
    test.setTimeout(90_000);
    const data = await fixture(page, true, String(test.info().project.use.baseURL));
    data.imageGroups.set('asset:501', artwork(1));
    await page.setViewportSize(viewport);
    await page.goto(`${root}/assets?asset=501`);
    const subject = page.locator('.episode-asset-detail .image-subject').first();
    for (const name of ['上传替换', '裁切', '标注', '调整尺寸', '宫格切分', '局部重绘', '表情', '质感', '多角度', '打光', '看图反推']) {
      await subject.hover();
      await subject.getByRole('button', { name: name === '看图反推' ? '第 1 张图片更多工具' : '编辑第 1 张图片', exact: true }).click();
      await page.getByRole('menuitem', { name, exact: true }).click();
      const dialog = page.getByRole('dialog', { name, exact: true });
      await expect(dialog).toHaveClass(/standard-creation-dialog/);
      await expect(dialog.getByRole('button', { name: /^(开始处理|保存图片|切分并保存)$/ })).toBeInViewport({ ratio: 1 });
      await expect(dialog.getByRole('button', { name: '关闭', exact: true })).toBeInViewport({ ratio: 1 });
      expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
      await dialog.screenshot({ path: info.outputPath(`image-tool-${name}-${viewport.width}.png`) });
      for (const select of await dialog.getByRole('combobox').all()) {
        await select.scrollIntoViewIfNeeded();
        await select.locator('xpath=../..').click();
        const popup = dialog.locator('.ant-select-dropdown:visible');
        await expect(popup).toBeVisible();
        await popup.locator('.ant-select-item-option:not(.ant-select-item-option-disabled)').first().click();
        await expect(popup).toBeHidden();
      }
      await dialog.getByRole('button', { name: '关闭弹窗', exact: true }).click();
    }
    expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
  });
}
