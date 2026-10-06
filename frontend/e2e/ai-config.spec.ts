import { expect, test, type Page } from '@playwright/test';
import { fixture } from './studio-fixture';
import type { AiRuntimeProfileDto } from '../src/api/types/ai-model-configs';
import type { BeefAPIConnectionDto } from '../src/api/types/ai-config-tools';

async function configsFixture(page: Page) {
  const base = await fixture(page, true);
  const config = {
    id: '78', name: '剧本模型', model_key: 'fixture-text', service_type: 'text',
    provider: '测试替身', base_url: 'https://fixture.invalid/v1', enabled: 1, is_default: 0,
    is_deleted: 0, row_version: '9007199254740993', has_api_key: true,
    has_secret_key: true, headers: [] as { name: string; has_value: boolean }[],
    credential_source: 'manual', runtime_profile: null as AiRuntimeProfileDto | null,
  };
  const state = { config, creates: 0, updates: [] as Record<string, unknown>[], defaults: [] as Record<string, unknown>[], defaultFailures: 0, updateConflicts: 0 };
  function apply(body: Record<string, unknown>) {
    const { apikey, secret_key, headers, ...publicFields } = body;
    Object.assign(state.config, publicFields);
    if (apikey !== undefined) state.config.has_api_key = apikey !== null;
    if (secret_key !== undefined) state.config.has_secret_key = secret_key !== null;
    if (Array.isArray(headers)) state.config.headers = headers.map(item => ({ name: item.name, has_value: true }));
  }
  await page.route('**/api/v1/ai-model-configs**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.slice('/api/v1'.length);
    const body = request.postDataJSON() ?? {};
    const reply = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path === '/ai-model-configs' && request.method() === 'GET') return reply({ items: [state.config], total: 1, offset: 0, limit: 20 });
    if (path === '/ai-model-configs' && request.method() === 'POST') {
      state.creates += 1;
      apply(body); state.config.row_version = '9007199254740994';
      return reply(state.config, 201);
    }
    if (path === '/ai-model-configs/78' && request.method() === 'GET') return reply(state.config);
    if (path === '/ai-model-configs/78' && request.method() === 'PATCH') {
      state.updates.push(body);
      if (state.updateConflicts > 0) { state.updateConflicts -= 1; return reply({ error: { code: 'CONFIG_VERSION_CONFLICT' } }, 409); }
      const nextVersion = String(BigInt(state.config.row_version) + 1n);
      apply(body); state.config.row_version = nextVersion;
      if (!state.config.enabled) state.config.is_default = 0;
      return reply(state.config);
    }
    if (path === '/ai-model-configs/78/default') {
      state.defaults.push(body);
      if (state.defaultFailures > 0) {
        state.defaultFailures -= 1;
        return reply({ error: { code: 'TEST_OFFLINE' } }, 503);
      }
      state.config.is_default = 1;
      state.config.row_version = String(BigInt(state.config.row_version) + 1n);
      return reply(state.config);
    }
    return route.fallback();
  });
  return { ...base, state };
}

const runtimeProfile: AiRuntimeProfileDto = {
  version: 1, api_format: 'openai', protocol: 'chat-completion',
  reference_asset_origin: 'https://assets.fixture.invalid',
  capability_config: { supportsThinking: true, maxOutputTokens: 4096 },
  default_options: { temperature: 0.35 },
  logical_capability_spec: { mode: 'text', input: { text: true } },
  logical_capability_profiles: [{ model: 'fixture-text', capability: 'text', protocol: 'chat-completion' }],
  video_capabilities_version: 'fixture-v1', concurrency_limit: 2,
};

