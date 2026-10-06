import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { createConnection } from 'node:net';
import { dirname, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(resolve(frontend, 'package.json'));
const runner = resolve(frontend, 'scripts/run-frontends.mjs');
const viteCli = resolve(dirname(require.resolve('vite/package.json')), 'bin/vite.js');
// Windows 不向子进程投递 Unix 信号，用 IPC 触发真实入口的信号处理与 finally 清理。
const signalHook = 'data:text/javascript,' + encodeURIComponent(
  "process.on('message', message => { if (message === 'runtime-test:stop') { process.disconnect(); if (!process.emit('SIGTERM')) process.exit(0); } }); process.channel?.unref();",
);

async function httpFixture() {
  const server = createServer((request, response) => {
    response.writeHead(202, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ url: request.url, marker: request.headers['x-runtime-test'] }));
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return {
    port: server.address().port,
    close: () => new Promise((resolveClose, reject) => server.close(error => error ? reject(error) : resolveClose())),
  };
}

async function availablePort() {
  const server = await httpFixture();
  const port = server.port;
  await server.close();
  return port;
}

function start(args, environment = {}) {
  const child = spawn(process.execPath, ['--import', signalHook, ...args], {
    cwd: frontend,
    env: { ...process.env, BROWSER: 'none', ...environment },
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
    windowsHide: true,
  });
  let output = '';
  let finished;
  for (const stream of [child.stdout, child.stderr]) {
    stream.on('data', chunk => { output = (output + chunk).slice(-32_768); });
  }
  const exited = new Promise(resolveExit => {
    child.once('error', error => { finished = { error }; resolveExit(finished); });
    child.once('exit', (code, signal) => { finished = { code, signal }; });
    child.once('close', (code, signal) => { finished ??= { code, signal }; resolveExit(finished); });
  });
  return {
    child, exited,
    get finished() { return finished; },
    get output() { return output; },
    async stop() {
      if (!finished && child.connected) child.send('runtime-test:stop');
      const result = await completeWithin(exited, 15_000);
      if (!result) {
        child.kill('SIGKILL');
        await exited;
        assert.fail(`前端退出超时，已终止本测试创建的进程。\n${output}`);
      }
      assert.equal(result.code, 0, `前端退出失败：${JSON.stringify(result)}\n${output}`);
    },
  };
}

async function completeWithin(promise, milliseconds) {
  let timer;
  try {
    return await Promise.race([promise, new Promise(resolveTimeout => {
      timer = setTimeout(() => resolveTimeout(null), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}

async function waitUntil(check, label, service) {
  const deadline = Date.now() + 90_000;
  let lastError;
  while (Date.now() < deadline) {
    if (service?.finished) assert.fail(`${label}前进程已退出。\n${service.output}`);
    try {
      if (await check()) return;
    } catch (error) { lastError = error; }
    await delay(100);
  }
  assert.fail(`${label}超时：${lastError?.message ?? '条件未满足'}\n${service?.output ?? ''}`);
}

async function response(url, accept = 'text/html') {
  const result = await fetch(url, { headers: { accept }, signal: AbortSignal.timeout(10_000) });
  assert.equal(result.status, 200, `${url} 返回 ${result.status}`);
  return result;
}

async function expectEntries(base) {
  const standard = await (await response(`${base}/projects`)).text();
  const canvas = await (await response(`${base}/canvas-app/`)).text();
  const deep = await (await response(`${base}/canvas-app/canvas/9007199254740993123?runtime=1`)).text();
  const dotted = await (await response(`${base}/canvas-app/canvas/source.key-1?runtime=1`, '*/*')).text();
  assert.match(standard, /<title>短剧工作台 · 网页版<\/title>/);
  assert.match(canvas, /infinite-canvas:theme_store/);
  assert.match(deep, /infinite-canvas:theme_store/);
  assert.match(dotted, /infinite-canvas:theme_store/);
  assert.doesNotMatch(canvas, /<title>短剧工作台 · 网页版<\/title>/);
  assert.doesNotMatch(deep, /<title>短剧工作台 · 网页版<\/title>/);
  assert.doesNotMatch(dotted, /<title>短剧工作台 · 网页版<\/title>/);
  return { standard, canvas, deep };
}

async function expectLogo(base) {
  const image = await response(`${base}/canvas-app/beef-logo.png`, 'image/png');
  assert.match(image.headers.get('content-type'), /image\/png/);
  assert.deepEqual(Buffer.from(await image.arrayBuffer()), await readFile(resolve(frontend, 'canvas/public/beef-logo.png')));
}

async function expectFont(base, stylesheets) {
  let fontUrl;
  for (const stylesheet of Array.isArray(stylesheets) ? stylesheets : [stylesheets]) {
    const stylesheetUrl = new URL(stylesheet, base);
    const css = await (await response(stylesheetUrl, 'text/css')).text();
    const font = css.match(/url\(["']?([^\s)"']+\.woff2)["']?\)/)?.[1];
    if (font) { fontUrl = new URL(font, stylesheetUrl); break; }
  }
  assert.ok(fontUrl, `画布样式 ${stylesheets} 未引用 WOFF2 字体`);
  assert.equal(fontUrl.pathname.startsWith('/canvas-app/'), true, `画布字体越出独立路径：${fontUrl}`);
  const bytes = Buffer.from(await (await response(fontUrl, 'font/woff2')).arrayBuffer());
  assert.equal(bytes.subarray(0, 4).toString(), 'wOF2', `${fontUrl} 未返回 WOFF2 字节`);
}

async function expectProxy(base) {
  const result = await fetch(`${base}/api/runtime-probe?workspace=canvas`, {
    headers: { 'X-Runtime-Test': 'isolated-api' }, signal: AbortSignal.timeout(10_000),
  });
  assert.equal(result.status, 202);
  assert.deepEqual(await result.json(), { url: '/api/runtime-probe?workspace=canvas', marker: 'isolated-api' });
}

async function expectClosed(port) {
  await waitUntil(() => new Promise(resolveCheck => {
    const socket = createConnection({ host: '127.0.0.1', port });
    socket.once('connect', () => { socket.destroy(); resolveCheck(false); });
    socket.once('error', error => { socket.destroy(); resolveCheck(error.code === 'ECONNREFUSED'); });
    socket.setTimeout(500, () => { socket.destroy(); resolveCheck(false); });
  }), `端口 ${port} 关闭`);
}

test('统一 dev 实际启动独立入口、宿主模块与资源，并在停止后释放两端口', { timeout: 120_000 }, async t => {
  const backend = await httpFixture();
  t.after(() => backend.close());
  const standardPort = await availablePort();
  let canvasPort = await availablePort();
  while (canvasPort === standardPort) canvasPort = await availablePort();
  const service = start([runner, 'dev', '--port', String(standardPort)], {
    CANVAS_DEV_PORT: String(canvasPort), API_PROXY_TARGET: `http://127.0.0.1:${backend.port}`,
  });
  t.after(() => service.finished ? undefined : service.stop());
  const base = `http://127.0.0.1:${standardPort}`;
  const canvasBase = `http://127.0.0.1:${canvasPort}`;
  await waitUntil(async () => (await fetch(`${canvasBase}/canvas-app/`, { signal: AbortSignal.timeout(1_000) })).ok, '两台 Vite 就绪', service);
  await expectEntries(base);
  assert.match(await (await response(`${canvasBase}/canvas-app/`)).text(), /infinite-canvas:theme_store/);
  const sessionModule = await (await response(`${base}/canvas-app/src/services/host-session.ts`, 'text/javascript')).text();
  const hostModule = sessionModule.match(/from\s+["']([^"']*\/src\/api\/http\.ts[^"']*)["']/)?.[1];
  assert.ok(hostModule, '画布 session 模块未解析到宿主 HTTP 客户端');
  assert.match(await (await response(new URL(hostModule, base), 'text/javascript')).text(), /class ApiError/);
  await expectLogo(base);
  await expectFont(base, '/canvas-app/node_modules/@fontsource-variable/inter/index.css');
  await expectProxy(base);
  await expectProxy(canvasBase);
  await service.stop();
  await expectClosed(standardPort);
  await expectClosed(canvasPort);
  assert.equal((await fetch(`http://127.0.0.1:${backend.port}/still-owned-by-test`)).status, 202);
});

test('画布端口被占用时统一入口关闭已启动标准服务器，保留占用端口的服务', { timeout: 120_000 }, async t => {
  const occupied = await httpFixture();
  t.after(() => occupied.close());
  const standardPort = await availablePort();
  const service = start([runner, 'dev', '--port', String(standardPort)], {
    CANVAS_DEV_PORT: String(occupied.port), API_PROXY_TARGET: `http://127.0.0.1:${occupied.port}`,
  });
  t.after(() => service.finished ? undefined : service.stop());
  const result = await completeWithin(service.exited, 90_000);
  assert.ok(result, `启动失败后未退出。\n${service.output}`);
  assert.equal(result.code, 1, service.output);
  assert.match(service.output, /already in use/);
  assert.match(service.output, /标准模式：/, '测试必须覆盖标准服务已成功启动后的画布失败');
  await expectClosed(standardPort);
  const survivor = await fetch(`http://127.0.0.1:${occupied.port}/independent-service`, { headers: { 'X-Runtime-Test': 'survived' } });
  assert.deepEqual(await survivor.json(), { url: '/independent-service', marker: 'survived' });
});

test('统一 preview 返回两包 HTML、画布 JS 与字体，并保留 API 代理', { timeout: 120_000 }, async t => {
  // 单独跳过仅用于先验证开发入口；完整 test:frontends 必须已有统一 build 产物。
  if (process.env.FRONTENDS_RUNTIME_SKIP_PREVIEW === '1') return t.skip('等待统一生产构建');
  await readFile(resolve(frontend, 'dist/canvas-app/index.html'));
  const backend = await httpFixture();
  t.after(() => backend.close());
  const port = await availablePort();
  const service = start([viteCli, 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    API_PROXY_TARGET: `http://127.0.0.1:${backend.port}`,
  });
  t.after(() => service.finished ? undefined : service.stop());
  const base = `http://127.0.0.1:${port}`;
  await waitUntil(async () => (await fetch(`${base}/`, { signal: AbortSignal.timeout(1_000) })).ok, '统一 preview 就绪', service);
  const { canvas } = await expectEntries(base);
  const redirect = await fetch(`${base}/canvas-app?runtime=1`, {
    redirect: 'manual', headers: { accept: '*/*' }, signal: AbortSignal.timeout(10_000),
  });
  assert.equal(redirect.status, 302);
  assert.equal(redirect.headers.get('location'), '/canvas-app/?runtime=1');
  assert.match(await (await response(new URL(redirect.headers.get('location'), base), '*/*')).text(), /infinite-canvas:theme_store/);
  const script = canvas.match(/<script\b[^>]*src="([^"]+)"/)?.[1];
  assert.ok(script?.startsWith('/canvas-app/static/'), '生产画布 HTML 未引用独立静态 JS');
  const javascript = await response(new URL(script, base), 'text/javascript');
  assert.match(javascript.headers.get('content-type'), /javascript/);
  assert.doesNotMatch(await javascript.text(), /^\s*<!doctype html>/i);
  const stylesheets = [...canvas.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"/g)].map(match => match[1]);
  assert.ok(stylesheets.length > 0 && stylesheets.every(stylesheet => stylesheet.startsWith('/canvas-app/static/')), '生产画布 HTML 未引用独立样式');
  await expectLogo(base);
  await expectFont(base, stylesheets);
  await expectProxy(base);
  await service.stop();
  await expectClosed(port);
});
