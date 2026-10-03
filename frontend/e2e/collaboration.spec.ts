import { expect, test, type Page } from '@playwright/test';
import { checkPrimaryButtonContrast } from './button-contrast';
import { fixture } from './studio-fixture';

const token = 'a'.repeat(43);
const owner = { id: '11', username: 'owner', display_name: '项目主人', email: 'owner@example.test', email_verified: true };
const editor = { id: '12', username: 'editor', display_name: '同名创作者', email: 'editor@example.test', email_verified: true };
const project = { id: '10', name: '协作项目', synopsis: '共享故事', style: '', aspect: '16:9', row_version: '1', owner_user_id: '11', capabilities: { delete: true, manage_members: true }, last_opened_at: null, created_at: null, updated_at: null };

for (const width of [1440, 390]) test(`collaboration choices are readable and show the selected recipient at ${width}px`, async ({ page }, info) => {
  await accountFixture(page, true);
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/projects/10?section=collaboration');
  await page.getByRole('button', { name: '邀请协作者' }).click();
  await page.getByLabel('搜索人名、账号名、账号 ID 或邮箱').fill('同名创作者');
  await page.getByRole('button', { name: '搜索账号', exact: true }).click();
  const person = page.locator('.collaboration-search-result').filter({ hasText: '@editor' });
  await person.click();
  await expect(person).toHaveAttribute('aria-pressed', 'true');
  const unselectedBackground = await page.locator('.collaboration-search-result[aria-pressed=false]').evaluate(node => getComputedStyle(node).backgroundColor);
  await expect.poll(() => person.evaluate(node => getComputedStyle(node).backgroundColor)).not.toBe(unselectedBackground);
  await page.getByRole('dialog', { name: '邀请协作者', exact: true }).scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await person.evaluate(button => button.focus({ preventScroll: true }));
  await expect(person).toBeFocused();
  await page.screenshot({ path: info.outputPath('collaboration.png'), fullPage: true, animations: 'disabled' });
});

async function accountFixture(page: Page, initial = false) {
  await fixture(page, true);
  const state = { signedIn: initial, expired: false, conflict: false, inviteMismatch: false, user: owner, project: { ...project }, patches: [] as Record<string, unknown>[], invited: false, accepted: false };
  await page.context().addCookies([{ name: 'sd_csrf', value: 'fixture-csrf', url: 'http://127.0.0.1:4175' }]);
  await page.route('**/api/v1/**', async route => {
    const request = route.request(); const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1', '');
    const body = request.postDataJSON() ?? {};
    const reply = (json: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(json) });
    if (path === '/auth/capabilities') return reply({ enabled: true });
    if (path === '/auth/me') return state.signedIn ? reply({ user: state.user }) : reply({ error: { code: 'authentication_required' } }, 401);
    if (path === '/auth/login') { state.signedIn = true; state.expired = false; state.inviteMismatch = false; return reply({ user: state.user }); }
    if (path === '/auth/logout') { state.signedIn = false; return reply({ logged_out: true }); }
    if (path === '/users/me/model-preferences') return reply({ items: [] });
    if (path.startsWith('/invitations/')) {
      if (state.signedIn && state.inviteMismatch) return reply({ error: { code: 'invitation_identity_mismatch' } }, 403);
      if (path.endsWith('/verification')) return reply({ challenge_id: '50', expires_in: 600 });
      if (path.endsWith('/accept')) {
        if (body.code !== '123456') return reply({ error: { code: 'email_proof_invalid' } }, 422);
        state.accepted = true; return reply({ project_id: '10', joined: true });
      }
      return reply(state.signedIn && !state.expired ? { status: 'pending', project_id: '10', project_name: '协作项目', requires_email_proof: true } : { status: 'pending', requires_login: true });
    }
    if (state.expired) return reply({ error: { code: 'session_expired' } }, 401);
    if (path === '/projects/10') {
      if (request.method() === 'PATCH') {
        expect(request.headers()['x-csrf-token']).toBe('fixture-csrf');
        state.patches.push(body);
        if (state.conflict) return reply({ error: { code: 'project_version_conflict' } }, 409);
        Object.assign(state.project, body, { row_version: String(Number(state.project.row_version) + 1) });
      }
      return reply(state.project);
    }
    if (path === '/projects/10/open') return reply(state.project);
    if (path === '/users/search') return reply({ items: [editor, { ...editor, id: '13', username: 'another_editor' }] });
    if (path === '/projects/10/members') return reply({ can_manage: true, items: [{ user_id: '11', username: 'owner', display_name: '项目主人', role: 'owner' }] });
    if (path === '/projects/10/invitations') {
      if (request.method() === 'POST') { expect(body).toEqual({ target_user_id: '12' }); state.invited = true; return reply({ id: '30', url: `http://127.0.0.1:4175/invite/${token}`, status: 'pending' }, 201); }
      return reply({ items: [] });
    }
    return route.fallback();
  });
  return state;
}

