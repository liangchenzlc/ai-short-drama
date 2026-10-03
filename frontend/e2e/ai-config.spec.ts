import { expect, test, type Page } from '@playwright/test';
import { fixture } from './studio-fixture';

async function configsFixture(page: Page) {
  const base = await fixture(page, true);
  const config = {
    id: '78', name: '剧本模型', model_key: 'fixture-text', service_type: 'text',
    provider: '测试替身', base_url: 'https://fixture.invalid/v1', enabled: 1, is_default: 0,
    is_deleted: 0, row_version: '9007199254740993', has_api_key: true,
  };
  const state = { config, creates: 0, updates: [] as Record<string, unknown>[], defaults: [] as Record<string, unknown>[], defaultFailures: 0 };
  await page.route('**/api/v1/ai-model-configs**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.slice('/api/v1'.length);
    const body = request.postDataJSON() ?? {};
    const reply = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path === '/ai-model-configs' && request.method() === 'GET') return reply({ items: [state.config], total: 1, offset: 0, limit: 20 });
    if (path === '/ai-model-configs' && request.method() === 'POST') {
      state.creates += 1;
      Object.assign(state.config, body, { row_version: '9007199254740994' });
      return reply(state.config, 201);
    }
    if (path === '/ai-model-configs/78' && request.method() === 'GET') return reply(state.config);
    if (path === '/ai-model-configs/78' && request.method() === 'PATCH') {
      state.updates.push(body);
      Object.assign(state.config, body, { row_version: String(BigInt(state.config.row_version) + 1n) });
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
