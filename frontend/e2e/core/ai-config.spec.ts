import { expect, test, type Page } from '@playwright/test';
import type { AiModelConfigDto, ModelConfigurationOptionsDto } from '../../src/types/ai-config/api';
import type { AssistantSettings } from '../../src/types/assistant/api';
import type { MemoryItem } from '../../src/types/assistant/settings/memory';
import { fixture } from '../support/studio-fixture';

async function settingsFixture(page: Page) {
  const base = await fixture(page, true);
  const textOptions: ModelConfigurationOptionsDto = {
    interfaces: [
      { id: 'auto', label: '自动识别', api_format: 'auto', requires_secret_key: false },
      { id: 'chat-completion', label: 'OpenAI Chat Completions', api_format: 'openai', requires_secret_key: false },
      { id: 'openai-response', label: 'OpenAI Responses', api_format: 'openai', requires_secret_key: false },
      { id: 'claude-messages', label: 'Claude Messages', api_format: 'claude', requires_secret_key: false },
    ],
    providers: [{ id: 'openai', label: 'OpenAI', base_url: 'https://api.openai.com/v1', interface_type: 'chat-completion' }], models: [],
  };
  const row: AiModelConfigDto = {
    id: '78', service_type: 'text', name: '剧本模型', provider: 'openai', model_key: 'gpt-5',
    base_url: 'https://api.openai.com/v1', enabled: 1, is_deleted: 0, is_default: 0,
    row_version: '9007199254740993', has_api_key: true, has_secret_key: false,
    headers: [], credential_source: 'manual', runtime_profile: { version: 1, api_format: 'openai', protocol: 'chat-completion', default_options: { temperature: 0 } },
    interface_type: 'chat-completion', create_key: null, created_at: '2026-11-04T00:00:00Z', updated_at: '2026-11-04T00:00:00Z',
  };
  const value: AssistantSettings = { custom_instructions: null, default_instructions: '系统默认指令',
    workbench_enabled: true, media_generation_enabled: true, long_term_memory_enabled: true, settings_version: '9007199254740993' };
  const state = {
    rows: [row], value, updates: [] as Record<string, unknown>[], creates: [] as { key: string; body: Record<string, unknown> }[],
    defaults: [] as Record<string, unknown>[], patches: [] as Record<string, unknown>[], discoveries: [] as Record<string, unknown>[],
    loseCreate: false, loseTest: false, conflict: false, modelTests: [] as { key: string; body: Record<string, unknown> }[],
  };
  await page.route('**/api/v1/assistant/settings', route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: state.value });
    const body = route.request().postDataJSON(); state.patches.push(body);
    if (state.conflict) {
      state.conflict = false; state.value.custom_instructions = '另一个窗口的指令';
      state.value.settings_version = String(BigInt(state.value.settings_version) + 1n);
      return route.fulfill({ status: 409, json: { error: { code: 'conflict', message: '设置版本冲突' } } });
    }
    Object.assign(state.value, body, { settings_version: String(BigInt(state.value.settings_version) + 1n) });
    return route.fulfill({ json: state.value });
  });
  await page.route('**/api/v1/ai-model-configs**', route => {
    const request = route.request(), url = new URL(request.url());
    const body: Record<string, unknown> = request.postDataJSON() ?? {};
    if (url.pathname.endsWith('/options')) return route.fulfill({ json: textOptions });
    if (url.pathname.endsWith('/discover-models')) { state.discoveries.push(body); return route.fulfill({ json: { items: [{ id: 'gpt-5' }, { id: 'gpt-5-mini' }], truncated: false, status: 'available', source: 'provider' } }); }
    if (url.pathname.endsWith('/default')) {
      state.defaults.push(body); row.is_default = 1; row.row_version = String(BigInt(row.row_version) + 1n);
      return route.fulfill({ json: row });
    }
    if (request.method() === 'PATCH') {
      state.updates.push(body); Object.assign(row, body, { row_version: String(BigInt(row.row_version) + 1n) });
      if ('apikey' in body) row.has_api_key = body.apikey !== null;
      return route.fulfill({ json: row });
    }
    if (request.method() === 'POST') {
      const key = request.headers()['idempotency-key']; state.creates.push({ key, body });
      const created = { ...row, ...body, id: '79', create_key: key } as AiModelConfigDto;
      state.rows.push(created);
      return state.loseCreate ? route.abort('failed') : route.fulfill({ status: 201, json: created });
    }
    if (url.pathname.endsWith('/78')) return route.fulfill({ json: row });
    const key = url.searchParams.get('create_key'), type = url.searchParams.get('service_type');
    const rows = state.rows.filter(item => (!key || item.create_key === key) && (!type || item.service_type === type));
    return route.fulfill({ json: { items: rows, total: rows.length, offset: 0, limit: 20 } });
  });
  await page.route('**/api/v1/canvas-runtime/model-tests', route => {
    state.modelTests.push({ key: route.request().headers()['idempotency-key'], body: route.request().postDataJSON() });
    if (state.loseTest) { state.loseTest = false; return route.abort('failed'); }
    return route.fulfill({ status: 202, json: { id: '501', status: 'succeeded', canCancel: false, result: { text: 'OK' } } });
  });
  return { ...base, state };
}