for (const width of [1440, 390]) test(`login and invitation retain wrong-code input at ${width}px`, async ({ page }) => {
  const state = await accountFixture(page); state.user = editor;
  await page.setViewportSize({ width, height: 844 });
  await page.goto(`/invite/${token}`);
  await expect(page.getByRole('link', { name: '登录受邀账号' })).toBeVisible();
  await page.getByRole('link', { name: '登录受邀账号' }).click();
  await page.getByLabel('账号名', { exact: true }).fill('editor');
  await page.getByLabel('密码', { exact: true }).fill('strong-fixture-password');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.getByRole('button', { name: '发送本次邀请验证码' }).click();
  await page.getByLabel('邮箱验证码').fill('000000');
  await page.getByRole('button', { name: '验证邮箱并加入项目' }).click();
  await expect(page.getByText('验证码无效或已过期，请重新获取验证码。')).toBeVisible();
  await expect(page.getByLabel('邮箱验证码')).toHaveValue('000000');
  expect(state.accepted).toBe(false);
  await page.getByLabel('邮箱验证码').fill('123456');
  await page.getByLabel('邮箱验证码').press('Enter');
  await expect(page).toHaveURL(/\/projects\/10$/);
  expect(state.accepted).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

for (const width of [1440, 390]) test(`logout requires confirmation and preserves the account when the request fails at ${width}px`, async ({ page }, info) => {
  const state = await accountFixture(page, true);
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/projects/10');
  await page.getByRole('button', { name: /^账号中心：/ }).click();
  const logout = page.getByRole('button', { name: '退出登录', exact: true });
  await logout.click();
  let confirmation = page.getByRole('dialog', { name: '退出登录', exact: true });
  await confirmation.getByRole('button', { name: '取消', exact: true }).click();
  expect(state.signedIn).toBe(true);
  await expect(page.getByLabel('项目名称', { exact: true })).toBeVisible();
  await page.route('**/api/v1/auth/logout', route => route.fulfill({ status: 503, json: { error: { code: 'TEST_SERVICE', message: '退出服务暂时不可用' } } }));
  await logout.click();
  confirmation = page.getByRole('dialog', { name: '退出登录', exact: true });
  await confirmation.getByRole('button', { name: '退出登录', exact: true }).click();
  const failure = page.getByRole('dialog', { name: '退出登录未完成', exact: true });
  await expect(failure.getByRole('alert')).toBeVisible();
  expect(state.signedIn).toBe(true);
  await page.screenshot({ path: info.outputPath('logout-failed.png'), animations: 'disabled' });
  await failure.getByRole('button', { name: '返回创作' }).click();
  await page.unroute('**/api/v1/auth/logout');
  await page.getByRole('button', { name: /^账号中心：/ }).click();
  await logout.click();
  await page.getByRole('dialog', { name: '退出登录', exact: true }).getByRole('button', { name: '退出登录', exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  expect(state.signedIn).toBe(false);
});

for (const width of [1440, 390]) test(`expired session keeps the edited project through keyboard reauthentication at ${width}px`, async ({ page }, info) => {
  const state = await accountFixture(page, true);
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/projects/10');
  await page.getByLabel('项目名称', { exact: true }).fill('保留的协作草稿');
  state.expired = true;
  await page.getByRole('button', { name: '保存项目信息' }).click();
  const dialog = page.getByRole('dialog', { name: '重新登录，继续创作' });
  await expect(dialog).toBeVisible();
  await expect(page.getByLabel('项目名称', { exact: true })).toHaveValue('保留的协作草稿');
  await page.screenshot({ path: info.outputPath('session-expired.png'), animations: 'disabled' });
  await page.route('**/api/v1/auth/login', route => route.fulfill({ status: 401, json: { error: { code: 'invalid_credentials', message: '账号或密码错误。' } } }));
  await dialog.getByLabel('密码', { exact: true }).fill('incorrect-fixture-password');
  await dialog.getByLabel('密码', { exact: true }).press('Enter');
  await expect(dialog.getByRole('alert')).toBeVisible();
  await expect(dialog.getByLabel('密码', { exact: true })).toHaveValue('incorrect-fixture-password');
  await expect(page.getByLabel('项目名称', { exact: true })).toHaveValue('保留的协作草稿');
  await checkPrimaryButtonContrast(page, dialog.getByRole('button', { name: /登录并返回编辑/ }), info, 'session-expired-error');
  await page.screenshot({ path: info.outputPath('session-expired-error.png'), animations: 'disabled' });
  await page.unroute('**/api/v1/auth/login');
  await dialog.getByLabel('密码', { exact: true }).fill('strong-fixture-password');
  await dialog.getByLabel('密码', { exact: true }).press('Enter');
  await expect(dialog).not.toBeVisible();
  await expect(page.getByLabel('项目名称', { exact: true })).toHaveValue('保留的协作草稿');
  await page.getByRole('button', { name: '保存项目信息' }).click();
  await expect(page.getByText('已保存至服务端', { exact: true })).toBeVisible();
});

test('version conflict preserves draft and requires reviewing the current version', async ({ page }) => {
  const state = await accountFixture(page, true);
  await page.goto('/projects/10');
  await page.getByLabel('项目名称', { exact: true }).fill('我的合并草稿');
  state.conflict = true; state.project.name = '另一成员的新名称'; state.project.row_version = '2';
  await page.getByRole('button', { name: '保存项目信息' }).click();
  await page.getByRole('button', { name: '查看最新设置并手动合并' }).click();
  await expect(page.getByRole('dialog').getByText('另一成员的新名称')).toBeVisible();
  await page.getByRole('button', { name: '保留输入，基于最新版本继续编辑' }).click();
  await expect(page.getByLabel('项目名称', { exact: true })).toHaveValue('我的合并草稿');
  state.conflict = false;
  await page.getByRole('button', { name: '保存项目信息' }).click();
  await expect(page.getByText('已保存至服务端', { exact: true })).toBeVisible();
  expect(state.patches.at(-1)?.row_version).toBe('2');
});

test('same-name recipients require choosing a unique account before inviting', async ({ page }) => {
  const state = await accountFixture(page, true);
  await page.goto('/projects/10?section=collaboration');
  await page.getByRole('button', { name: '邀请协作者' }).click();
  await page.getByLabel('搜索人名、账号名、账号 ID 或邮箱').fill('同名创作者');
  await page.getByRole('button', { name: '搜索账号', exact: true }).click();
  await expect(page.locator('.collaboration-search-result')).toHaveCount(2);
  expect(state.invited).toBe(false);
  await page.locator('.collaboration-search-result').filter({ hasText: '@editor' }).click();
  await page.getByRole('button', { name: '为此受邀人生成链接' }).click();
  await expect(page.getByLabel('邀请链接')).toHaveValue(`http://127.0.0.1:4175/invite/${token}`);
  expect(state.invited).toBe(true);
});

test('invitation preview requests login when the locally remembered session has expired', async ({ page }) => {
  const state = await accountFixture(page, true);
  await page.goto('/projects/10');
  await expect(page.getByLabel('项目名称', { exact: true })).toBeVisible();
  state.expired = true;
  await page.evaluate(path => { history.pushState(null, '', path); window.dispatchEvent(new PopStateEvent('popstate')); }, `/invite/${token}`);
  await expect(page.getByRole('link', { name: '登录受邀账号' })).toBeVisible();
  await expect(page.getByRole('button', { name: '发送本次邀请验证码' })).not.toBeVisible();
  await page.getByRole('link', { name: '登录受邀账号' }).click();
  await page.getByLabel('账号名', { exact: true }).fill('owner');
  await page.getByLabel('密码', { exact: true }).fill('strong-fixture-password');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('button', { name: '发送本次邀请验证码' })).toBeVisible();
});

test('a wrong-account invitation provides a complete switch-account path', async ({ page }) => {
  const state = await accountFixture(page, true); state.inviteMismatch = true;
  await page.goto(`/invite/${token}`);
  await expect(page.getByText('此链接属于另一个受邀账号，请切换到受邀账号。')).toBeVisible();
  await page.getByRole('button', { name: '切换受邀账号' }).click();
  await expect(page).toHaveURL(/\/login\?next=/);
  expect(state.signedIn).toBe(false);
  state.user = editor;
  await page.getByLabel('账号名', { exact: true }).fill('editor');
  await page.getByLabel('密码', { exact: true }).fill('strong-fixture-password');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByText(/当前账号：editor/)).toBeVisible();
});

test('disabled browser storage still allows login and project editing', async ({ page }) => {
  await page.addInitScript(() => {
    for (const name of ['sessionStorage', 'localStorage']) Object.defineProperty(window, name, { configurable: true, get() { throw new DOMException('Storage is disabled', 'SecurityError'); } });
  });
  await accountFixture(page);
  await page.goto('/login?next=%2Fprojects%2F10');
  await page.getByLabel('账号名', { exact: true }).fill('owner');
  await page.getByLabel('密码', { exact: true }).fill('strong-fixture-password');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByLabel('项目名称', { exact: true })).toBeVisible();
  await page.getByLabel('项目名称', { exact: true }).fill('无本地存储也能保存');
  await page.getByRole('button', { name: '保存项目信息' }).click();
  await expect(page.getByText('已保存至服务端', { exact: true })).toBeVisible();
});
