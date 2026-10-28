import { expect, test, type Page } from '@playwright/test';
import { checkPrimaryButtonContrast } from '../support/button-contrast';
import { fixture } from '../support/studio-fixture';

const owner = { id: '11', username: 'owner', display_name: '创作者', email: 'owner@example.test', email_verified: true };

async function accountFixture(page: Page, signedIn = false) {
  const base = await fixture(page, true);
  const state = {
    signedIn, expired: false,
    project: { id: '10', name: '测试项目', workspace_mode: 'standard', row_version: '1', canvas_count: 0, primary_canvas_id: null },
    patches: [] as Record<string, unknown>[],
  };
  await page.context().addCookies([{ name: 'sd_csrf', value: 'fixture-csrf', url: 'http://127.0.0.1:4175' }]);
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1', '');
    const reply = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (path === '/auth/capabilities') return reply({ enabled: true });
    if (path === '/auth/me') return state.signedIn ? reply({ user: owner }) : reply({ error: { code: 'authentication_required' } }, 401);
    if (path === '/auth/login') { state.signedIn = true; state.expired = false; return reply({ user: owner }); }
    if (path === '/auth/logout') { state.signedIn = false; return reply({ logged_out: true }); }
    if (path === '/users/me/model-preferences') return reply({ items: [] });
    if (state.expired) return reply({ error: { code: 'session_expired' } }, 401);
    if (path === '/projects/10') {
      if (request.method() === 'PATCH') {
        expect(request.headers()['x-csrf-token']).toBe('fixture-csrf');
        state.patches.push(request.postDataJSON());
        Object.assign(state.project, request.postDataJSON(), { row_version: String(BigInt(state.project.row_version) + 1n) });
      }
      return reply(state.project);
    }
    if (path === '/projects/10/open') return reply(state.project);
    return route.fallback();
  });
  return { ...base, state };
}

for (const width of [1440, 1280]) test(`logout confirmation preserves the account after a failed request at ${width}px`, async ({ page }, info) => {
  const { state } = await accountFixture(page, true);
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/account?next=%2Fprojects%2F10%2Fsource');
  const logout = page.getByRole('button', { name: '退出登录', exact: true });
  const confirmation = page.getByRole('dialog', { name: '退出登录', exact: true });
  await logout.click();
  await confirmation.getByRole('button', { name: '取消', exact: true }).click();
  expect(state.signedIn).toBe(true);
  await page.route('**/api/v1/auth/logout', route => route.fulfill({ status: 503, json: { error: { code: 'unavailable' } } }));
  await logout.click();
  await confirmation.getByRole('button', { name: '退出登录', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('退出登录未完成');
  expect(state.signedIn).toBe(true);
  await page.screenshot({ path: info.outputPath('logout-failed.png'), animations: 'disabled' });
  await page.unroute('**/api/v1/auth/logout');
  await expect(logout).toBeEnabled();
  await logout.click();
  await confirmation.getByRole('button', { name: '退出登录', exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  expect(state.signedIn).toBe(false);
});

for (const width of [1440, 1280]) test(`expired sessions retain a project name through keyboard reauthentication at ${width}px`, async ({ page }, info) => {
  const { state } = await accountFixture(page, true);
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/projects/10/source');
  await page.getByRole('button', { name: '测试项目', exact: true }).click();
  const name = page.getByRole('textbox', { name: '作品名称', exact: true });
  await name.fill('保留的名称草稿');
  state.expired = true;
  await name.press('Enter');
  const dialog = page.getByRole('dialog', { name: '重新登录，继续创作' });
  await expect(dialog).toBeVisible();
  await page.route('**/api/v1/auth/login', route => route.fulfill({ status: 401, json: { error: { code: 'invalid_credentials' } } }));
  await dialog.getByLabel('密码', { exact: true }).fill('incorrect-fixture-password');
  await dialog.getByLabel('密码', { exact: true }).press('Enter');
  await expect(dialog.getByRole('alert')).toBeVisible();
  await expect(dialog.getByLabel('密码', { exact: true })).toHaveValue('incorrect-fixture-password');
  await checkPrimaryButtonContrast(page, dialog.getByRole('button', { name: /登录并返回编辑/ }), info, 'session-expired-error');
  await page.unroute('**/api/v1/auth/login');
  await dialog.getByLabel('密码', { exact: true }).fill('strong-fixture-password');
  await dialog.getByLabel('密码', { exact: true }).press('Enter');
  await expect(dialog).toHaveCount(0);
  await page.getByRole('button', { name: '保留的名称草稿', exact: true }).click();
  await expect(name).toHaveValue('保留的名称草稿');
  await name.press('Enter');
  await expect.poll(() => state.project.name).toBe('保留的名称草稿');
});

test('disabled browser storage still allows login and an explicit project rename', async ({ page }) => {
  await page.addInitScript(() => {
    for (const name of ['sessionStorage', 'localStorage']) Object.defineProperty(window, name, { configurable: true, get() { throw new DOMException('Storage is disabled', 'SecurityError'); } });
  });
  const { state } = await accountFixture(page);
  await page.goto('/login?next=%2Fprojects%2F10%2Fsource');
  await page.getByLabel('账号名', { exact: true }).fill('owner');
  await page.getByLabel('密码', { exact: true }).fill('strong-fixture-password');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.getByRole('button', { name: '测试项目', exact: true }).click();
  const name = page.getByRole('textbox', { name: '作品名称', exact: true });
  await name.fill('无本地存储也能保存');
  await name.press('Enter');
  await expect.poll(() => state.project.name).toBe('无本地存储也能保存');
});
