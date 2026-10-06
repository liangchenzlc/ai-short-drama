import assert from 'node:assert/strict';
import { chromium, expect } from '@playwright/test';
import { createServer } from 'vite';

// 配置经标准输入传入，避免把认证 Cookie 放进进程命令行。
let source = '';
for await (const chunk of process.stdin) source += chunk;
const config = JSON.parse(source);
let server;
let browser;
const errors = [];
const failures = [];
try {
  server = await createServer({
    logLevel: 'silent',
    clearScreen: false,
    server: {
      host: '127.0.0.1',
      port: config.frontendPort,
      strictPort: true,
      proxy: { '/api': { target: config.apiOrigin, changeOrigin: true } },
    },
  });
  await server.listen();
  browser = await chromium.launch({
    headless: true,
    ...(config.executablePath ? { executablePath: config.executablePath } : {}),
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addCookies([
    { name: 'sd_session', value: config.token, url: config.frontendOrigin, httpOnly: true, sameSite: 'Lax' },
    { name: 'sd_csrf', value: config.csrf, url: config.frontendOrigin, sameSite: 'Lax' },
  ]);
  const page = await context.newPage();
  page.setDefaultTimeout(45_000);
  const posts = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.startsWith('/api/')) {
      posts.push({ path: new URL(request.url()).pathname, body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
    }
  });
  page.on('response', response => {
    if (response.status() >= 400 && new URL(response.url()).pathname.startsWith('/api/')) {
      failures.push({ path: new URL(response.url()).pathname, status: response.status() });
    }
  });
  const parameters = new URLSearchParams({
    mode: 'agent',
    agent_subject_assets: config.targetId,
    [`conversation_assets:asset:${config.targetId}:creation`]: config.conversationId,
  });
  await page.goto(`${config.frontendOrigin}/projects/${config.projectId}/episodes/${config.episodeId}/assets?${parameters}`);
  const composer = page.getByRole('textbox', { name: '创作要求' });
  await expect(composer).toBeVisible();
  await composer.fill('请将当前人物的描述改得更冷峻，生成候选供我审核。');
  const sent = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === `/api/v1/agent/conversations/${config.conversationId}/messages`);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  const accepted = await sent;
  assert.equal(accepted.status(), 201, await accepted.text());
  const result = await accepted.json();
  assert.equal(result.run.mode, 'auto');
  await expect(composer).toHaveValue('');
  await expect(page.getByText('候选已经生成，请审核后采用。', { exact: true })).toBeVisible({ timeout: 45_000 });
  await page.locator('.agent-message-results').getByRole('button', { name: '核对候选', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '核对创作候选' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('region', { name: '候选修改比较' })).toContainText('更冷峻的人物');
  await expect(dialog.getByRole('button', { name: '确认采用', exact: true })).toBeEnabled();
  assert.equal(posts.filter(item => item.path.endsWith('/messages')).length, 1);
  const message = posts.find(item => item.path.endsWith('/messages'));
  assert.deepEqual(message.body.expected_scope, config.scope);
  assert.equal(message.body.model_config_id, config.modelId);
  assert.equal(message.body.mode, undefined);
  assert.equal(message.body.task, undefined);
  assert.ok(message.key);
  assert.ok(!posts.some(item => /\/(verify|adopt|continue)(?:\/|$)/.test(item.path)));
  assert.deepEqual(errors, []);
  assert.deepEqual(failures, []);
  process.stdout.write(JSON.stringify({ runId: result.run.id, sentMessages: 1, candidateVisible: true }));
} catch (error) {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.stderr.write(`${JSON.stringify({ pageErrors: errors, apiFailures: failures })}\n`);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  if (server) await server.close();
}
