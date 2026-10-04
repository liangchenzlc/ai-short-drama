import { test, expect, type Page } from '@playwright/test';

import { fixture, root, image } from './studio-fixture';

test('episode card exposes editing and deletion only in its more menu', async ({ page }) => {
  const state = await fixture(page); await page.goto('/projects/10');
  const item = page.locator('.episode-media-card');
  await expect(item.getByRole('link')).toBeVisible();
  await expect(page.getByRole('menuitem', { name: '编辑分集' })).toHaveCount(0);
  await item.getByRole('button', { name: '第 1 集更多操作' }).click();
  await expect(page.getByRole('menuitem', { name: '编辑分集' })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: '删除分集' })).toBeVisible();
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('merged writing imports TXT, collapses navigation and adopts a historical script', async ({ page }, info) => {
  const state = await fixture(page); await page.goto(`${root}/source`);
  await expect(page.getByRole('tab', { name: '本集小说' })).toBeVisible();
  await expect(page.locator('.episode-stage-link')).toHaveCount(4);
  await page.getByRole('button', { name: '收起创作流程' }).click();
  await expect(page.locator('.episode-layout')).toHaveClass(/is-collapsed/);
  await page.locator('input[type=file]').first().setInputFiles({ name: '小说.txt', mimeType: 'text/plain', buffer: Buffer.from('导入的雨夜故事。') });
  await page.getByRole('dialog', { name: '确认操作', exact: true }).getByRole('button', { name: '确认继续', exact: true }).click();
  await expect(page.locator('#episode-novel')).toHaveValue('导入的雨夜故事。');
  await expect.poll(() => state.writing.novel.content).toBe('导入的雨夜故事。');
  await page.screenshot({ path: info.outputPath('writing-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: '生成记录', exact: true }).click();
  await page.screenshot({ path: info.outputPath('script-history.png'), animations: 'disabled' });
  await page.getByRole('button', { name: '预览全文' }).click();
  await page.screenshot({ path: info.outputPath('script-preview.png'), animations: 'disabled' });
  await page.getByRole('button', { name: '采用并编辑' }).click();
  await page.getByRole('dialog', { name: '确认操作', exact: true }).getByRole('button', { name: '确认继续', exact: true }).click();
  await expect(page.getByRole('tab', { name: '剧本定稿' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.episode-script-textarea')).toHaveValue(/候选剧本/);
  await expect(page.locator('dialog[open]')).toHaveCount(0);
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('asset editors persist reference images and expose prompts only on click', async ({ page }, info) => {
  const state = await fixture(page); await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '新建角色', exact: true }).first().click();
  await expect(page.locator('.asset-create-drawer')).toBeVisible();
  const bounds = await page.locator('.asset-create-drawer').boundingBox(); expect(bounds!.width).toBeCloseTo(720, 0);
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await page.getByRole('button', { name: '林晚', exact: true }).click();
  await expect(page.getByRole('heading', { name: '图片与生成', exact: true })).toBeVisible();
  await expect(page.getByText('本次补充要求', { exact: true })).toHaveCount(0);
  await page.locator('.generation-reference-images input[type=file]').setInputFiles({ name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from(image.split(',')[1], 'base64') });
  await expect(page.locator('.reference-image-strip figure')).toHaveCount(1);
  await page.screenshot({ path: info.outputPath('asset-drawer-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: '返回素材列表', exact: true }).click();
  await page.getByRole('button', { name: '林晚', exact: true }).click();
  await expect(page.locator('.reference-image-strip figure')).toHaveCount(1);
  await page.locator('.asset-generation-history summary').click();
  await page.getByRole('link', { name: '查看任务详情' }).first().click();
  await expect(page.getByText('测试生成提示词', { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('asset-task-detail.png'), animations: 'disabled' });
  await expect(page.getByText('DO_NOT_RENDER')).toHaveCount(0);
  await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '从剧本提取素材', exact: true }).click();
  await page.screenshot({ path: info.outputPath('extraction-result.png'), animations: 'disabled' });
  await page.getByRole('dialog').getByRole('button', { name: '提取记录', exact: true }).click();
  await page.screenshot({ path: info.outputPath('extraction-history.png'), animations: 'disabled' });
  await page.getByRole('button', { name: '查看', exact: true }).click();
  await expect(page.getByText('原文依据', { exact: false })).toHaveCount(0);
  await expect(page.getByLabel('图片生成提示词', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '查看 林晚 的图片生成提示词' }).click();
  await expect(page.getByLabel('图片生成提示词', { exact: true })).toHaveValue('隐藏的人物图片提示词');
  await page.screenshot({ path: info.outputPath('extraction-prompt.png'), animations: 'disabled' });
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('storyboard and history load pages on scroll and image history stays in a dialog', async ({ page }, info) => {
  const state = await fixture(page); await page.goto(`${root}/storyboard`);
  await expect(page.locator('.storyboard-summary')).toHaveCount(20);
  // StrictMode can repeat the first request; it must never eagerly fetch later pages.
  expect(state.requests.filter(item => item.path === `${root}/shots`).every(item => Number(item.query.get('offset') || 0) === 0 && item.query.get('limit') === '20')).toBe(true);
  await expect(page.getByRole('button', { name: '归档', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '下载草稿', exact: true })).toHaveCount(0);
  await page.locator('.storyboard-list').evaluate(node => { node.scrollTop = node.scrollHeight; });
  await expect(page.locator('.storyboard-summary')).toHaveCount(40);
  await page.locator('.storyboard-list').evaluate(node => { node.scrollTop = 0; });
  await page.locator('.storyboard-summary').first().click();
  await expect(page.locator('.shot-image-candidates')).toHaveCount(1);
  await expect(page.getByRole('combobox', { name: '关联角色', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '分镜图', exact: true }).click();
  await page.locator('.generation-reference-images input[type=file]').setInputFiles({ name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from(image.split(',')[1], 'base64') });
  await expect(page.locator('.reference-image-strip figure')).toHaveCount(1);
  await page.screenshot({ path: info.outputPath('storyboard-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await expect.poll(() => state.requests.filter(item => item.path === '/ai/generations/image').length).toBe(1);
  await expect(page.getByRole('dialog', { name: '分镜图片生成记录' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('shot-image-history.png'), animations: 'disabled' });
  await page.getByRole('button', { name: '关闭弹窗', exact: true }).click();
  await page.getByRole('button', { name: '历史记录', exact: true }).click();
  await page.screenshot({ path: info.outputPath('storyboard-history.png'), animations: 'disabled' });
  await page.getByRole('button', { name: '查看分镜' }).first().click();
  await expect(page.locator('.compact-shot-list > li')).toHaveCount(20);
  await page.screenshot({ path: info.outputPath('storyboard-result.png'), animations: 'disabled' });
  await page.locator('.storyboard-result-scroll').evaluate(node => { node.scrollTop = node.scrollHeight; });
  await expect(page.locator('.compact-shot-list > li')).toHaveCount(40);
  await page.getByRole('button', { name: '追加到现有分镜' }).click();
  await expect(page.locator('dialog[open]')).toHaveCount(0);
  expect(state.requests.some(item => item.path.endsWith('/apply') && item.body.mode === 'append')).toBe(true);
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('compact desktop workspaces and drawers fit the viewport', async ({ page }, info) => {
  const state = await fixture(page); await page.setViewportSize({ width: 1024, height: 900 });
  for (const stage of ['source', 'storyboard', 'assets']) {
    await page.goto(`${root}/${stage}`);
    await expect(page.getByRole('button', { name: '收起创作流程' })).toBeVisible();
    if (stage === 'storyboard') {
      await expect(page.locator('.storyboard-summary')).toHaveCount(20);
      await page.locator('.lazy-load-more').scrollIntoViewIfNeeded();
      await expect(page.locator('.storyboard-summary')).toHaveCount(40);
      await page.evaluate(() => window.scrollTo(0, 0));
    }
    if (stage === 'assets') await page.getByRole('button', { name: '林晚', exact: true }).click();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath(`${stage}-compact-desktop.png`), fullPage: true });
  }
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});