async function toolsFixture(page: Page) {
  const data = await configsFixture(page);
  const state = {
    connection: { state: 'disconnected', enterpriseOrigin: 'https://enterprise.beefapi.com', hasCredential: false, balance: 'unknown', catalogFailed: false } as BeefAPIConnectionDto,
    calls: [] as string[], testKeys: [] as string[], testBodies: [] as Record<string, unknown>[],
    testFailures: 0, modelTest: { id: 'test-78', status: 'queued', canCancel: true } as Record<string, unknown>,
    discoveries: [] as Record<string, unknown>[],
  };
  await page.route('**/api/v1/canvas-runtime/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.slice('/api/v1/canvas-runtime'.length);
    state.calls.push(`${request.method()} ${path}`);
    const reply = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path === '/plugins/catalog') return reply({ providers: [{ id: 'chat-completion', name: 'OpenAI 聊天', enabled: true, categories: ['text'] }] });
    if (path === '/ai/models') {
      const body = request.postDataJSON(); state.discoveries.push(body);
      if (body.apiKey === null) return reply({ error: { code: 'canvas_model_catalog_credential' } }, 422);
      return reply({ models: [{ id: 'fixture-discovered' }] });
    }
    if (path === '/model-tests' && request.method() === 'POST') {
      state.testKeys.push(request.headers()['idempotency-key']); state.testBodies.push(request.postDataJSON());
      if (state.testFailures > 0) { state.testFailures -= 1; return reply({ error: { code: 'TEST_OFFLINE' } }, 503); }
      return reply(state.modelTest, 201);
    }
    if (path === '/model-tests/test-78') return reply(state.modelTest);
    if (path === '/model-tests/test-78/cancel') { state.modelTest = { id: 'test-78', status: 'cancelled', canCancel: false }; return reply(state.modelTest); }
    if (path === '/beefapi/connection') return reply(state.connection);
    if (path === '/beefapi/connection/start') {
      state.connection = { ...state.connection, state: 'pending', hasCredential: false, userCode: 'FIXTURE-ONLY', verificationUri: 'https://enterprise.beefapi.com/desktop-auth?state=fixture' };
      return reply(state.connection, 201);
    }
    if (path === '/beefapi/connection/cancel') { state.connection = { ...state.connection, state: 'cancelled', hasCredential: false }; return reply(state.connection); }
    if (path === '/beefapi/connection/disconnect') { state.connection = { ...state.connection, state: 'disconnected', hasCredential: false }; return reply(state.connection); }
    if (path === '/beefapi/connection/open-wallet') return reply({ enterpriseOrigin: 'https://enterprise.beefapi.com', walletUrl: 'https://enterprise.beefapi.com/console/topup' });
    return route.fallback();
  });
  return { ...data, tools: state };
}

async function fakeEnterpriseWindows(page: Page) {
  await page.addInitScript(() => {
    const windows: { url: string; closed: boolean; opener: unknown }[] = [];
    Object.defineProperty(window, '__fixtureWindows', { value: windows });
    window.open = () => {
      const popup = { url: 'about:blank', closed: false, opener: {} as unknown,
        location: { replace: (url: string) => { popup.url = url; } }, close: () => { popup.closed = true; } };
      windows.push(popup);
      return popup as unknown as Window;
    };
  });
}

test('AI settings use the new route, separate columns, and a single material navigation entry', async ({ page }) => {
  const data = await configsFixture(page);
  await page.goto('/ai');
  await expect(page).toHaveURL(/\/ai_config$/);
  await expect(page.getByRole('heading', { name: 'AI 配置', exact: true })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: '模型', exact: true })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: '服务地址', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '刷新列表', exact: true })).toHaveCount(0);
  await expect(page.getByText('为每类选择一个默认模型，创作时自动选用。保存配置不会发起生成。', { exact: true })).toHaveCount(0);
  const navigation = page.getByRole('navigation', { name: '主导航', exact: true });
  await expect(navigation.getByRole('link', { name: '素材库', exact: true })).toHaveCount(1);
  for (const name of ['角色', '场景', '道具']) await expect(navigation.getByRole('link', { name, exact: true })).toHaveCount(0);
  expect(data.errors).toEqual([]);
  expect(data.unexpected).toEqual([]);
});

