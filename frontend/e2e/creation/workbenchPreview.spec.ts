import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';

const preview = '/e2e/creation/fixtures/workbenchPreview.html';

test.beforeAll(() => {
  const frontend = process.cwd();
  const target = path.join(frontend, '.runtime', 'workbenchPreview');
  const source = path.resolve(frontend, '..', 'docs', 'design', 'standard-creation-workflow', 'assets');
  mkdirSync(target, { recursive: true });
  for (const name of ['woman.jpg', 'street.jpg', 'camera.jpg']) {
    copyFileSync(path.join(source, name), path.join(target, name));
  }
  if (!existsSync(path.join(frontend, '.runtime', 'timeline-fixture.mp4'))) {
    execFileSync('powershell.exe', [
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
      path.join(frontend, 'scripts', 'prepare-timeline-acceptance.ps1'),
    ], { cwd: frontend, stdio: 'inherit' });
  }
});

test('演示入口与真实接口隔离', async ({ page }) => {
  const calls: string[] = [];
  const origin = new URL(test.info().project.use.baseURL as string).origin;
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === origin && !url.pathname.startsWith('/api/')) {
      await route.continue();
      return;
    }
    calls.push(url.href);
    await route.abort();
  });
  await page.goto(preview + '?scenario=single');
  await expect(page.getByRole('status', { name: '演示数据' })).toBeVisible();
  await page.getByRole('navigation', { name: '创作流程' })
    .getByRole('button', { name: '资产生成', exact: true }).click();
  await expect(page.getByRole('button', { name: '林夏', exact: true })).toBeVisible();
  expect(calls).toEqual([]);
});

test('单剧增加分集后分别编辑剧本', async ({ page }) => {
  await page.goto(preview + '?scenario=single');
  await page.getByRole('tab', { name: '剧本', exact: true }).click();
  const editor = page.getByRole('textbox', { name: '剧本正文', exact: true });
  await editor.fill('第一集的剧本');
  await expect(page.getByRole('button', { name: /当前分集/ })).toHaveCount(0);
  await page.getByRole('button', { name: '添加分集', exact: true }).click();
  await page.getByRole('button', { name: '当前分集：第 01 集' }).click();
  await page.getByRole('menuitem', { name: '第 02 集', exact: true }).click();
  await editor.fill('第二集的剧本');
  await editor.press('Control+z');
  await expect(editor).not.toHaveValue('第一集的剧本');
  await editor.fill('第二集的剧本');
  await page.getByRole('button', { name: '当前分集：第 02 集' }).click();
  await page.getByRole('menuitem', { name: '第 01 集', exact: true }).click();
  await expect(editor).toHaveValue('第一集的剧本');
});

test('AI 概要采用空白首集并追加其余分集', async ({ page }) => {
  await page.goto(preview + '?scenario=single');
  await page.getByRole('button', { name: '生成分集概要', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '生成分集概要' });
  await dialog.getByRole('spinbutton', { name: '生成集数' }).fill('3');
  await dialog.getByRole('button', { name: '生成概要', exact: true }).click();
  await dialog.getByRole('button', { name: '采用概要', exact: true }).click();
  await expect(page.getByRole('list', { name: '分集概要' }).getByRole('listitem')).toHaveCount(3);
  const first = page.getByRole('textbox', { name: '第 01 集概要' });
  await first.fill('用户已经写好的概要');
  await page.getByRole('button', { name: '生成分集概要', exact: true }).click();
  await dialog.getByRole('spinbutton', { name: '生成集数' }).fill('2');
  await dialog.getByRole('button', { name: '生成概要', exact: true }).click();
  await dialog.getByRole('button', { name: '采用概要', exact: true }).click();
  await expect(first).toHaveValue('用户已经写好的概要');
  await expect(page.getByRole('list', { name: '分集概要' }).getByRole('listitem')).toHaveCount(5);
});