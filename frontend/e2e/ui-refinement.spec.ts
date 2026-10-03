import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';

async function fits(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
}

const routes = [
  ['projects', '/projects', '项目管理'], ['project-detail', '/projects/10', '雨夜来信'],
  ...['character', 'scene', 'prop'].map(kind => [`assets-${kind}`, `/assets/${kind}`, '全局素材库']),
  ...['text', 'image', 'video', 'audio'].map(kind => [`tasks-${kind}`, `/tasks/${kind}`, '任务管理']),
  ...['image', 'video'].map(kind => [`media-${kind}`, `/media-library/${kind}`, '资产库']),
  ['ai-config', '/ai_config', 'AI 配置'],
  ...['source', 'assets', 'storyboard', 'assembly'].map(stage => [`episode-${stage}`, `${root}/${stage}`, '归来的旅人']),
  ['not-found', '/missing', '没有找到内容'],
];

test.describe('touch workspace', () => {
  test.use({ hasTouch: true });
  for (const width of [390, 768]) test(`all routes fit and expose their actions at ${width}px`, async ({ page }, info) => {
    test.setTimeout(90000);
    const state = await fixture(page, true);
    await page.setViewportSize({ width, height: 900 });
    const overflow: unknown[] = [];
    for (const [name, path, heading] of routes) {
      await page.goto(path);
      await expect(page.getByRole('heading', { name: heading, exact: true, level: 1 })).toBeVisible();
      if (name === 'projects') {
        await expect(page.locator('.project-tile')).toHaveCount(4);
        await expect(page.getByRole('searchbox', { name: '搜索项目名称' })).toBeVisible();
      }
      if (name.startsWith('assets-')) await expect(page.locator('.library-resource-card')).toHaveCount(1);
      if (name.startsWith('tasks-')) await expect(page.locator('.ant-table-row')).toHaveCount(4);
      if (name === 'episode-storyboard') await expect(page.locator('.storyboard-summary')).toHaveCount(20);
      if (name === 'episode-assembly') await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
      const layout = await page.evaluate(() => ({ path: location.pathname, width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
        offenders: [...document.querySelectorAll('body *')].filter(node => { const box = node.getBoundingClientRect(); return box.width > 0 && (box.right > innerWidth + 1 || box.left < -1) && getComputedStyle(node).position !== 'fixed'; }).slice(0, 12).map(node => ({ tag: node.tagName, class: node.className, width: node.getBoundingClientRect().width })) }));
      if (layout.scrollWidth > width + 1) overflow.push(layout);
      await page.screenshot({ path: info.outputPath(`${name}.png`), fullPage: true, animations: 'disabled' });
      if (name.startsWith('episode-') && name !== 'episode-assembly') {
        await page.getByRole('tab', { name: 'AI 创作', exact: true }).click();
        await expect(page.getByRole('complementary', { name: '提示词 AI 创作', exact: true })).toBeVisible();
        await fits(page);
        await page.screenshot({ path: info.outputPath(`${name}-ai.png`), fullPage: true, animations: 'disabled' });
      }
    }
    expect(overflow).toEqual([]);
    expect(state.unexpected).toEqual([]);
    expect(state.errors).toEqual([]);
  });

  for (const width of [390, 681, 768, 900, 1024, 1280, 1440, 1920]) test(`project cards keep titles readable at ${width}px`, async ({ page }, info) => {
    const state = await fixture(page, true);
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/projects');
    await expect(page.locator('.project-tile')).toHaveCount(4);
    await expect(page.locator('.continue-episodes')).toHaveCount(0);
    await expect(page.getByRole('searchbox', { name: '搜索项目名称' })).toBeVisible();
    for (const row of await page.locator('.project-tile').all()) {
      const copy = await row.locator('.project-tile-copy').boundingBox();
      expect(copy!.width).toBeGreaterThanOrEqual(150);
      const title = await row.locator('.project-tile-copy > h3').boundingBox();
      expect(title!.height).toBeLessThan(44);
    }
    await fits(page);
    await page.screenshot({ path: info.outputPath('projects.png'), fullPage: true, animations: 'disabled' });
    expect(state.unexpected).toEqual([]);
    expect(state.errors).toEqual([]);
  });

  test('mobile project search accepts touch and keyboard input and clearing restores the list', async ({ page }, info) => {
    const state = await fixture(page, true);
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto('/projects');
    const search = page.getByRole('searchbox', { name: '搜索项目名称' });
    await search.tap();
    await search.fill('雨夜');
    const submit = page.locator('.project-search .ant-input-search-button');
    await expect.poll(async () => Math.round((await submit.boundingBox())!.height)).toBeGreaterThanOrEqual(44);
    await submit.tap();
    await expect(page.locator('.project-tile')).toHaveCount(1);
    await expect(page.locator('.project-tile-copy > h3')).toHaveText('雨夜来信');
    await fits(page);
    await page.screenshot({ path: info.outputPath('projects-search-results.png'), fullPage: true, animations: 'disabled' });
    await search.fill('不存在的测试项目');
    await search.press('Enter');
    await expect(page.getByRole('heading', { name: '没有找到项目' })).toBeVisible();
    await page.screenshot({ path: info.outputPath('projects-search-empty.png'), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '清除搜索', exact: true }).tap();
    await expect(search).toHaveValue('');
    await expect(page.locator('.project-tile')).toHaveCount(4);
    expect(state.unexpected).toEqual([]);
    expect(state.errors).toEqual([]);
  });

  test('mobile asset edits survive a nested cancellation and task drawers fit', async ({ page }, info) => {
    const state = await fixture(page, true);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/assets/character');
    await page.getByRole('button', { name: '编辑素材', exact: true }).click();
    const drawer = page.locator('.asset-editor-drawer');
    await expect(drawer).toBeVisible();
    const description = drawer.getByRole('textbox', { name: '描述', exact: true });
    const draft = '这是尚未保存的角色外观描述，关闭确认后仍应保留。';
    await description.fill(draft);
    await fits(page);
    await page.screenshot({ path: info.outputPath('asset-edit.png'), animations: 'disabled' });
    await drawer.getByRole('button', { name: '关闭弹窗', exact: true }).click();
    await expect(page.getByRole('dialog', { name: '未保存的修改', exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(description).toHaveValue(draft);
    await drawer.getByRole('button', { name: '关闭弹窗', exact: true }).click();
    await page.getByRole('dialog', { name: '未保存的修改', exact: true }).getByRole('button', { name: '放弃修改', exact: true }).click();
    await expect(drawer).toHaveCount(0);
    await page.getByRole('link', { name: '任务管理', exact: true }).tap();
    await page.locator('.page-actions').getByRole('button', { name: '新建文本任务' }).click();
    await expect(page.locator('.ant-drawer-body')).toBeVisible();
    await fits(page);
    const bounds = await page.locator('.ant-drawer-content-wrapper').boundingBox();
    expect(bounds!.width).toBeLessThanOrEqual(391);
    await page.screenshot({ path: info.outputPath('task-create.png'), animations: 'disabled' });
    expect(state.unexpected).toEqual([]);
    expect(state.errors).toEqual([]);
  });
});

test('batch selection leaves room for asset descriptions and stays with its storyboard row', async ({ page }) => {
  const state = await fixture(page, true);
  await page.goto('/projects/10?section=resources');
  const library = page.locator('.project-section .remote-asset-library');
  await expect(library.getByRole('button', { name: '编辑素材', exact: true })).toBeVisible();
  const workspaceBounds = await page.locator('.project-section').boundingBox();
  const libraryBounds = await library.boundingBox();
  expect(libraryBounds!.width).toBeGreaterThan(workspaceBounds!.width * .9);
  const card = library.locator('.library-resource-card').first();
  const image = await card.locator('.resource-image').boundingBox();
  const copy = await card.locator('.asset-card-content').boundingBox();
  expect(copy!.width).toBeGreaterThan(150);
  expect(Math.abs(copy!.y - image!.y)).toBeLessThanOrEqual(14);
  await card.getByRole('checkbox', { name: '批量选择 林晚' }).check();
  await expect(library.getByText('已选 1 项', { exact: true })).toBeVisible();
  await page.goto(`${root}/storyboard`);
  await expect(page.locator('.storyboard-summary')).toHaveCount(20);
  const row = page.locator('.storyboard-item').first();
  const checkbox = row.getByRole('checkbox', { name: '批量选择分镜 1', exact: true });
  await checkbox.check();
  await expect(page.getByRole('checkbox', { name: '选择已加载项' })).toBeChecked({ indeterminate: true });
  const rowBounds = await row.boundingBox();
  const summaryBounds = await row.locator('.storyboard-summary').boundingBox();
  expect(rowBounds!.height).toBeLessThan(summaryBounds!.height + 4);
  await row.locator('.storyboard-summary').click();
  await expect(checkbox).toBeChecked();
  await expect(page.locator('.storyboard-expanded')).toBeVisible();
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});

test('reduced motion removes dialog movement while retaining keyboard focus', async ({ page }) => {
  await fixture(page, true);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/projects');
  const create = page.getByRole('button', { name: '新建项目', exact: true });
  await create.click();
  const dialog = page.getByRole('dialog', { name: '新建项目', exact: true });
  await expect(dialog.getByLabel('项目名称')).toBeFocused();
  expect(await dialog.evaluate(node => getComputedStyle(node).animationName)).toBe('none');
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  await expect(create).toBeFocused();
});

for (const width of [1440, 390]) test(`account entry and recovery pages fit at ${width}px`, async ({ page }, info) => {
  const state = await fixture(page, true);
  await page.route('**/api/v1/invitations/**', route => route.fulfill({ json: { status: 'pending', requires_login: true } }));
  await page.setViewportSize({ width, height: 900 });
  for (const path of ['/login', '/register', '/verify-email', '/reset-password', '/invite/fixture-token']) {
    await page.goto(path);
    await expect(page.locator('.identity-form h1')).toBeVisible();
    if (path.includes('/invite')) await expect(page.getByRole('link', { name: '登录受邀账号' })).toBeVisible();
    await fits(page);
    await page.screenshot({ path: info.outputPath(`${path.split('/')[1]}.png`), fullPage: true, animations: 'disabled' });
  }
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});
