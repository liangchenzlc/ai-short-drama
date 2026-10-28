import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from '../support/studio-fixture';
import { captureRefinement, refinementViewports } from '../support/refinement-visual';

test('script stage keeps story tabs and retains edits when continuing to assets', async ({ page }) => {
  const data = await fixture(page, true);
  await page.goto(`${root}/source`);
  await expect(page.locator('.sd-doc-head').getByRole('button')).toHaveCount(0);
  const editor = page.getByRole('textbox', { name: '剧本正文', exact: true });
  await editor.fill('进入下一阶段前保存的正文');
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '资产生成', exact: true }).click();
  await expect.poll(() => data.writing.editing_script.content).toBe('进入下一阶段前保存的正文');
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '剧本创作', exact: true }).click();
  await expect(editor).toHaveValue('进入下一阶段前保存的正文');
  expect(data.errors).toEqual([]);
});

test('storyboard history remains reachable with creation at the sidebar bottom', async ({ page }) => {
  const data = await fixture(page, true);
  await page.goto(`${root}/storyboard`);
  await page.getByRole('button', { name: '分镜生成记录', exact: true }).click();
  const history = page.getByRole('dialog', { name: '分镜生成记录', exact: true });
  await expect(history).toBeVisible();
  await history.getByRole('button', { name: '关闭', exact: true }).click();
  await expect(history).toHaveCount(0);
  await expect(page.getByRole('button', { name: '创建分镜', exact: true })).toBeInViewport();
  await expect(page.getByRole('button', { name: '提取分镜', exact: true })).toHaveCount(0);
  expect(data.errors).toEqual([]);
});

test('asset cards select the shared preview and settings panel without a details modal', async ({ page }) => {
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.goto(`${root}/assets`);
  const card = page.locator('.standard-asset-card').first();
  const detail = page.locator('.creation-asset-detail');
  await expect(card.getByRole('button', { name: '编辑素材', exact: true })).toHaveCount(0);
  await card.getByRole('button', { name: '林晚', exact: true }).click();
  await expect(detail).toBeVisible();
  await detail.locator('.image-subject-open').click();
  await expect(page.getByRole('dialog', { name: '图片预览', exact: true })).toBeVisible();
  await expect(detail).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(card.getByRole('button', { name: '林晚更多操作', exact: true })).toHaveCount(0);
  await detail.getByRole('button', { name: '生成记录', exact: true }).click();
  await page.locator('.asset-generation-history-dialog').getByRole('button', { name: '素材更多操作', exact: true }).click();
  await expect(page.getByRole('button', { name: '从资产库选择', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '删除素材', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: '删除素材', exact: true })).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(page.locator('.asset-generation-history-dialog')).toHaveCount(0);
  const box = await card.boundingBox();
  await card.click({ position: { x: box!.width - 80, y: box!.height - 16 } });
  await expect(detail).toBeVisible();
  await expect(page.locator('.creation-editor-target .creation-asset-detail')).toBeVisible();
  await expect(page.locator('.creation-work-stage .creation-asset-detail')).toHaveCount(0);
  await expect(detail.getByRole('button', { name: '生成图片', exact: true })).toBeVisible();
  await detail.getByRole('button', { name: '收起生成设置', exact: true }).click();
  await expect(detail.getByRole('textbox', { name: '描述', exact: true })).toBeHidden();
  await expect(detail.locator('.image-subject-open')).toBeVisible();
  await detail.getByRole('button', { name: '展开生成设置', exact: true }).click();
  await expect(detail.getByRole('textbox', { name: '描述', exact: true })).toHaveValue(data.asset.description);
  expect(data.errors).toEqual([]);
});

test('asset extraction history remains available from the library heading', async ({ page }) => {
  const data = await fixture(page, true);
  await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '资产提取记录', exact: true }).click();
  const history = page.locator('.asset-extraction-dialog');
  await expect(history).toBeVisible();
  await history.getByRole('button', { name: '查看', exact: true }).first().click();
  await expect(history.locator('.extraction-candidate').first()).toBeVisible();
  expect(data.errors).toEqual([]);
});

test('desktop material settings preserve input through explicit collapse and restore focus', async ({ page }) => {
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.setViewportSize({ width: 1280, height: 720 }); await page.goto(root + '/assets');
  const detail = page.locator('.creation-editor-target .creation-asset-detail');
  const settings = detail.locator('.standard-object-settings');
  await expect(settings).toBeVisible();
  await expect(detail.getByRole('textbox', { name: '描述', exact: true })).toHaveValue(data.asset.description);
  await settings.getByRole('button', { name: '关闭生成设置', exact: true }).click();
  const open = detail.getByRole('button', { name: '展开生成设置', exact: true });
  await expect(open).toBeFocused(); await open.press('Enter');
  await expect(settings).toBeVisible();
  await expect(detail.getByRole('textbox', { name: '描述', exact: true })).toHaveValue(data.asset.description);
  expect(data.errors).toEqual([]);
});

test('material cards, right editor and storyboard remain usable at the review viewports', async ({ page }) => {
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.goto(`${root}/assets`);
  for (const viewport of refinementViewports) {
    await page.setViewportSize(viewport);
    const entry = page.getByRole('button', { name: '林晚', exact: true });
    await expect(entry).toBeVisible();
    await captureRefinement(page, 'material-cards', viewport.width);
    await entry.click();
    const detail = page.locator('.creation-editor-target .creation-asset-detail');
    await expect(detail).toBeVisible();
    await captureRefinement(page, 'material-editor', viewport.width);
    const close = detail.getByRole('button', { name: '收起生成设置', exact: true });
    if (await close.isVisible()) await close.click();
    const expand = detail.getByRole('button', { name: '展开生成设置', exact: true });
    if (await expand.isVisible()) await expand.click();
  }
  await page.goto(`${root}/storyboard`);
  for (const viewport of refinementViewports) {
    await page.setViewportSize(viewport);
    await expect(page.locator('.storyboard-item').first()).toBeVisible();
    await captureRefinement(page, 'storyboard-cards', viewport.width);
  }
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});
