import { expect, test } from '@playwright/test';
import { fixture } from './studio-fixture';

test('创建弹窗默认标准模式，切换保留输入，画布未知结果复用同一创建键', async ({ page }) => {
  const state = await fixture(page);
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: '9007199254740993', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true } } }));
  const requests: { body: unknown; key?: string }[] = [];
  const project = { id: '10', name: '新画布故事', aspect: '16:9', style: '', synopsis: '', workspace_mode: 'infinite_canvas', primary_canvas_id: '9007199254740997', canvas_count: 1 };
  await page.route('**/api/v1/projects', route => {
    if (route.request().method() !== 'POST') return route.fallback();
    requests.push({ body: route.request().postDataJSON(), key: route.request().headers()['idempotency-key'] });
    return requests.length === 1
      ? route.fulfill({ status: 503, json: { error: { code: 'service_unavailable', message: 'Retry the same request' } } })
      : route.fulfill({ status: 201, json: project });
  });
  await page.route('**/api/v1/projects/10', route => route.fulfill({ json: project }));
  await page.route('**/api/v1/projects/10/open', route => route.fulfill({ json: project }));
  await page.route('**/api/v1/projects/10/canvases/9007199254740997', route => route.fulfill({ json: { id: project.primary_canvas_id, project_id: '10', source_key: 'new-canvas-source' } }));
  // The independent editor itself is exercised by canvas/e2e.
  await page.route('**/canvas-app/canvas/new-canvas-source', route => route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<main>独立画布入口</main>' }));
  await page.goto('/projects');
  await page.getByRole('button', { name: '新建项目', exact: true }).click();
  const dialog = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: '新建项目', exact: true }) });
  await expect(dialog.getByRole('radio', { name: '标准模式', exact: true })).toBeChecked();
  await dialog.getByLabel('项目名称', { exact: true }).fill(project.name);
  await dialog.getByRole('radio', { name: '无限画布模式' }).check();
  await dialog.getByRole('radio', { name: '标准模式', exact: true }).check();
  await expect(dialog.getByLabel('项目名称', { exact: true })).toHaveValue(project.name);
  await dialog.getByRole('radio', { name: '无限画布模式' }).check();
  await dialog.getByRole('button', { name: /创建项目$/ }).click();
  await expect(dialog.getByRole('alert')).toContainText('保留原内容重试');
  await dialog.getByRole('button', { name: /创建项目$/ }).click();
  await expect(page).toHaveURL(/\/canvas-app\/canvas\/new-canvas-source$/);
  await expect(page.getByText('独立画布入口')).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[0].key).toBeTruthy();
  expect(requests[1]).toEqual(requests[0]);
  expect(requests[0].body).toMatchObject({ workspace_mode: 'infinite_canvas', name: project.name });
  expect(state.errors).toEqual([]);
});