test('editing can set a default using the version returned by saving the configuration', async ({ page }) => {
  const data = await configsFixture(page);
  await page.goto('/ai_config');
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  const form = page.getByRole('dialog', { name: '编辑文本模型', exact: true });
  await form.getByRole('checkbox', { name: '设为默认配置', exact: true }).check();
  await form.getByRole('textbox', { name: '配置名称', exact: true }).fill('新的剧本模型');
  await form.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(form).toHaveCount(0);
  await expect(page.getByText('默认模型', { exact: true })).toBeVisible();
  expect(data.state.updates[0].row_version).toBe('9007199254740993');
  expect(data.state.defaults).toEqual([{ row_version: '9007199254740994' }]);
  expect(data.state.config.name).toBe('新的剧本模型');
  expect(data.errors).toEqual([]);
});

test('a default failure preserves the saved identity and draft so retry never creates a duplicate', async ({ page }) => {
  const data = await configsFixture(page);
  data.state.defaultFailures = 1;
  await page.goto('/ai_config');
  await page.getByRole('button', { name: '添加文本模型', exact: true }).click();
  const form = page.getByRole('dialog', { name: /添加文本模型|编辑文本模型/ });
  await form.getByRole('textbox', { name: '配置名称', exact: true }).fill('可重试的配置');
  await form.getByRole('textbox', { name: '厂商名称', exact: true }).fill('测试替身');
  await form.getByRole('combobox', { name: '模型标识', exact: true }).fill('fixture-text');
  await form.getByRole('checkbox', { name: '设为默认配置', exact: true }).check();
  await form.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(form.getByRole('alert')).toContainText('配置内容已保存，但设为默认配置未完成');
  await expect(form.getByRole('textbox', { name: '配置名称', exact: true })).toHaveValue('可重试的配置');
  await expect(form.getByRole('checkbox', { name: '设为默认配置', exact: true })).toBeChecked();
  await form.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(form).toHaveCount(0);
  expect(data.state.creates).toBe(1);
  expect(data.state.updates[0].row_version).toBe('9007199254740994');
  expect(data.state.defaults).toEqual([{ row_version: '9007199254740994' }, { row_version: '9007199254740995' }]);
  expect(data.errors).toEqual([]);
});

test('a disabled configuration cannot be selected as default and disabling the current default clears it', async ({ page }) => {
  const data = await configsFixture(page);
  data.state.config.is_default = 1;
  await page.goto('/ai_config');
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  const form = page.getByRole('dialog', { name: '编辑文本模型', exact: true });
  const defaultChoice = form.getByRole('checkbox', { name: '设为默认配置', exact: true });
  await expect(defaultChoice).toBeChecked();
  await expect(defaultChoice).toBeDisabled();
  await form.getByRole('checkbox', { name: '启用此配置', exact: true }).uncheck();
  await expect(defaultChoice).not.toBeChecked();
  await expect(defaultChoice).toBeDisabled();
  await form.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(form).toHaveCount(0);
  expect(data.state.config.is_default).toBe(0);
  expect(data.state.defaults).toEqual([]);
  expect(data.errors).toEqual([]);
});

