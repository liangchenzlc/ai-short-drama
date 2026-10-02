import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';
import { checkPrimaryButtonContrast } from './button-contrast';

const account = { id: '11', username: 'admin', display_name: 'admin', email: 'admin@example.test', email_verified: true };

async function signedInFixture(page: Page) {
  const base = await fixture(page, true);
  const state = { user: { ...account }, expired: false, passwordRequests: [] as Record<string, unknown>[], proofs: [] as Record<string, unknown>[], logins: [] as Record<string, unknown>[] };
  await page.context().addCookies([{ name: 'sd_csrf', value: 'fixture-csrf', url: 'http://127.0.0.1:4175' }]);
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.slice('/api/v1'.length);
    const body = request.postDataJSON() ?? {};
    const reply = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (path === '/auth/capabilities') return reply({ enabled: true });
    if (path === '/auth/me') return reply({ user: state.user });
    if (path === '/auth/login') { state.logins.push(body); state.expired = false; return reply({ user: state.user }); }
    if (path === '/auth/password/request' || path === '/auth/verification/request') { state.passwordRequests.push(body); return reply({ challenge_id: '50' }); }
    if (path === '/auth/password/reset' || path === '/auth/verification/confirm') {
      state.proofs.push(body);
      if (body.code !== '123456') return reply({ error: { code: 'email_proof_invalid' } }, 422);
      state.expired = path === '/auth/password/reset';
      return reply({ verified: true });
    }
    if (path === '/users/me/model-preferences') return reply({ items: [] });
    if (path === '/projects/10/members') return reply({ can_manage: false, items: [{ user_id: state.user.id, username: state.user.username, display_name: state.user.display_name, role: 'owner' }] });
    if (path === '/projects/10/invitations') return reply({ items: [] });
    if (state.expired) return reply({ error: { code: 'authentication_required' } }, 401);
    return route.fallback();
  });
  return { ...base, state };
}

async function fits(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual((await page.viewportSize())!.width + 1);
}

const studioRoutes = [
  ['projects', '/projects', '项目管理'], ['project-detail', '/projects/10', '雨夜来信'],
  ...['source', 'assets', 'storyboard', 'assembly'].map(stage => [`episode-${stage}`, `${root}/${stage}`, '归来的旅人']),
];

for (const width of [2048, 1440, 768, 390, 320]) test(`one account entry occupies the header and preserves the full detail canvas at ${width}px`, async ({ page }, info) => {
  test.setTimeout(90000);
  const data = await signedInFixture(page);
  await page.setViewportSize({ width, height: width === 2048 ? 1070 : 900 });
  for (const [name, path, heading] of studioRoutes) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: heading, exact: true, level: 1 })).toBeVisible();
    if (name === 'episode-source') await expect(page.getByRole('textbox', { name: '本集小说正文', exact: true })).toBeEditable();
    if (name === 'episode-storyboard') await expect(page.locator('.storyboard-summary')).toHaveCount(20);
    if (name === 'episode-assembly') await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
    const entry = page.getByRole('button', { name: '账号中心：admin', exact: true });
    await expect(entry).toHaveCount(1);
    await expect(entry).toBeVisible();
    await expect(page.getByRole('button', { name: '退出登录', exact: true })).toHaveCount(0);
    expect(await entry.evaluate(node => !!node.closest('header'))).toBe(true);
    const box = (await entry.boundingBox())!;
    expect(box.x).toBeGreaterThan(width / 2);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
    expect(box.y).toBeLessThan(230);
    if (width <= 600) expect(box.height).toBeGreaterThanOrEqual(44);
    if (name !== 'projects') {
      expect(await page.locator('.studio-content').evaluate(node => node.getBoundingClientRect().left)).toBe(0);
      await expect(page.locator('.studio > .account-controls')).toHaveCount(0);
    }
    await fits(page);
    await page.screenshot({ path: info.outputPath(`${name}.png`), fullPage: true, animations: 'disabled' });
  }
  await page.getByRole('button', { name: '专注剪辑', exact: true }).click();
  const entry = page.getByRole('button', { name: '账号中心：admin', exact: true });
  await expect(entry).toHaveCount(1);
  await entry.click();
  const center = page.getByRole('dialog', { name: '账号中心', exact: true });
  await expect(center).toBeVisible();
  for (const field of await center.locator('.account-details dd').all()) expect((await field.boundingBox())!.width).toBeGreaterThanOrEqual(170);
  await page.screenshot({ path: info.outputPath('focused-account-center.png'), animations: 'disabled' });
  await page.keyboard.press('Escape');
  await expect(page.locator('.assembly-workspace')).toHaveClass(/is-focused/);
  await expect(entry).toBeFocused();
  await fits(page);
  expect(data.unexpected).toEqual([]);
  expect(data.errors).toEqual([]);
});

