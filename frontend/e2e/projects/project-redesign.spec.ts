import { expect, test } from '@playwright/test';
import { fixture } from '../support/studio-fixture';

for (const width of [1440, 1280]) test(`作品直接进入创作并保持60px顶栏 ${width}`, async ({ page }, info) => {
  const state = await fixture(page, true, undefined, true);
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/projects');
  await expect(page.locator('.project-tile')).toHaveCount(4);
  await expect(page.getByRole('link', { name: '素材库', exact: true })).toHaveCount(0);
  const opened = page.waitForEvent('popup');
  await page.getByRole('link', { name: '打开项目 雨夜来信' }).click();
  const creation = await opened;
  await creation.setViewportSize({ width, height: 900 });
  await expect(page).toHaveURL(/\/projects$/);
  await expect(creation).toHaveURL(/\/projects\/10\/source$/);
  await expect(creation.getByRole('button', { name: '雨夜来信', exact: true })).toBeVisible();
  await expect(creation.getByRole('navigation', { name: '创作流程' })).toBeVisible();
  const bounds = await creation.locator('.episode-top').boundingBox();
  expect(bounds?.height).toBe(60);
  expect(await creation.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(state.requests.some(item => item.path.includes('/episodes'))).toBe(false);
  await creation.screenshot({ path: info.outputPath('creation.png'), fullPage: true, animations: 'disabled' });
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

for (const width of [1440, 1280]) test(`项目三点菜单取消与删除不打开新标签，信息保持同行 ${width}`, async ({ page }) => {
  const state = await fixture(page, true);
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/projects');
  const card = page.locator('.project-tile').first();
  const link = card.getByRole('link', { name: '打开项目 雨夜来信' });
  await expect(link).toHaveAttribute('target', '_blank');
  await expect(link).toHaveAttribute('rel', /noopener/);
  await expect(card.locator('.project-tile-open')).toHaveCount(0);
  const boxes = await Promise.all([card.locator('h3').boundingBox(), card.locator('time').boundingBox(), card.getByRole('button', { name: /更多操作/ }).boundingBox()]);
  const centers = boxes.map(box => box!.y + box!.height / 2);
  expect(Math.max(...centers) - Math.min(...centers)).toBeLessThan(2);
  await card.getByRole('button', { name: /更多操作/ }).click();
  await page.getByRole('menuitem', { name: '删除项目' }).click();
  await page.getByRole('dialog', { name: '删除项目' }).getByRole('button', { name: '取消', exact: true }).click();
  await expect(card.getByRole('button', { name: /更多操作/ })).toBeFocused();
  expect(state.requests.filter(item => item.method === 'DELETE')).toEqual([]);
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: /更多操作/ }).click();
  await page.getByRole('menuitem', { name: '删除项目' }).click();
  await page.getByRole('dialog', { name: '删除项目' }).getByRole('button', { name: '删除项目', exact: true }).click();
  await expect(page.locator('.project-tile')).toHaveCount(3);
  await expect(page.getByRole('link', { name: '打开项目 雨夜来信' })).toHaveCount(0);
  const request = state.requests.find(item => item.method === 'DELETE');
  expect(request?.path).toBe('/projects/10');
  expect(request?.query.get('row_version')).toBe('1');
  expect(page.context().pages()).toHaveLength(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('项目删除409保留确认，重新核对后需要再次明确删除', async ({ page }) => {
  const state = await fixture(page, true);
  let attempt = 0;
  await page.route(/\/api\/v1\/projects\/10(?:\?.*)?$/, async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: { id: '10', name: '另一标签页修改后的项目', workspace_mode: 'standard', row_version: '2' } });
    if (route.request().method() !== 'DELETE') return route.fallback();
    attempt += 1;
    if (attempt === 1) return route.fulfill({ status: 409, json: { error: { code: 'project_version_conflict', message: '项目已变化' } } });
    expect(new URL(route.request().url()).searchParams.get('row_version')).toBe('2');
    state.controls.emptyProjects = true;
    return route.fulfill({ status: 204 });
  });
  await page.goto('/projects');
  await page.getByRole('button', { name: '项目 雨夜来信 的更多操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除项目' }).click();
  const dialog = page.getByRole('dialog', { name: '删除项目' });
  await dialog.getByRole('button', { name: '删除项目', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '重新核对项目', exact: true })).toBeVisible();
  await expect(page.locator('.project-tile')).toHaveCount(4);
  await dialog.getByRole('button', { name: '重新核对项目', exact: true }).click();
  await expect(dialog).toContainText('另一标签页修改后的项目');
  expect(attempt).toBe(1);
  await dialog.getByRole('button', { name: '删除项目', exact: true }).click();
  await expect(page.getByRole('heading', { name: '开始你的第一部短剧' })).toBeVisible();
  expect(attempt).toBe(2);
  expect(state.errors).toEqual([]);
});

