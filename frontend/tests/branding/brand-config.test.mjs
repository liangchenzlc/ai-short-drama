import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer as createNetServer } from 'node:net';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { build, createServer, preview } from 'vite';

async function fixture(t, change = () => {}) {
  const fixtureRoot = fileURLToPath(new URL('../../../.runtime/miniart-brand/fixtures/', import.meta.url));
  await mkdir(fixtureRoot, { recursive: true });
  const brandRoot = await mkdtemp(resolve(fixtureRoot, 'brand-'));
  t.after(() => rm(brandRoot, { recursive: true, force: true }));
  const root = resolve(brandRoot, 'app');
  await mkdir(root);
  await mkdir(resolve(brandRoot, 'assets'));
  await writeFile(resolve(brandRoot, 'assets/mark.svg'), '<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 2"/></svg>');
  await writeFile(resolve(brandRoot, 'assets/logo.svg'), '<svg xmlns="http://www.w3.org/2000/svg"><text>Brand</text></svg>');
  await writeFile(resolve(brandRoot, 'assets/icon.png'), Buffer.from('89504e470d0a1a0a', 'hex'));
  const config = {
    name: '  Next & <Art> "Studio"  ', tagline: '  {name} 创作空间  ',
    titles: { app: '{name}', canvas: '画布 · {name}' },
    descriptions: { app: '{name} 的故事', canvas: '{name} 的画布' },
    assets: { mark: 'assets/mark.svg', lockup: 'assets/logo.svg', icon: 'assets/icon.png' },
  };
  change(config);
  const path = resolve(brandRoot, 'brand.json');
  await writeFile(path, JSON.stringify(config));
  await writeFile(resolve(root, 'index.html'), '<!doctype html><html><head></head><body><script type="module" src="/main.js"></script></body></html>');
  await writeFile(resolve(root, 'main.js'), "import { brand } from 'virtual:brand'; globalThis.brandFixture = brand;");
  return { root, brandRoot, path, config };
}

async function implementation() {
  return import('../../branding/vite-plugin.ts');
}

async function availablePort() {
  const server = createNetServer();
  await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen));
  const port = server.address().port;
  await new Promise(resolveClose => server.close(resolveClose));
  return port;
}

test('唯一配置改名后展开标题与描述模板，保留字面标语及资源相对路径', async t => {
  const f = await fixture(t);
  const { loadBrandConfig } = await implementation();
  const result = loadBrandConfig(f.path);
  assert.equal(result.name, 'Next & <Art> "Studio"');
  assert.equal(result.tagline, '{name} 创作空间');
  assert.equal(result.titles.canvas, '画布 · Next & <Art> "Studio"');
  assert.equal(result.descriptions.app, 'Next & <Art> "Studio" 的故事');
  assert.equal(result.assets.icon, 'assets/icon.png');
});

test('名称里的美元符号在标题和描述中按字面替换，不解释替换字符串语法', async t => {
  const f = await fixture(t, config => { config.name = "Cash $$ $& $` $' Studio"; });
  const { loadBrandConfig } = await implementation();
  const result = loadBrandConfig(f.path);
  assert.equal(result.titles.app, "Cash $$ $& $` $' Studio");
  assert.equal(result.titles.canvas, "画布 · Cash $$ $& $` $' Studio");
  assert.equal(result.descriptions.app, "Cash $$ $& $` $' Studio 的故事");
});

for (const [label, change] of [
  ['缺少字段', config => { delete config.tagline; }],
  ['空白文本', config => { config.name = '  '; }],
  ['未知顶层字段', config => { config.extra = 'other'; }],
  ['未知嵌套字段', config => { config.titles.other = 'other'; }],
  ['非字符串', config => { config.descriptions.app = 3; }],
  ['错误嵌套结构', config => { config.titles = null; }],
  ['未知模板', config => { config.titles.app = '{unknown}'; }],
  ['绝对资源路径', config => { config.assets.icon = '/icon.png'; }],
  ['越出配置目录', config => { config.assets.icon = '../icon.png'; }],
  ['资源反斜杠', config => { config.assets.icon = 'assets\\icon.png'; }],
  ['资源点路径', config => { config.assets.icon = 'assets/./icon.png'; }],
  ['资源空路径段', config => { config.assets.icon = 'assets//icon.png'; }],
  ['资源父路径段', config => { config.assets.icon = 'assets/../assets/icon.png'; }],
  ['资源冒号', config => { config.assets.icon = 'assets/icon:one.png'; }],
  ['图片不存在', config => { config.assets.icon = 'assets/missing.png'; }],
  ['不支持的图片类型', config => { config.assets.icon = 'brand.json'; }],
]) {
  test(`无效配置启动即报错：${label}`, async t => {
    const f = await fixture(t, change);
    const { loadBrandConfig } = await implementation();
    assert.throws(() => loadBrandConfig(f.path), /brand|品牌/i);
  });
}