for (const width of [1280, 1440]) test(`配置在工作台壳内展示，原位获取模型且编辑测试使用图标 ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 900 });
  const { state, errors } = await settingsFixture(page);
  await page.goto('/settings/models/text');
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: '配置导航' })).toBeVisible();
  await expect(page.getByRole('searchbox')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '编辑模型：剧本模型' })).toBeVisible();
  await page.getByRole('button', { name: '新增文本模型' }).click();
  const dialog = page.getByRole('dialog', { name: '新增文本模型' });
  await dialog.getByLabel('服务商', { exact: true }).selectOption('openai');
  await expect(dialog.getByLabel('接口类型')).toHaveValue('chat-completion');
  await expect(dialog.getByLabel('接口类型').locator('option')).toHaveCount(4);
  const model = dialog.getByRole('combobox', { name: '模型标识' }); await model.fill('手填模型');
  await dialog.getByRole('button', { name: '获取模型', exact: true }).click();
  await dialog.getByText('gpt-5-mini', { exact: true }).click();
  await expect(model).toHaveValue('gpt-5-mini');
  await expect(dialog.getByRole('combobox', { name: '模型标识' })).toHaveCount(1);
  const address = dialog.getByLabel('服务地址'); await address.focus();
  expect(await address.evaluate(element => getComputedStyle(element).outlineStyle)).toBe('none');
  expect(await address.evaluate(element => getComputedStyle(element).boxShadow)).toBe('none');
  await page.screenshot({ path: info.outputPath(`model-dialog-${width}.png`) });
  await model.focus(); await model.press('Escape');
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('.ant-select-open')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: '未保存的修改' }).getByRole('button', { name: '放弃修改' }).click();
  await expect(dialog).not.toBeVisible();
  expect(state.discoveries[0]).toMatchObject({ service_type: 'text', interface_type: 'chat-completion' });
  expect(errors).toEqual([]);
});

test('直接删除遮罩密钥，改名保留隐藏参数，默认操作使用新版本', async ({ page }) => {
  const { state } = await settingsFixture(page); await page.goto('/settings/models/text');
  await page.getByRole('button', { name: '编辑模型：剧本模型' }).click();
  const dialog = page.getByRole('dialog', { name: '编辑文本模型' });
  await dialog.getByLabel('显示名称').fill('新模型');
  await dialog.getByLabel('API 密钥', { exact: true }).press('Backspace');
  await dialog.getByRole('switch', { name: '设为默认' }).click();
  await dialog.getByRole('button', { name: '保存', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(state.updates[0]).toMatchObject({ apikey: null, name: '新模型' });
  expect(state.updates[0]).not.toHaveProperty('interface_type');
  expect(state.updates[0]).not.toHaveProperty('runtime_profile');
  expect(state.defaults[0].row_version).toBe('9007199254740994');
  await page.getByRole('button', { name: '编辑模型：新模型' }).click();
  await expect(dialog.getByRole('switch', { name: '设为默认' })).toBeChecked();
  await expect(dialog.getByRole('switch', { name: '设为默认' })).toBeDisabled();
});

test('创建响应丢失按原create_key核对，模型测试沿原身份核对', async ({ page }) => {
  const { state } = await settingsFixture(page); state.loseCreate = true;
  await page.goto('/settings/models/text'); await page.getByRole('button', { name: '新增文本模型' }).click();
  const dialog = page.getByRole('dialog', { name: '新增文本模型' });
  await dialog.getByLabel('服务商', { exact: true }).selectOption('openai');
  await dialog.getByRole('combobox', { name: '模型标识' }).fill('gpt-5-mini');
  await dialog.getByRole('button', { name: '保存', exact: true }).click();
  await dialog.getByRole('button', { name: '核对保存结果', exact: true }).click();
  await expect(dialog).not.toBeVisible(); expect(state.creates).toHaveLength(1);
  state.loseTest = true; await page.getByRole('button', { name: '测试模型：剧本模型', exact: true }).first().click();
  await page.getByRole('button', { name: '开始模型测试' }).click();
  await page.getByRole('button', { name: '核对原测试' }).click();
  await expect(page.getByText('OK', { exact: true })).toBeVisible();
  expect(state.modelTests).toHaveLength(2);
  expect(state.modelTests[0].key).toBe(state.modelTests[1].key);
  expect(state.modelTests[0].body).toMatchObject({ config_id: '78' });
  expect(state.modelTests[0].body).not.toHaveProperty('channel');
});

test('助手父子开关和指令跨页静默自动保存，仅提交改动字段', async ({ page }) => {
  const { state } = await settingsFixture(page); await page.goto('/settings/assistant/general');
  await page.getByRole('switch', { name: '工作台操作', exact: true }).click();
  await expect(page.getByRole('switch', { name: '允许生成图片、视频、音频' })).toHaveCount(0);
  await expect.poll(() => state.patches.length).toBe(1);
  expect(state.patches[0]).toEqual({ settings_version: '9007199254740993', workbench_enabled: false });
  await page.getByRole('link', { name: '个性化指令', exact: true }).click();
  await page.getByRole('textbox').fill('对白保持简短');
  await page.getByRole('link', { name: '长期记忆', exact: true }).click();
  await expect.poll(() => state.value.custom_instructions).toBe('对白保持简短');
  await expect(page.getByRole('button', { name: '保存', exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '长期记忆', exact: true })).toHaveCount(0);
  expect(state.patches[1]).not.toHaveProperty('workbench_enabled');
});

test('指令冲突保留输入且关闭核对不自动选择', async ({ page }) => {
  const { state } = await settingsFixture(page); state.conflict = true;
  await page.goto('/settings/assistant/instructions');
  const input = page.getByRole('textbox'); await input.fill('我的创作偏好');
  const review = page.getByRole('dialog', { name: '核对设置变更' }); await expect(review).toBeVisible();
  await review.getByRole('button', { name: '关闭弹窗' }).click();
  await expect(input).toHaveValue('我的创作偏好'); expect(state.patches).toHaveLength(1);
  await page.getByRole('button', { name: /提示详情/ }).click();
  await page.getByRole('button', { name: '核对变更', exact: true }).click();
  await review.getByRole('button', { name: '保留我的输入' }).click();
  await expect.poll(() => state.patches.length).toBe(2);
  expect(state.patches[1]).toMatchObject({ settings_version: '9007199254740994', custom_instructions: '我的创作偏好' });
});

test('账号记忆自动保存，关闭重开保留失败草稿，删除使用当前版本', async ({ page }) => {
  await settingsFixture(page);
  const item: MemoryItem = {
    id: '801', kind: 'preference', memory_key: 'dialogue-style', title: '对白偏好', content: '对白保持简短',
    aliases: [], tags: [], pinned: false, state: 'active', origin: 'explicit', content_version: '9007199254740993',
    created_at: '2026-11-04T00:00:00Z', updated_at: '2026-11-04T00:00:00Z',
  };
  let failNext = false;
  const writes: Record<string, unknown>[] = [];
  await page.route('**/api/v1/assistant/memory**', route => {
    const request = route.request();
    const body = request.postDataJSON() ?? {};
    if (request.method() === 'PATCH') {
      writes.push(body);
      if (failNext) {
        failNext = false;
        return route.fulfill({ status: 503, json: { error: { code: 'unavailable', message: '暂时无法同步' } } });
      }
      Object.assign(item, body, { content_version: String(BigInt(item.content_version) + 1n) });
    }
    if (request.method() === 'DELETE') {
      expect(body).toEqual({ content_version: item.content_version });
      item.state = 'forgotten';
    }
    return route.fulfill({ json: new URL(request.url()).pathname.endsWith('/801') ? item : {
      items: item.state === 'active' ? [item] : [], total: item.state === 'active' ? 1 : 0, offset: 0, limit: 20,
    } });
  });
  await page.goto('/settings/assistant/memory');
  await page.getByRole('button', { name: '管理记忆', exact: true }).click();
  await page.getByRole('button', { name: '编辑记忆：对白偏好', exact: true }).click();
  const editor = page.getByRole('dialog', { name: '编辑记忆', exact: true });
  await editor.getByRole('textbox', { name: '记忆内容' }).fill('每句对白尽量不超过二十字');
  await expect.poll(() => item.content).toBe('每句对白尽量不超过二十字');
  expect(writes[0]).toMatchObject({ content_version: '9007199254740993' });
  failNext = true;
  await editor.getByRole('textbox', { name: '记忆内容' }).fill('让人物用行动表达情绪');
  await expect(editor.getByRole('button', { name: /提示详情/ })).toBeVisible();
  await editor.getByRole('button', { name: '关闭弹窗' }).click();
  await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '项目管理', exact: true }).click();
  await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '配置', exact: true }).click();
  await page.getByRole('navigation', { name: '配置导航' }).getByRole('link', { name: '长期记忆', exact: true }).click();
  await page.getByRole('button', { name: '管理记忆', exact: true }).click();
  await expect(editor.getByRole('textbox', { name: '记忆内容' })).toHaveValue('让人物用行动表达情绪');
  expect(item.content).toBe('每句对白尽量不超过二十字');
  await editor.getByRole('button', { name: /提示详情/ }).click();
  await editor.getByRole('button', { name: '重新核对', exact: true }).click();
  await expect.poll(() => item.content).toBe('让人物用行动表达情绪');
  await editor.getByRole('button', { name: '返回记忆', exact: true }).click();
  const manager = page.getByRole('dialog', { name: '管理记忆', exact: true });
  await manager.getByRole('button', { name: '删除记忆：对白偏好', exact: true }).click();
  await page.getByRole('dialog', { name: '删除记忆', exact: true }).getByRole('button', { name: '删除', exact: true }).click();
  await expect(manager.getByText('还没有长期记忆', { exact: true })).toBeVisible();
  await expect(manager.getByRole('button', { name: '保存', exact: true })).toHaveCount(0);
});