test('项目删除失败保留卡片并可重试，删除末页最后一项返回有效分页', async ({ page }) => {
  const state = await fixture(page, true);
  let deleted = false;
  let attempt = 0;
  await page.route('**/api/v1/projects?*', route => {
    const offset = new URL(route.request().url()).searchParams.get('offset');
    const total = deleted ? 20 : 21;
    const items = offset === '20' ? deleted ? [] : [{ id: '99', name: '末页项目', row_version: '1', workspace_mode: 'standard' }]
      : Array.from({ length: 20 }, (_, i) => ({ id: String(i + 1), name: `项目${i + 1}`, row_version: '1', workspace_mode: 'standard' }));
    return route.fulfill({ json: { items, total, offset: Number(offset), limit: 20 } });
  });
  await page.route('**/api/v1/projects/99?*', route => {
    attempt += 1;
    if (attempt === 1) return route.fulfill({ status: 503, json: { error: { code: 'unavailable', message: '删除暂时失败' } } });
    deleted = true;
    return route.fulfill({ status: 204 });
  });
  await page.goto('/projects');
  await page.locator('.ant-pagination-item-2').click();
  await page.getByRole('button', { name: '项目 末页项目 的更多操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除项目' }).click();
  const dialog = page.getByRole('dialog', { name: '删除项目' });
  await dialog.getByRole('button', { name: '删除项目', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('服务暂时不可用，请稍后重试。');
  await expect(page.getByRole('link', { name: '打开项目 末页项目' })).toBeVisible();
  await dialog.getByRole('button', { name: '删除项目', exact: true }).click();
  await expect(page.locator('.project-tile')).toHaveCount(20);
  await expect(page.locator('.ant-pagination')).toHaveCount(0);
  expect(attempt).toBe(2);
  expect(state.errors).toEqual([]);
});

test('原位修改名称只提交名称与版本，保存后四阶段仍保持当前作品', async ({ page }) => {
  const state = await fixture(page, true);
  await page.goto('/projects/10/source');
  await page.getByRole('button', { name: '雨夜来信', exact: true }).click();
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill('改名后的作品');
  await page.getByRole('textbox', { name: '作品名称', exact: true }).press('Enter');
  await expect.poll(() => state.requests.filter(item => item.path === '/projects/10' && item.method === 'PATCH').map(item => item.body)).toEqual([{ name: '改名后的作品', row_version: '1' }]);
  await expect(page.getByRole('button', { name: '改名后的作品', exact: true })).toBeVisible();
  await page.getByRole('button', { name: /资产生成/ }).click();
  await expect(page).toHaveURL(/\/projects\/10\/assets$/);
  await expect(page.getByRole('heading', { name: '资产库' })).toBeVisible();
  expect(state.requests.some(item => item.path.includes('/episodes') || item.path.includes('/libraries/'))).toBe(false);
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('名称409保留本机输入并提供明确的恢复选择', async ({ page }) => {
  const state = await fixture(page, true);
  await page.route('**/api/v1/projects/10', route => route.request().method() === 'PATCH'
    ? route.fulfill({ status: 409, json: { error: { code: 'project_version_conflict', message: '版本已变化' } } }) : route.fallback());
  await page.goto('/projects/10/source');
  await page.getByRole('button', { name: '雨夜来信', exact: true }).click();
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill('保留的名称');
  await page.getByRole('textbox', { name: '作品名称', exact: true }).press('Enter');
  await expect(page.getByRole('button', { name: '保留的名称', exact: true })).toBeVisible();
  await page.getByRole('button', { name: /保存状态：需处理/ }).click();
  await expect(page.getByRole('button', { name: '核对后保留本机名称' })).toBeVisible();
  await expect(page.getByRole('button', { name: '使用服务端名称' })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('名称停顿不提交，Escape取消，Enter和失焦各只提交一次', async ({ page }) => {
  const state = await fixture(page, true);
  const updates = () => state.requests.filter(item => item.path === '/projects/10' && item.method === 'PATCH').map(item => item.body);
  await page.goto('/projects/10/source');
  await page.getByRole('button', { name: '雨夜来信', exact: true }).click();
  const name = page.getByRole('textbox', { name: '作品名称', exact: true });
  await name.fill('尚未确认的名称');
  await page.waitForTimeout(1200);
  expect(updates()).toEqual([]);
  await name.press('Escape');
  await expect(page.getByRole('button', { name: '雨夜来信', exact: true })).toBeVisible();
  expect(updates()).toEqual([]);
  await page.getByRole('button', { name: '雨夜来信', exact: true }).click();
  await name.fill('回车确认');
  await name.press('Enter');
  await expect.poll(updates).toEqual([{ name: '回车确认', row_version: '1' }]);
  await page.getByRole('button', { name: '回车确认', exact: true }).click();
  await name.fill('失焦确认');
  await name.press('Tab');
  await expect.poll(updates).toEqual([{ name: '回车确认', row_version: '1' }, { name: '失焦确认', row_version: '2' }]);
  await page.waitForTimeout(1200);
  expect(updates()).toHaveLength(2);
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('旧分集、项目素材库、全局素材库与协作链接均为退役入口', async ({ page }) => {
  const state = await fixture(page);
  for (const path of ['/projects/10/episodes/20/source', '/projects/10?section=resources', '/assets/character', '/invite/old']) {
    await page.goto(path);
    await expect(page.getByText(/已停用/).first()).toBeVisible();
  }
  expect(state.requests.some(item => /episodes|libraries|invitations/.test(item.path))).toBe(false);
  expect(state.errors).toEqual([]);
});
