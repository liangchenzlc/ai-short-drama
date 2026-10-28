import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from '../support/studio-fixture';

async function fits(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
}

const routes = [
  ['projects', '/projects', '项目管理'],
  ...['all', 'image', 'video', 'audio'].map(kind => [`media-${kind}`, `/media-library/${kind}`, '资产库']),
  ['ai-config', '/ai_config', 'AI 配置'],
  ...['source', 'assets', 'storyboard', 'assembly'].map(stage => [`episode-${stage}`, `${root}/${stage}`, '雨夜来信']),
  ['not-found', '/missing', '没有找到内容'],
];

test.describe('desktop workspace', () => {
  for (const width of [1280, 1366]) test('all desktop routes fit and expose their actions at ' + width + 'px', async ({ page }, info) => {
    test.setTimeout(90000);
    const state = await fixture(page, true);
    await page.setViewportSize({ width, height: 900 });
    for (const [name, route] of routes) {
      await page.goto(route);
      if (route.startsWith(root)) {
        await expect(page.getByRole('button', { name: '雨夜来信', exact: true })).toBeVisible();
        await expect(page.getByRole('navigation', { name: '创作流程' }).getByRole('button')).toHaveCount(4);
      } else await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      if (name === 'projects') {
        await expect(page.locator('.project-tile')).toHaveCount(4);
        await expect(page.getByRole('textbox', { name: '搜索项目名称' })).toBeVisible();
      }
      if (name === 'episode-source') await expect(page.getByRole('textbox', { name: '剧本正文', exact: true })).toBeEditable();
      if (name === 'episode-assets' || name === 'episode-storyboard') {
        await expect(page.locator('.standard-object-settings:visible')).toHaveCount(1);
        await expect(page.getByRole('textbox', { name: '画面要求', exact: true })).toBeVisible();
      }
      if (name === 'episode-assembly') await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
      await fits(page);
      await page.screenshot({ path: info.outputPath(name + '.png'), fullPage: true, animations: 'disabled' });
    }
    expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
  });

  for (const width of [1280, 1366, 1440, 1920]) test(`project cards keep titles readable at ${width}px`, async ({ page }, info) => {
    const state = await fixture(page, true);
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/projects');
    await expect(page.locator('.project-tile')).toHaveCount(4);
    await expect(page.locator('.continue-episodes')).toHaveCount(0);
    await expect(page.getByRole('textbox', { name: '搜索项目名称' })).toBeVisible();
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

  test('desktop project search accepts pointer focus and keyboard submission and clearing restores the list', async ({ page }, info) => {
    const state = await fixture(page, true);
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto('/projects');
    const search = page.getByRole('textbox', { name: '搜索项目名称' });
    await search.click();
    await search.fill('雨夜');
    await expect(search).toBeInViewport({ ratio: 1 });
    await search.press('Enter');
    await expect(page.locator('.project-tile')).toHaveCount(1);
    await expect(page.locator('.project-tile-copy > h3')).toHaveText('雨夜来信');
    await fits(page);
    await page.screenshot({ path: info.outputPath('projects-search-results.png'), fullPage: true, animations: 'disabled' });
    await search.fill('不存在的测试项目');
    await search.press('Enter');
    await expect(page.getByRole('heading', { name: '没有找到项目' })).toBeVisible();
    await page.screenshot({ path: info.outputPath('projects-search-empty.png'), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '清除搜索', exact: true }).click();
    await expect(search).toHaveValue('');
    await expect(page.locator('.project-tile')).toHaveCount(4);
    expect(state.unexpected).toEqual([]);
    expect(state.errors).toEqual([]);
  });

  test('desktop asset drafts survive picker cancellation and media previews fit', async ({ page }, info) => {
    const state = await fixture(page, true);
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto(root + '/assets?asset=501');
    const detail = page.locator('.episode-asset-detail');
    const description = detail.getByRole('textbox', { name: '描述', exact: true });
    const draft = '在桌面选择素材图片时保留角色外观描述。';
    await description.fill(draft);
    await detail.getByRole('button', { name: '素材更多操作', exact: true }).click();
    await page.getByRole('button', { name: '从资产库选择', exact: true }).click();
    const picker = page.getByRole('dialog', { name: '从图片资产库选择', exact: true });
    await expect(picker).toBeVisible(); await picker.getByRole('button', { name: '关闭弹窗', exact: true }).click();
    await expect(description).toHaveValue(draft);
    await expect.poll(() => state.asset.description).toBe(draft);
    await page.goto('/media-library/image');
    await page.locator('.media-gallery-card').first().click();
    await expect(page.locator('.ant-image-preview')).toBeVisible();
    await fits(page);
    await page.screenshot({ path: info.outputPath('asset-preview-desktop.png'), animations: 'disabled' });
    await page.keyboard.press('Escape');
    await expect(page.locator('.ant-image-preview')).toHaveCount(0);
    expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
  });
});

test('batch selection leaves room for asset descriptions and stays with its storyboard card', async ({ page }) => {
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
  await page.getByRole('button', { name: '分镜生成记录', exact: true }).click();
  await page.getByRole('dialog', { name: '分镜生成记录', exact: true }).getByRole('button', { name: /^(分镜 01 更多操作|分镜列表更多操作)$/ }).click();
  await page.getByRole('menuitem', { name: '批量操作', exact: true }).click();
  const row = page.locator('.storyboard-item').first();
  const checkbox = row.getByRole('checkbox', { name: '批量选择分镜 1', exact: true });
  await checkbox.check();
  await expect(page.getByRole('checkbox', { name: '选择已加载项' })).toBeChecked({ indeterminate: true });
  const rowBounds = await row.boundingBox();
  const summaryBounds = await row.locator('.storyboard-summary').boundingBox();
  expect(rowBounds!.height).toBeLessThan(summaryBounds!.height + 4);
  await row.locator('.storyboard-summary').click();
  await expect(checkbox).toBeChecked();
  await expect(page.getByRole('textbox', { name: '分镜 1 脚本', exact: true })).toBeVisible();
  await expect(page.locator('.storyboard-expanded')).toHaveCount(0);
  const selectedBounds = await row.boundingBox();
  expect(selectedBounds!.height).toBeCloseTo(rowBounds!.height, 0);
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

for (const width of [1440, 1280]) test(`account entry and recovery pages fit at ${width}px`, async ({ page }, info) => {
  const state = await fixture(page, true);
  await page.setViewportSize({ width, height: 900 });
  for (const path of ['/login', '/register', '/verify-email', '/reset-password']) {
    await page.goto(path);
    await expect(page.locator('.identity-form h1')).toBeVisible();
    if (path.includes('/invite')) await expect(page.getByRole('link', { name: '登录受邀账号' })).toBeVisible();
    await fits(page);
    await page.screenshot({ path: info.outputPath(`${path.split('/')[1]}.png`), fullPage: true, animations: 'disabled' });
  }
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});