for (const width of [1440, 390]) test(`account details support long identity values and restore keyboard focus at ${width}px`, async ({ page }, info) => {
  const data = await signedInFixture(page);
  data.state.user = { ...account, display_name: '创作者的完整显示名称'.repeat(8), username: 'creator_with_a_long_account_name', email: 'creator'.repeat(31) + '@independent-creator.example.test', email_verified: false };
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/projects/10');
  await page.getByLabel('项目名称', { exact: true }).fill('尚未保存的项目修改');
  const entry = page.getByRole('button', { name: /^账号中心：/ });
  await entry.focus();
  await entry.press('Enter');
  const center = page.getByRole('dialog', { name: '账号中心', exact: true });
  await expect(center.getByText(data.state.user.display_name, { exact: true })).toBeVisible();
  await expect(center.getByText(data.state.user.email, { exact: true })).toBeVisible();
  await expect(center.getByText('未验证', { exact: true })).toBeVisible();
  for (const field of await center.locator('.account-details dd').all()) expect((await field.boundingBox())!.width).toBeGreaterThanOrEqual(170);
  await fits(page);
  const close = center.getByRole('button', { name: '关闭弹窗', exact: true });
  const last = center.getByRole('button', { name: '返回创作', exact: true });
  await close.focus(); await page.keyboard.press('Shift+Tab'); await expect(last).toBeFocused();
  await page.keyboard.press('Tab'); await expect(close).toBeFocused();
  await page.screenshot({ path: info.outputPath('account-long-identity.png'), animations: 'disabled' });
  await page.keyboard.press('Escape');
  await expect(entry).toBeFocused();
  await expect(page.getByLabel('项目名称', { exact: true })).toHaveValue('尚未保存的项目修改');
  expect(data.errors).toEqual([]);
});

for (const width of [1440, 390]) test(`password verification handles request errors and wrong codes before reauthenticating without losing a draft at ${width}px`, async ({ page }, info) => {
  const data = await signedInFixture(page);
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/projects/10');
  await page.getByLabel('项目名称', { exact: true }).fill('修改密码时保留的项目草稿');
  await page.getByRole('button', { name: /^账号中心：/ }).click();
  const center = page.getByRole('dialog', { name: '账号中心', exact: true });
  await center.getByRole('button', { name: '修改密码', exact: true }).click();
  const email = center.getByLabel('注册邮箱', { exact: true });
  await expect(email).toHaveValue(account.email);
  await expect(email).toHaveAttribute('readonly', '');
  await page.route('**/api/v1/auth/password/request', route => route.fulfill({ status: 503, json: { error: { code: 'TEST_OFFLINE', message: '验证码服务暂时不可用' } } }));
  await center.getByRole('button', { name: '发送验证码', exact: true }).click();
  await expect(center.getByRole('alert')).toBeVisible();
  await expect(email).toHaveValue(account.email);
  await page.unroute('**/api/v1/auth/password/request');
  await center.getByRole('button', { name: '发送验证码', exact: true }).click();
  await expect(center.getByRole('button', { name: /秒后可重发/ })).toBeDisabled();
  expect(data.state.passwordRequests).toEqual([{ email: account.email }]);
  await center.getByLabel('邮箱验证码', { exact: true }).fill('000000');
  await center.getByLabel('新密码', { exact: true }).fill('new-fixture-password');
  await center.getByRole('button', { name: /验证并更新密码/ }).click();
  await expect(center.getByText('验证码无效或已过期，请重新获取验证码。')).toBeVisible();
  await expect(center.getByLabel('邮箱验证码', { exact: true })).toHaveValue('000000');
  await expect(center.getByLabel('新密码', { exact: true })).toHaveValue('new-fixture-password');
  await center.getByRole('button', { name: /验证并更新密码/ }).scrollIntoViewIfNeeded();
  await checkPrimaryButtonContrast(page, center.getByRole('button', { name: /验证并更新密码/ }), info, 'account-password-error');
  await page.screenshot({ path: info.outputPath('account-password-error.png'), animations: 'disabled' });
  await center.getByLabel('邮箱验证码', { exact: true }).fill('123456');
  await center.getByRole('button', { name: /验证并更新密码/ }).click();
  const resume = page.getByRole('dialog', { name: '重新登录，继续创作', exact: true });
  await expect(resume).toBeVisible();
  await expect(center).toHaveCount(0);
  await expect(page.getByLabel('项目名称', { exact: true })).toHaveValue('修改密码时保留的项目草稿');
  expect(data.state.proofs.at(-1)).toEqual({ challenge_id: '50', code: '123456', password: 'new-fixture-password' });
  await resume.getByLabel('密码', { exact: true }).fill('new-fixture-password');
  await resume.getByRole('button', { name: '登录并返回编辑', exact: true }).click();
  await expect(resume).toHaveCount(0);
  await expect(page.getByLabel('项目名称', { exact: true })).toHaveValue('修改密码时保留的项目草稿');
  expect(data.state.logins).toEqual([{ username: 'admin', password: 'new-fixture-password' }]);
  await fits(page);
  expect(data.unexpected).toEqual([]);
  expect(data.errors).toEqual([]);
});