for (const [surface, base, assetsDir, title] of [
  ['app', '/', 'assets', 'Next &amp; &lt;Art&gt; &quot;Studio&quot;'],
  ['canvas', '/canvas-app/', 'static', '画布 · Next &amp; &lt;Art&gt; &quot;Studio&quot;'],
]) {
  test(`${surface} dev 的首屏、虚拟模块与三张图片使用当前 base、内容 hash 和正确 MIME`, async t => {
    const f = await fixture(t);
    const { brandPlugin } = await implementation();
    const server = await createServer({
      configFile: false, root: f.root, base, publicDir: false, logLevel: 'silent',
      plugins: [brandPlugin(surface, f.path)],
      server: { host: '127.0.0.1', port: await availablePort(), strictPort: true, fs: { allow: [f.root] } },
      build: { assetsDir },
    });
    t.after(() => server.close());
    await server.listen();
    const origin = server.resolvedUrls.local[0];
    const html = await (await fetch(origin)).text();
    assert.ok(html.includes(`<title>${title}</title>`));
    assert.ok(html.includes('content="Next &amp; &lt;Art&gt; &quot;Studio&quot; 的'));
    assert.match(html, /rel="icon"[^>]*type="image\/png"/);
    assert.doesNotMatch(html, /<title>[^<]*<Art>/);
    const module = await fetch(new URL(`${base}@id/__x00__virtual:brand`, origin));
    assert.equal(module.status, 200);
    const { brand } = await import(`data:text/javascript,${encodeURIComponent(await module.text())}`);
    for (const [key, mime] of [['mark', 'image/svg+xml'], ['lockup', 'image/svg+xml'], ['icon', 'image/png']]) {
      assert.ok(brand.assets[key].startsWith(`${base}${assetsDir}/branding/`));
      assert.match(brand.assets[key], /-[a-f0-9]{12}\.(svg|png)$/);
      const response = await fetch(new URL(brand.assets[key], origin));
      assert.equal(response.status, 200);
      assert.match(response.headers.get('content-type'), new RegExp(mime.replace('+', '\\+')));
      assert.deepEqual(Buffer.from(await response.arrayBuffer()), await readFile(resolve(f.brandRoot, f.config.assets[key])));
      const head = await fetch(new URL(brand.assets[key], origin), { method: 'HEAD' });
      assert.equal(head.status, 200);
      assert.equal(await head.text(), '');
    }
    assert.ok(html.includes(`href="${brand.assets.icon}"`));
    const exposed = await fetch(new URL(`${base}@fs/${f.path.replaceAll('\\', '/')}`, origin));
    assert.equal(exposed.status, 403, `品牌插件不能将配置目录加入 Vite fs.allow：${JSON.stringify(server.config.server.fs.allow)}`);
  });

  test(`${surface} build 使用 emitFile 输出三张资源，HTML 与运行时 URL 保持一致`, async t => {
    const f = await fixture(t);
    const { brandPlugin } = await implementation();
    const result = await build({
      configFile: false, root: f.root, base, publicDir: false, logLevel: 'silent',
      plugins: [brandPlugin(surface, f.path)],
      build: { minify: false, modulePreload: false, assetsDir },
    });
    const html = String(result.output.find(file => file.fileName === 'index.html').source);
    assert.ok(html.includes(`<title>${title}</title>`));
    assert.match(html, /rel="icon"[^>]*type="image\/png"/);
    const sandbox = {};
    const javascript = result.output.find(file => file.type === 'chunk').code;
    runInNewContext(javascript, sandbox);
    const brand = sandbox.brandFixture;
    assert.equal(brand.titles.app, 'Next & <Art> "Studio"');
    const server = await preview({
      configFile: false, root: f.root, base, logLevel: 'silent',
      preview: { host: '127.0.0.1', port: await availablePort(), strictPort: true },
    });
    t.after(() => new Promise((resolveClose, reject) => server.httpServer.close(error => error ? reject(error) : resolveClose())));
    for (const key of ['mark', 'lockup', 'icon']) {
      assert.ok(brand.assets[key].startsWith(`${base}${assetsDir}/branding/`));
      const file = result.output.find(file => `${base}${file.fileName}` === brand.assets[key]);
      assert.ok(file, `缺少 ${key} 输出`);
      assert.deepEqual(Buffer.from(file.source), await readFile(resolve(f.brandRoot, f.config.assets[key])));
      const response = await fetch(new URL(brand.assets[key], server.resolvedUrls.local[0]));
      assert.equal(response.status, 200);
      assert.match(response.headers.get('content-type'), key === 'icon' ? /image\/png/ : /image\/svg\+xml/);
      assert.deepEqual(Buffer.from(await response.arrayBuffer()), Buffer.from(file.source));
    }
    assert.ok(html.includes(`href="${brand.assets.icon}"`));
  });

  for (const fileName of ['图标.svg', 'my icon.svg', 'icon#new.svg']) {
    for (const mode of ['dev', 'build/preview']) {
      test(`${surface} ${mode} 支持资源文件名 ${fileName}`, async t => {
        const f = await fixture(t, config => {
          config.assets.icon = `assets/${fileName}`;
          config.name = "Cash $$ $& $` $' Studio";
        });
        const svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>Custom icon</text></svg>';
        await writeFile(resolve(f.brandRoot, f.config.assets.icon), svg);
        const { brandPlugin } = await implementation();
        const options = {
          configFile: false, root: f.root, base, publicDir: false, logLevel: 'silent',
          plugins: [brandPlugin(surface, f.path)],
          build: { minify: false, modulePreload: false, assetsDir },
        };
        let brand;
        let origin;
        let html;
        if (mode === 'dev') {
          const server = await createServer({
            ...options,
            server: { host: '127.0.0.1', port: await availablePort(), strictPort: true, fs: { allow: [f.root] } },
          });
          t.after(() => server.close());
          await server.listen();
          origin = server.resolvedUrls.local[0];
          const module = await fetch(new URL(`${base}@id/__x00__virtual:brand`, origin));
          ({ brand } = await import(`data:text/javascript,${encodeURIComponent(await module.text())}`));
          html = await (await fetch(origin)).text();
        } else {
          const result = await build(options);
          const sandbox = {};
          runInNewContext(result.output.find(file => file.type === 'chunk').code, sandbox);
          brand = sandbox.brandFixture;
          const emitted = result.output.find(file => file.type === 'asset' && new RegExp(`^${assetsDir}/branding/icon-[a-f0-9]{12}\\.svg$`).test(file.fileName));
          assert.ok(emitted, 'emitFile 使用资源角色与内容 hash 命名，不依赖源文件名');
          const server = await preview({
            configFile: false, root: f.root, base, logLevel: 'silent',
            preview: { host: '127.0.0.1', port: await availablePort(), strictPort: true },
          });
          t.after(() => new Promise((resolveClose, reject) => server.httpServer.close(error => error ? reject(error) : resolveClose())));
          origin = server.resolvedUrls.local[0];
          html = await (await fetch(origin)).text();
        }
        const response = await fetch(new URL(brand.assets.icon, origin));
        assert.equal(response.status, 200);
        assert.match(response.headers.get('content-type'), /image\/svg\+xml/, '图片 URL 必须返回 SVG，而非 SPA HTML');
        assert.equal(await response.text(), svg);
        assert.match(brand.assets.icon, new RegExp(`^${base}${assetsDir}/branding/icon-[a-f0-9]{12}\\.svg$`));
        assert.equal(new URL(brand.assets.icon, origin).hash, '');
        assert.ok(html.includes(`href="${brand.assets.icon}"`));
        const expectedTitle = surface === 'app' ? "Cash $$ $&amp; $` $&#39; Studio" : "画布 · Cash $$ $&amp; $` $&#39; Studio";
        assert.ok(html.includes(`<title>${expectedTitle}</title>`), 'HTML 注入必须保留美元符号文案');
        assert.equal((html.match(/<\/head>/g) ?? []).length, 1, 'HTML 注入不能重复插入匹配内容');
      });
    }
  }
}

test('资源内容变化时输出 URL 的 hash 随之改变', async t => {
  const f = await fixture(t);
  const { brandPlugin } = await implementation();
  async function outputMark() {
    const result = await build({
      configFile: false, root: f.root, publicDir: false, logLevel: 'silent',
      plugins: [brandPlugin('app', f.path)], build: { write: false, modulePreload: false },
    });
    return result.output.find(file => /mark-.*\.svg$/.test(file.fileName)).fileName;
  }
  const before = await outputMark();
  await writeFile(resolve(f.brandRoot, 'assets/mark.svg'), '<svg xmlns="http://www.w3.org/2000/svg"><circle r="2"/></svg>');
  assert.notEqual(await outputMark(), before);
});
