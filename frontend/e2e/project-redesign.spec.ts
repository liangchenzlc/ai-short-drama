import { expect, test, type Page } from '@playwright/test';
import { fixture, image, root } from './studio-fixture';

const episode = { id: '20', project_id: '10', position: 1, episode_number: 1, title: '归来的旅人', synopsis: '林晚走进雨夜。', style: '电影质感', aspect: '16:9', row_version: '1', cover_url: image };

async function projectEpisodes(page: Page) {
  const state = await fixture(page, true);
  const items = [{ ...episode }, { ...episode, id: '21', position: 2, episode_number: 2, title: '未完成的信', aspect: '9:16', cover_url: null }];
  const patches: unknown[] = [];
  await page.route('**/api/v1/projects/10/episodes*', route => route.fulfill({ json: { items, total: items.length, offset: 0, limit: 20 } }));
  await page.route('**/api/v1/projects/10/episodes/20', route => {
    if (route.request().method() === 'PATCH') { const patch = route.request().postDataJSON(); patches.push(patch); Object.assign(items[0], patch); }
    return route.fulfill({ json: items[0] });
  });
  return { ...state, patches };
}

for (const width of [1440, 390]) test(`project and episode cards retain fixed media dimensions at ${width}px`, async ({ page }, info) => {
  const state = await projectEpisodes(page);
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/projects');
  await expect(page.locator('.project-tile')).toHaveCount(4);
  await expect(page.getByRole('button', { name: '刷新', exact: true })).toHaveCount(0);
  expect(state.requests.filter(request => request.path.endsWith('/episodes'))).toHaveLength(0);
  await page.screenshot({ path: info.outputPath('project-cards.png'), fullPage: true, animations: 'disabled' });
  await page.getByRole('link', { name: '打开项目 雨夜来信' }).focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/projects\/10$/);
  const cards = page.locator('.episode-media-card');
  await expect(cards).toHaveCount(2);
  const landscape = await cards.first().boundingBox();
  const portrait = await cards.last().boundingBox();
  expect(landscape?.width).toBe(240);
  expect(landscape?.height).toBe(180);
  expect(portrait?.width).toBe(180);
  expect(portrait?.height).toBe(364);
  await expect(cards.first().locator('img')).toHaveAttribute('src', image);
  await expect(cards.last().getByText('暂无封面')).toBeVisible();
  await expect(page.getByRole('button', { name: '刷新', exact: true })).toHaveCount(0);
  const projectInfo = await page.getByRole('region', { name: '剧集信息' }).boundingBox();
  const episodes = await page.getByRole('region', { name: /分集列表/ }).boundingBox();
  expect(episodes!.y).toBeGreaterThan(projectInfo!.y + projectInfo!.height);
  expect(episodes!.width).toBeCloseTo(projectInfo!.width, 0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath('episode-cards.png'), fullPage: true, animations: 'disabled' });
  await page.getByRole('button', { name: '第 1 集更多操作' }).click();
  await page.getByRole('menuitem', { name: '编辑分集' }).click();
  const dialog = page.getByRole('dialog', { name: '编辑分集' });
  await dialog.getByLabel('标题', { exact: true }).fill('修改后的标题');
  await dialog.getByRole('button', { name: '保存分集' }).click();
  await expect(dialog).toHaveCount(0);
  expect(state.patches).toEqual([expect.objectContaining({ title: '修改后的标题', row_version: '1' })]);
  await page.getByRole('link', { name: '进入第 1 集：修改后的标题' }).click();
  await expect(page).toHaveURL(new RegExp(`${root}/(?:source|assets|storyboard|assembly)$`));
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('project sections preserve drafts and support direct resource links', async ({ page }) => {
  await projectEpisodes(page);
  await page.goto('/projects/10?section=resources');
  await expect(page.getByRole('heading', { name: '项目资源库', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: '剧集信息' })).not.toBeVisible();
  await page.getByRole('button', { name: '剧集信息', exact: true }).click();
  await page.getByLabel('项目名称', { exact: true }).fill('保留的项目信息');
  await page.getByRole('button', { name: '项目资源库', exact: true }).click();
  await expect(page).toHaveURL(/section=resources/);
  await page.getByRole('button', { name: '剧集信息', exact: true }).click();
  await expect(page.getByLabel('项目名称', { exact: true })).toHaveValue('保留的项目信息');
  await expect(page.getByText('有未保存的修改')).toBeVisible();
});

test('unavailable cover reloads its signed URL and falls back without blocking navigation', async ({ page }) => {
  await projectEpisodes(page);
  await page.route('**/api/v1/projects/10/episodes*', route => route.fulfill({ json: { items: [{ ...episode, cover_url: '/expired-cover.png' }], total: 1, offset: 0, limit: 20 } }));
  await page.route('**/api/v1/projects/10/episodes/20', route => route.fulfill({ json: episode }));
  await page.route('**/expired-cover.png', route => route.fulfill({ status: 403 }));
  await page.goto('/projects/10');
  await expect(page.locator('.episode-cover img')).toHaveAttribute('src', image);
  await expect(page.locator('.episode-cover img')).toBeVisible();
  await page.getByRole('link', { name: '进入第 1 集：归来的旅人' }).click();
  await expect(page).toHaveURL(new RegExp(`${root}/(?:source|assets|storyboard|assembly)$`));
});

test('episode delete failure retains the selected episode and its confirmation', async ({ page }) => {
  await projectEpisodes(page);
  await page.route('**/api/v1/projects/10/episodes/20', route => route.request().method() === 'DELETE' ? route.fulfill({ status: 409, json: { error: { code: 'conflict', message: '仍有分镜内容' } } }) : route.fallback());
  await page.goto('/projects/10');
  await page.getByRole('button', { name: '第 1 集更多操作' }).click();
  await page.getByRole('menuitem', { name: '删除分集' }).click();
  const confirmation = page.getByRole('dialog', { name: '删除分集' });
  await confirmation.getByRole('button', { name: '确认删除' }).click();
  await expect(confirmation.getByRole('alert')).toBeVisible();
  await expect(page.locator('.episode-media-card')).toHaveCount(2);
  await confirmation.getByRole('button', { name: '取消' }).click();
  await expect(page.getByRole('button', { name: '第 1 集更多操作' })).toBeFocused();
});