test('advanced editing retains every runtime field without exposing or resubmitting saved credentials', async ({ page }) => {
  const data = await toolsFixture(page);
  data.state.config.runtime_profile = structuredClone(runtimeProfile);
  data.state.config.headers = [{ name: 'X-Workspace', has_value: true }];
  await page.goto('/ai_config');
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  const form = page.getByRole('dialog', { name: '编辑文本模型', exact: true });
  await expect(form.getByLabel('API 密钥', { exact: true })).toHaveValue('');
  await expect(form.getByLabel('Secret Key（第二密钥）', { exact: true })).toHaveValue('');
  await form.locator('summary').filter({ hasText: /^自定义请求头/ }).click();
  await expect(form.getByLabel('请求头 1 值', { exact: true })).toHaveValue('');
  const advanced = form.locator('summary').filter({ hasText: /^高级配置/ });
  await advanced.focus(); await page.keyboard.press('Enter');
  await expect(form.getByLabel('请求协议', { exact: true })).toHaveValue('chat-completion');
  await expect(form.getByLabel(/^默认参数 JSON/)).toHaveValue(JSON.stringify(runtimeProfile.default_options, null, 2));
  await page.screenshot({ path: '.runtime/ai-config-runtime-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(form.getByLabel(/^逻辑能力配置集 JSON/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.screenshot({ path: '.runtime/ai-config-runtime-mobile.png' });
  await form.getByRole('textbox', { name: '配置名称', exact: true }).fill('完整保留的配置');
  await form.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(form).toHaveCount(0);
  expect(data.state.updates[0].runtime_profile).toEqual(runtimeProfile);
  for (const key of ['apikey', 'secret_key', 'headers']) expect(data.state.updates[0]).not.toHaveProperty(key);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('a persisted minimal runtime profile restores null optionals as empty fields and allows an unchanged-profile rename', async ({ page }) => {
  const data = await toolsFixture(page);
  data.state.config.runtime_profile = { version: 1, api_format: 'openai', protocol: 'chat-completion', reference_asset_origin: null, capability_config: null, default_options: { temperature: 0 }, logical_capability_spec: null, logical_capability_profiles: null, video_capabilities_version: null, concurrency_limit: null };
  await page.goto('/ai_config');
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  const form = page.getByRole('dialog', { name: '编辑文本模型', exact: true });
  await form.locator('summary').filter({ hasText: /^高级配置/ }).click();
  for (const label of ['参考资源源站', '能力配置 JSON', '逻辑能力规范 JSON', '逻辑能力配置集 JSON', '视频能力版本', '并发上限']) await expect(form.getByLabel(new RegExp(`^${label}`))).toHaveValue('');
  await expect(form.getByLabel(/^默认参数 JSON/)).toHaveValue(JSON.stringify({ temperature: 0 }, null, 2));
  await form.getByLabel('配置名称', { exact: true }).fill('重载后修改名称');
  await form.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(form).toHaveCount(0);
  expect(data.state.updates).toHaveLength(1);
  expect(data.state.updates[0]).toMatchObject({ name: '重载后修改名称', runtime_profile: { version: 1, api_format: 'openai', protocol: 'chat-completion', default_options: { temperature: 0 } } });
  for (const key of ['apikey', 'secret_key', 'headers']) expect(data.state.updates[0]).not.toHaveProperty(key);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('invalid JSON never submits and a version conflict preserves advanced and secret drafts without automatic retry', async ({ page }) => {
  const data = await toolsFixture(page);
  data.state.config.runtime_profile = structuredClone(runtimeProfile);
  data.state.updateConflicts = 1;
  await page.goto('/ai_config');
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  const form = page.getByRole('dialog', { name: '编辑文本模型', exact: true });
  await form.locator('summary').filter({ hasText: /^高级配置/ }).click();
  await form.getByLabel(/^默认参数 JSON/).fill('{broken');
  await form.getByLabel('Secret Key（第二密钥）', { exact: true }).fill('fixture-only-secret-draft');
  await form.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(form.getByRole('alert')).toContainText('不是有效 JSON');
  expect(data.state.updates).toEqual([]);
  await form.getByLabel(/^默认参数 JSON/).fill('{"temperature":0.6}');
  await form.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(form.getByRole('alert')).toContainText('数据已被修改');
  await expect(form.getByLabel(/^默认参数 JSON/)).toHaveValue('{"temperature":0.6}');
  await expect(form.getByLabel('Secret Key（第二密钥）', { exact: true })).toHaveValue('fixture-only-secret-draft');
  await expect(form.getByRole('button', { name: '保存配置', exact: true })).toBeDisabled();
  expect(data.state.updates).toHaveLength(1);
  await form.getByRole('button', { name: '取消', exact: true }).click();
  await page.getByRole('dialog', { name: '未保存的修改', exact: true }).getByRole('button', { name: '取消', exact: true }).click();
  await expect(form.getByLabel(/^默认参数 JSON/)).toHaveValue('{"temperature":0.6}');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('clearing keys and deleting all headers uses explicit null and an empty list', async ({ page }) => {
  const data = await toolsFixture(page);
  data.state.config.headers = [{ name: 'X-Workspace', has_value: true }];
  await page.goto('/ai_config');
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  const form = page.getByRole('dialog', { name: '编辑文本模型', exact: true });
  await form.getByRole('checkbox', { name: '清除已保存的密钥', exact: true }).check();
  await form.getByRole('checkbox', { name: '清除已保存的第二密钥', exact: true }).check();
  await form.locator('summary').filter({ hasText: /^自定义请求头/ }).click();
  await form.getByRole('button', { name: '删除请求头 1', exact: true }).click();
  await form.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(form).toHaveCount(0);
  expect(data.state.updates[0]).toMatchObject({ apikey: null, secret_key: null, headers: [], runtime_profile: null });
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('BeefAPI managed protocol and keys are read-only while private headers remain editable', async ({ page }) => {
  const data = await toolsFixture(page);
  data.state.config.credential_source = 'beefapi';
  data.state.config.runtime_profile = structuredClone(runtimeProfile);
  await page.goto('/ai_config');
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  const form = page.getByRole('dialog', { name: '编辑文本模型', exact: true });
  for (const label of ['厂商名称', '服务地址（Base URL）', 'API 密钥', 'Secret Key（第二密钥）']) await expect(form.getByLabel(label, { exact: true })).toBeDisabled();
  await form.locator('summary').filter({ hasText: /^高级配置/ }).click();
  await expect(form.getByLabel('请求协议', { exact: true })).toBeDisabled();
  await form.locator('summary').filter({ hasText: /^自定义请求头/ }).click();
  await form.getByRole('button', { name: '添加请求头', exact: true }).click();
  await form.getByLabel('请求头 1 名称', { exact: true }).fill('X-Workspace');
  await form.getByLabel('请求头 1 值', { exact: true }).fill('fixture-only-header');
  await form.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(form).toHaveCount(0);
  expect(data.state.updates[0].headers).toEqual([{ name: 'X-Workspace', value: 'fixture-only-header' }]);
  expect(data.state.updates[0].runtime_profile).toEqual(runtimeProfile);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('returning to the independent canvas preserves the deep link and is disabled while editing', async ({ page }) => {
  const data = await configsFixture(page);
  const target = '/canvas-app/canvas/999900001111?panel=model%2Fdetail#node-fixture';
  await page.route('**/canvas-app/canvas/**', route => route.fulfill({ contentType: 'text/html', body: '<main>画布入口测试替身</main>' }));
  await page.goto(`/ai_config?return_to=${encodeURIComponent(target)}`);
  const back = page.getByRole('button', { name: '返回画布', exact: true });
  await expect(back).toBeEnabled();
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  await expect(back).toBeDisabled();
  await page.getByRole('dialog', { name: '编辑文本模型', exact: true }).getByRole('button', { name: '取消', exact: true }).click();
  await back.click();
  await expect(page).toHaveURL(`http://127.0.0.1:4175${target}`);
  for (const unsafeTarget of ['https://external.fixture.invalid', '/canvas-app/canvas/../settings', '/canvas-app/settings']) {
    await page.goto(`/ai_config?return_to=${encodeURIComponent(unsafeTarget)}`);
    await expect(page.getByRole('button', { name: '返回画布', exact: true })).toHaveCount(0);
  }
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('unknown model-test admission reuses its original identity after closing and reports success only after a completed result', async ({ page }) => {
  const data = await toolsFixture(page);
  data.tools.testFailures = 1;
  await page.goto('/ai_config');
  const open = async () => {
    await page.getByRole('button', { name: '更多操作：剧本模型', exact: true }).click();
    await page.getByRole('menuitem', { name: '测试模型', exact: true }).click();
  };
  await open();
  let dialog = page.getByRole('dialog', { name: '测试模型 · 剧本模型', exact: true });
  await expect(dialog).toContainText('可能产生费用');
  await dialog.getByRole('button', { name: '开始模型测试', exact: true }).click();
  await expect(dialog).toContainText('受理结果尚未确认');
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  await open();
  dialog = page.getByRole('dialog', { name: '测试模型 · 剧本模型', exact: true });
  await dialog.getByRole('button', { name: '核对原测试', exact: true }).click();
  await expect(dialog.getByRole('status')).toHaveText('排队中');
  expect(data.tools.testKeys).toHaveLength(2);
  expect(data.tools.testKeys[0]).toBe(data.tools.testKeys[1]);
  expect(data.tools.testBodies[0]).toEqual(data.tools.testBodies[1]);
  expect(data.tools.testBodies[0]).toMatchObject({ channel: { id: 'host-78', scope: 'system' } });
  await expect(dialog).not.toContainText('模型已返回真实结果');
  data.tools.modelTest = { id: 'test-78', status: 'succeeded', canCancel: false, result: { mode: 'text', text: 'OK，测试替身结果' } };
  await expect(dialog.getByRole('status')).toHaveText('模型已返回真实结果');
  await expect(dialog).toContainText('OK，测试替身结果');
  expect(data.state.creates).toBe(0);
  expect(data.requests.filter(item => item.method === 'POST' && item.path === '/projects')).toEqual([]);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('a cancellable model test uses the server cancellation receipt', async ({ page }) => {
  const data = await toolsFixture(page);
  await page.goto('/ai_config');
  await page.getByRole('button', { name: '更多操作：剧本模型', exact: true }).click();
  await page.getByRole('menuitem', { name: '测试模型', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '测试模型 · 剧本模型', exact: true });
  await dialog.getByRole('button', { name: '开始模型测试', exact: true }).click();
  await dialog.getByRole('button', { name: '取消测试任务', exact: true }).click();
  await expect(dialog.getByRole('status')).toHaveText('测试已取消');
  expect(data.tools.calls).toContain('POST /model-tests/test-78/cancel');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('official connection retains connect, cancel, reconnect, wallet and confirmed disconnect operations', async ({ page }) => {
  const data = await toolsFixture(page);
  await fakeEnterpriseWindows(page);
  await page.goto('/ai_config');
  expect(data.tools.calls).toEqual([]);
  await page.locator('summary').filter({ hasText: /^BeefAPI 官方连接/ }).click();
  await expect(page.getByText('未连接', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '连接 BeefAPI', exact: true }).click();
  await expect(page.getByText('等待浏览器授权', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __fixtureWindows: { url: string; opener: unknown }[] }).__fixtureWindows[0])).toMatchObject({ url: 'https://enterprise.beefapi.com/desktop-auth?state=fixture', opener: null });
  await page.getByRole('button', { name: '取消授权', exact: true }).click();
  await expect(page.getByText('已取消授权', { exact: true })).toBeVisible();
  data.tools.connection = { ...data.tools.connection, state: 'connected', hasCredential: true, account: { id: 'fixture-account', display_name: '测试替身账号' } };
  await page.getByRole('button', { name: '重新读取状态', exact: true }).click();
  await expect(page.getByText('已连接 · 测试替身账号', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '打开钱包', exact: true }).click();
  expect(await page.evaluate(() => (window as unknown as { __fixtureWindows: { url: string }[] }).__fixtureWindows[1].url)).toBe('https://enterprise.beefapi.com/console/topup');
  const beforeReconnect = data.tools.calls.length;
  await page.getByRole('button', { name: '重新连接', exact: true }).click();
  await expect(page.getByText(/等待浏览器授权/).first()).toBeVisible();
  expect(data.tools.calls.slice(beforeReconnect)).toContain('POST /beefapi/connection/disconnect');
  expect(data.tools.calls.slice(beforeReconnect)).toContain('POST /beefapi/connection/start');
  data.tools.connection = { ...data.tools.connection, state: 'connected', hasCredential: true };
  await page.getByRole('button', { name: '重新读取状态', exact: true }).click();
  await page.getByRole('button', { name: '断开连接', exact: true }).click();
  await page.getByRole('dialog', { name: '断开官方连接', exact: true }).getByRole('button', { name: '断开连接', exact: true }).click();
  await expect(page.getByText(/未连接/).first()).toBeVisible();
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

for (const advanced of [false, true]) {
  test(`${advanced ? 'runtime' : 'ordinary'} discovery retains same-address header placeholders, rejects masked headers at a changed address, and clears keys explicitly`, async ({ page }) => {
    const data = await toolsFixture(page);
    data.state.config.runtime_profile = advanced ? structuredClone(runtimeProfile) : null;
    data.state.config.headers = [{ name: 'X-Workspace', has_value: true }];
    await page.goto('/ai_config');
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    const form = page.getByRole('dialog', { name: '编辑文本模型', exact: true });
    expect(data.tools.discoveries).toEqual([]);
    await form.getByRole('button', { name: '获取模型', exact: true }).click();
    await expect(form.getByRole('status')).toContainText('已获取 1 个模型');
    expect(data.tools.discoveries[0]).toEqual({ baseUrl: 'https://fixture.invalid/v1', apiFormat: 'openai', channelId: 'host-78', credentialRef: 'host:78', headers: [{ name: 'X-Workspace', value: '' }] });
    await form.getByRole('checkbox', { name: '清除已保存的密钥', exact: true }).check();
    await form.getByRole('button', { name: '获取模型', exact: true }).click();
    await expect.poll(() => data.tools.discoveries.length).toBe(2);
    expect(data.tools.discoveries[1]).toMatchObject({ channelId: 'host-78', apiKey: null });
    await expect(form.getByRole('alert')).toContainText('请填写有效的 API 密钥及请求头');
    await form.getByRole('checkbox', { name: '清除已保存的密钥', exact: true }).uncheck();
    await form.getByLabel('服务地址（Base URL）', { exact: true }).fill('https://new.fixture.invalid/v1');
    await form.getByLabel('API 密钥', { exact: true }).fill('fixture-only-new-address-key');
    await form.getByRole('button', { name: '获取模型', exact: true }).click();
    await expect(form.getByRole('alert')).toContainText('服务地址已修改');
    expect(data.tools.discoveries).toHaveLength(2);
    await form.locator('summary').filter({ hasText: /^自定义请求头/ }).click();
    await form.getByLabel('请求头 1 值', { exact: true }).fill('fixture-only-new-address-header');
    await form.getByRole('button', { name: '获取模型', exact: true }).click();
    await expect.poll(() => data.tools.discoveries.length).toBe(3);
    expect(data.tools.discoveries[2]).toEqual({ baseUrl: 'https://new.fixture.invalid/v1', apiFormat: 'openai', apiKey: 'fixture-only-new-address-key', headers: [{ name: 'X-Workspace', value: 'fixture-only-new-address-header' }] });
    expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
  });
}

test('ordinary discovery sends newly entered headers only after an explicit click and leaves the runtime disabled', async ({ page }) => {
  const data = await toolsFixture(page);
  await page.goto('/ai_config');
  await page.getByRole('button', { name: '添加文本模型', exact: true }).click();
  const form = page.getByRole('dialog', { name: '添加文本模型', exact: true });
  await form.getByLabel('配置名称', { exact: true }).fill('带请求头的普通配置');
  await form.getByLabel('厂商名称', { exact: true }).fill('测试替身');
  await form.getByLabel('服务地址（Base URL）', { exact: true }).fill('https://new.fixture.invalid/v1');
  await form.getByLabel('API 密钥', { exact: true }).fill('fixture-only-new-key');
  await form.getByLabel('Secret Key（第二密钥）', { exact: true }).fill('fixture-only-second-key');
  await form.locator('summary').filter({ hasText: /^自定义请求头/ }).click();
  await form.getByRole('button', { name: '添加请求头', exact: true }).click();
  await form.getByLabel('请求头 1 名称', { exact: true }).fill('X-Workspace');
  await form.getByLabel('请求头 1 值', { exact: true }).fill('fixture-only-new-header');
  expect(data.tools.discoveries).toEqual([]);
  await form.getByRole('button', { name: '获取模型', exact: true }).click();
  await expect(form.getByRole('status')).toContainText('已获取 1 个模型');
  expect(data.tools.discoveries).toEqual([{ baseUrl: 'https://new.fixture.invalid/v1', apiFormat: 'openai', apiKey: 'fixture-only-new-key', headers: [{ name: 'X-Workspace', value: 'fixture-only-new-header' }] }]);
  expect(data.requests.filter(item => item.path === '/ai-model-configs/discover-models')).toEqual([]);
  await form.getByRole('combobox', { name: '模型标识', exact: true }).fill('fixture-manual');
  await form.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(form).toHaveCount(0);
  expect(data.state.config.runtime_profile).toBeNull();
  expect(data.state.creates).toBe(1);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('ordinary discovery deleting every saved header submits an empty list and never restores the deleted headers', async ({ page }) => {
  const data = await toolsFixture(page);
  data.state.config.headers = [{ name: 'X-Workspace', has_value: true }];
  await page.goto('/ai_config');
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  const form = page.getByRole('dialog', { name: '编辑文本模型', exact: true });
  await form.locator('summary').filter({ hasText: /^自定义请求头/ }).click();
  await form.getByRole('button', { name: '删除请求头 1', exact: true }).click();
  await form.getByRole('button', { name: '获取模型', exact: true }).click();
  await expect(form.getByRole('status')).toContainText('已获取 1 个模型');
  expect(data.tools.discoveries).toEqual([{ baseUrl: 'https://fixture.invalid/v1', apiFormat: 'openai', channelId: 'host-78', credentialRef: 'host:78', headers: [] }]);
  expect(data.requests.filter(item => item.path === '/ai-model-configs/discover-models')).toEqual([]);
  await form.getByRole('combobox', { name: '模型标识', exact: true }).fill('fixture-manual');
  await form.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(form).toHaveCount(0);
  expect(data.state.updates[0].model_key).toBe('fixture-manual');
  expect(data.state.updates[0].headers).toEqual([]);
  expect(data.state.updates[0].runtime_profile).toBeNull();
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('catalog errors use fixed public feedback without reflecting supplier credentials or raw messages', async ({ page }) => {
  const data = await toolsFixture(page);
  data.state.config.runtime_profile = structuredClone(runtimeProfile);
  await page.route('**/api/v1/canvas-runtime/ai/models', route => route.fulfill({ status: 422, json: { error: { code: 'canvas_model_catalog_format', message: 'fixture-sensitive-message-that-must-not-render' } } }));
  await page.goto('/ai_config');
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  const form = page.getByRole('dialog', { name: '编辑文本模型', exact: true });
  await form.getByRole('button', { name: '获取模型', exact: true }).click();
  await expect(form.getByRole('alert')).toContainText('该协议不支持读取模型目录');
  await expect(page.locator('body')).not.toContainText('fixture-sensitive-message-that-must-not-render');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});
