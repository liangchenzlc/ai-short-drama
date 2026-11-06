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
