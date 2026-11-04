import { expect, test, type Page } from '@playwright/test';
import type { McpAccessToken, McpTokenCreate } from '../../src/types/assistant/settings/mcp';
import { fixture } from '../support/studio-fixture';

async function mcpFixture(page: Page, loseCreateResponse = false) {
  const base = await fixture(page, true);
  const read = ['list_projects', 'read_project', 'read_assets', 'read_storyboard', 'read_current_script', 'read_image_group', 'read_image_content', 'get_generation_status'].map(name => `workbench__${name}`);
  const workbench = ['apply_standard_patch', 'apply_canvas_patch', 'set_image_group_primary'].map(name => `workbench__${name}`);
  const media = ['workbench__submit_generation_batch'];
  const items: McpAccessToken[] = [{
    id: '9007199254740993', name: '过期令牌', token_hint: 'sdmcp_…old', create_key: 'existing-token',
    tool_names: read, expires_at: '2020-01-01T00:00:00Z', allowed_ips: [],
    created_at: '2019-10-01T00:00:00Z', deleted_at: null,
  }];
  const creates: { body: McpTokenCreate; key: string }[] = [];
  const checks: string[] = [];
  await page.route('**/api/v1/assistant/settings', route => route.fulfill({ json: {
    custom_instructions: null, default_instructions: '默认指令', workbench_enabled: true,
    media_generation_enabled: true, long_term_memory_enabled: true, settings_version: '0',
  } }));
  await page.route('**/api/v1/account/mcp-tokens**', route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.endsWith('/options')) return route.fulfill({ json: {
      connection_url: 'https://studio.example/mcp/', transport: 'streamable-http',
      capability_groups: { read, workbench, media },
      tools: [...read, ...workbench, ...media].map(name => ({ name, description: name, modes: ['standard'], mutating: !read.includes(name) })),
    } });
    if (request.method() === 'GET') {
      const key = url.searchParams.get('create_key');
      if (key) checks.push(key);
      return route.fulfill({ json: items.filter(item => key ? item.create_key === key : !item.deleted_at) });
    }
    if (request.method() === 'POST') {
      const body: McpTokenCreate = request.postDataJSON(), key = request.headers()['idempotency-key'];
      creates.push({ body, key });
      const row: McpAccessToken = {
        id: String(9000 + items.length), name: body.name, create_key: key, token_hint: 'sdmcp_…new',
        tool_names: [...read, ...(body.workbench_enabled ? workbench : []), ...(body.media_generation_enabled ? media : [])],
        expires_at: body.expires_at ?? '2027-02-01T00:00:00Z', allowed_ips: body.allowed_ips,
        created_at: '2026-11-04T00:00:00Z', deleted_at: null,
      };
      items.push(row);
      return loseCreateResponse ? route.abort('failed') : route.fulfill({ status: 201, json: { ...row, secret: 'fixture-once-secret' } });
    }
    const token = items.find(item => item.id === url.pathname.split('/').at(-1));
    if (token) token.deleted_at = '2026-11-04T01:00:00Z';
    return route.fulfill({ status: 204 });
  });
  return { ...base, creates, checks, items };
}