test('all workspace areas share the account center and a missing email disables password recovery', async ({ page }) => {
  const data = await signedInFixture(page); data.state.user.email = '';
  for (const path of ['/assets/character', '/assets/scene', '/assets/prop', '/tasks/text', '/tasks/image', '/tasks/video', '/tasks/audio', '/media-library/image', '/media-library/video', '/ai']) {
    await page.goto(path);
    await page.getByRole('button', { name: /^账号中心：/ }).click();
    const center = page.getByRole('dialog', { name: '账号中心', exact: true });
    await expect(center.getByText('未设置邮箱', { exact: true })).toBeVisible();
    await expect(center.getByRole('button', { name: '修改密码', exact: true })).toBeDisabled();
    await center.getByRole('button', { name: '返回创作', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(path + '$'));
  }
  expect(data.unexpected).toEqual([]);
  expect(data.errors).toEqual([]);
});

test('account management remains reachable when a project cannot be opened', async ({ page }, info) => {
  await signedInFixture(page);
  await page.route('**/api/v1/projects/10', route => route.fulfill({ status: 503, json: { error: { code: 'TEST_OFFLINE', message: '项目服务暂时不可用' } } }));
  await page.goto('/projects/10');
  await expect(page.getByRole('heading', { name: '项目无法打开', exact: true })).toBeVisible();
  await page.getByRole('button', { name: /^账号中心：/ }).click();
  await expect(page.getByRole('dialog', { name: '账号中心', exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('account-project-error.png'), animations: 'disabled' });
});

for (const reset of [false, true]) test(`shared email proof form completes the public ${reset ? 'password recovery' : 'email verification'} path`, async ({ page }) => {
  const data = await signedInFixture(page);
  await page.goto(reset ? '/reset-password' : '/verify-email');
  await page.getByLabel('注册邮箱', { exact: true }).fill(account.email);
  await page.getByRole('button', { name: '发送验证码', exact: true }).click();
  await page.getByLabel('邮箱验证码', { exact: true }).fill('123456');
  if (reset) await page.getByLabel('新密码', { exact: true }).fill('new-fixture-password');
  await page.getByRole('button', { name: reset ? /验证并更新密码/ : /验证邮箱/ }).click();
  await expect(page.getByRole('status')).toContainText(reset ? '密码已更新' : '邮箱已验证');
  expect(data.state.proofs).toEqual([{ challenge_id: '50', code: '123456', ...(reset ? { password: 'new-fixture-password' } : {}) }]);
  expect(data.errors).toEqual([]);
});
