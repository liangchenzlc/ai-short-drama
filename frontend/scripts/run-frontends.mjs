import { spawn } from 'node:child_process';
import { cp, stat } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const packages = [root, resolve(root, 'canvas')];

function packageRequire(directory) {
  return createRequire(resolve(directory, 'package.json'));
}

async function loadVite(directory) {
  return import(pathToFileURL(packageRequire(directory).resolve('vite')).href);
}

async function typecheck(directory) {
  const executable = packageRequire(directory).resolve('typescript/bin/tsc');
  await new Promise((resolveCheck, reject) => {
    const child = spawn(process.execPath, [executable, '--noEmit'], {
      cwd: directory, stdio: 'inherit', windowsHide: true,
    });
    child.once('error', reject);
    child.once('exit', (code, signal) => {
      if (code === 0) resolveCheck();
      else reject(new Error(`${directory}: 类型检查失败（${signal ?? code}）`));
    });
  });
}

async function buildFrontends() {
  for (const directory of packages) {
    await typecheck(directory);
    const vite = await loadVite(directory);
    await vite.build({ root: directory });
  }
  const output = resolve(root, 'canvas/dist');
  // 两包构建均成功后才交付画布产物；宿主 Vite 已清空本次 dist。
  await stat(resolve(output, 'index.html'));
  await cp(output, resolve(root, 'dist/canvas-app'), { recursive: true });
  process.stdout.write('标准模式和画布构建完成：dist/、dist/canvas-app/（保留 canvas/dist/）。\n');
}

async function startFrontends(args) {
  const { values } = parseArgs({
    args,
    options: {
      host: { type: 'string', default: '127.0.0.1' },
      port: { type: 'string' },
      mode: { type: 'string' },
      open: { type: 'boolean' },
      force: { type: 'boolean' },
      strictPort: { type: 'boolean' },
    },
  });
  const hostPort = values.port === undefined ? undefined : Number(values.port);
  if (hostPort !== undefined && (!Number.isInteger(hostPort) || hostPort < 1 || hostPort > 65535)) {
    throw new Error('--port 必须为 1–65535 的整数。');
  }
  const servers = [];
  let finish;
  const stopped = new Promise(resolveStop => { finish = resolveStop; });
  const stop = () => finish();
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  if (process.env.CI !== 'true') process.stdin.once('end', stop);
  try {
    for (const [index, directory] of packages.entries()) {
      const vite = await loadVite(directory);
      const previousSignals = process.listeners('SIGTERM');
      const previousInput = process.stdin.listeners('end');
      let server;
      try {
        server = await vite.createServer({
          root: directory,
          mode: values.mode,
          clearScreen: false,
          server: {
            host: values.host,
            ...(index === 0 && hostPort !== undefined ? { port: hostPort } : {}),
            ...(index === 0 && values.open ? { open: true } : {}),
            strictPort: true,
          },
          optimizeDeps: values.force ? { force: true } : undefined,
        });
      } finally {
        // Vite 单服务器的退出处理会直接退出进程；统一入口负责等两台都关闭。
        for (const listener of process.listeners('SIGTERM')) {
          if (!previousSignals.includes(listener)) process.removeListener('SIGTERM', listener);
        }
        for (const listener of process.stdin.listeners('end')) {
          if (!previousInput.includes(listener)) process.stdin.removeListener('end', listener);
        }
      }
      servers.push(server);
      await server.listen();
      process.stdout.write(index === 0 ? '\n标准模式：\n' : '\n无限画布模式：\n');
      server.printUrls();
    }
    await stopped;
  } finally {
    process.removeListener('SIGINT', stop);
    process.removeListener('SIGTERM', stop);
    process.stdin.removeListener('end', stop);
    const results = await Promise.allSettled(servers.map(server => server.close()));
    for (const result of results) if (result.status === 'rejected') {
      process.stderr.write(`关闭前端服务失败：${result.reason}\n`);
      process.exitCode = 1;
    }
  }
}

try {
  const [command, ...args] = process.argv.slice(2);
  if (command === 'dev') await startFrontends(args);
  else if (command === 'build' && args.length === 0) await buildFrontends();
  else throw new Error('用法：node scripts/run-frontends.mjs dev [--port 8080] 或 build');
} catch (error) {
  process.stderr.write(`${error.stack ?? error}\n`);
  process.exitCode = 1;
}