for (const viewport of [{ width: 1280, height: 720 }, { width: 1440, height: 900 }]) {
  test(`MCP settings use six columns, capability hierarchy and one-time credentials at ${viewport.width}`, async ({ page }, info) => {
    await page.setViewportSize(viewport);
    const data = await mcpFixture(page);
    await page.goto('/settings/mcp');
    await expect(page.getByRole('columnheader')).toHaveText(['名称', 'MCP 令牌', '授权范围', '有效期', '状态', '操作']);
    await expect(page.getByRole('row').filter({ hasText: '过期令牌' })).toContainText('已过期');
    await page.getByRole('button', { name: '创建令牌', exact: true }).click();
    const form = page.getByRole('dialog', { name: '创建 MCP 令牌', exact: true });
    await expect(form.getByText('授权项目', { exact: true })).toHaveCount(0);
    await expect(form.getByRole('switch', { name: '允许生成图片、视频、音频' })).toHaveCount(0);
    await form.getByRole('textbox', { name: '令牌名称', exact: true }).fill('桌面客户端');
    await form.getByRole('switch', { name: '工作台操作', exact: true }).click();
    await form.getByRole('switch', { name: '允许生成图片、视频、音频', exact: true }).click();
    await form.getByRole('button', { name: '创建令牌', exact: true }).click();
    const secret = page.getByRole('dialog', { name: '令牌已创建', exact: true });
    await expect(secret.getByRole('textbox', { name: '完整 MCP 密钥' })).toHaveValue('fixture-once-secret');
    expect(data.creates[0].body).toEqual({ name: '桌面客户端', workbench_enabled: true, media_generation_enabled: true, allowed_ips: [] });
    expect(data.creates[0].key).toBeTruthy();
    await secret.getByRole('button', { name: '完成', exact: true }).click();
    await expect(page.locator('body')).not.toContainText('fixture-once-secret');
    await page.getByRole('button', { name: '查看授权：桌面客户端', exact: true }).click();
    await expect(page.getByRole('dialog', { name: '令牌授权', exact: true })).not.toContainText('fixture-once-secret');
    await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath(`mcp-settings-${viewport.width}.png`), fullPage: true });
    await page.getByRole('button', { name: '撤销令牌：桌面客户端', exact: true }).click();
    await page.getByRole('dialog', { name: '撤销令牌', exact: true }).getByRole('button', { name: '撤销令牌', exact: true }).click();
    await expect(page.getByRole('row').filter({ hasText: '桌面客户端' })).toHaveCount(0);
  });
}

test('MCP unknown creation checks its original key before showing an explicitly revocable record', async ({ page }) => {
  const data = await mcpFixture(page, true);
  await page.goto('/settings/mcp');
  await page.getByRole('button', { name: '创建令牌', exact: true }).click();
  const form = page.getByRole('dialog', { name: '创建 MCP 令牌', exact: true });
  await form.getByRole('textbox', { name: '令牌名称', exact: true }).fill('核对客户端');
  await form.getByRole('button', { name: '创建令牌', exact: true }).click();
  await expect(form.getByRole('textbox', { name: '令牌名称', exact: true })).toBeDisabled();
  await form.getByRole('button', { name: '核对创建结果', exact: true }).click();
  const lost = page.getByRole('dialog', { name: '未取得完整密钥', exact: true });
  await expect(lost).toContainText('本次令牌已创建');
  expect(data.creates).toHaveLength(1);
  expect(data.checks).toEqual([data.creates[0].key]);
  await lost.getByRole('button', { name: '关闭', exact: true }).click();
  await expect(page.getByRole('row').filter({ hasText: '核对客户端' })).toBeVisible();
});

test('MCP empty and error states recover, and denied clipboard access leaves the secret selectable', async ({ page }) => {
  const data = await mcpFixture(page);
  data.items.length = 0;
  let unavailable = true;
  await page.route('**/api/v1/account/mcp-tokens', route => unavailable
    ? route.fulfill({ status: 503, json: { error: { code: 'database_unavailable', message: '暂时无法读取令牌' } } })
    : route.fallback());
  await page.goto('/settings/mcp');
  await expect(page.getByRole('alert').filter({ hasText: '令牌读取失败' })).toBeVisible();
  unavailable = false;
  await page.getByRole('button', { name: '重新读取', exact: true }).click();
  await expect(page.getByText('还没有 MCP 令牌', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '创建令牌', exact: true }).click();
  const form = page.getByRole('dialog', { name: '创建 MCP 令牌', exact: true });
  await form.getByRole('textbox', { name: '令牌名称', exact: true }).fill('只读客户端');
  await form.getByRole('button', { name: '创建令牌', exact: true }).click();
  expect(data.creates[0].body.workbench_enabled).toBe(false);
  expect(data.creates[0].body.media_generation_enabled).toBe(false);
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
      writeText: async () => { throw new Error('Clipboard permission denied'); },
    } });
  });
  const secret = page.getByRole('dialog', { name: '令牌已创建', exact: true });
  await secret.getByRole('button', { name: '复制密钥', exact: true }).click();
  const input = secret.getByRole('textbox', { name: '完整 MCP 密钥', exact: true });
  await expect(input).toBeFocused();
  expect(await input.evaluate(node => {
    const field = node as HTMLInputElement;
    return field.value.slice(field.selectionStart ?? 0, field.selectionEnd ?? 0);
  })).toBe('fixture-once-secret');
  await secret.getByRole('button', { name: '完成', exact: true }).click();
});
