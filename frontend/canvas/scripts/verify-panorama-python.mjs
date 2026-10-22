import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}/my-document`;
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = { step: "start", width: config.width, page_errors: [], http_errors: [], resource_posts: 0, task_posts: 0, outputs: [] };
const server = await createServer({ cacheDir: resolve(".runtime/vite-panorama"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let owner;
let page;
const digest = bytes => createHash("sha256").update(bytes).digest("hex");
const card = id => page.locator(`[data-node-id="${id}"]`);
async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function eventually(readValue, predicate, label) {
    let value;
    for (let attempt = 0; attempt < 300; attempt++) {
        value = await readValue();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`${label}: ${JSON.stringify(value)}`);
}
async function graph(predicate = () => true, user = owner) {
    return eventually(async () => (await read(await user.request.get(origin + path))).source_document, predicate, "全景作品没有保存到预期状态");
}
async function context(username, cookies) {
    const value = await browser.newContext({ viewport: { width: config.width, height: 900 }, ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    value.setDefaultTimeout(20000);
    value.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await value.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await value.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await value.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return value;
}
async function open(user) {
    page = await user.newPage();
    page.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    page.on("request", request => {
        const url = new URL(request.url()).pathname;
        if (request.method() === "POST" && url === "/api/v1/canvas-runtime/resources") report.resource_posts++;
        if (request.method() === "POST" && url === "/api/v1/canvas-runtime/tasks") report.task_posts++;
    });
    page.on("response", response => {
        const url = new URL(response.url()).pathname;
        if (url.startsWith("/api/") && response.status() >= 400) report.http_errors.push({ path: url, status: response.status() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    const focus = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focus.isVisible()) await focus.getByRole("button", { name: "退出专注模式", exact: true }).press("Control+0");
    else await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
}
async function readyCanvas(scope) {
    const canvas = scope.locator("canvas");
    await canvas.waitFor({ state: "visible", timeout: 60000 });
    await scope.getByText("全景加载中...", { exact: true }).waitFor({ state: "hidden" });
    assert.equal(await canvas.count(), 1);
    await eventually(() => canvas.evaluate(element => element.width > 100 && element.height > 100), Boolean, "全景WebGL尺寸未就绪");
    return canvas;
}
async function pixels(canvas) {
    return digest(await canvas.evaluate(element => element.toDataURL("image/png")));
}
async function resource(node, filename) {
    assert.match(node.metadata.storageKey, /^resource:\d+$/);
    const id = node.metadata.storageKey.slice("resource:".length);
    const response = await owner.request.get(`${origin}/api/v1/canvas-runtime/resources/${id}/file`);
    assert.ok(response.ok());
    const bytes = await response.body();
    await writeFile(resolve(output, filename), bytes);
    return { node_id: node.id, resource_id: id, checksum: digest(bytes), filename, asset_id: node.metadata.assetId, width: node.metadata.naturalWidth, height: node.metadata.naturalHeight };
}
async function checkOutputs(label) {
    for (const item of report.outputs) {
        const image = card(item.node_id).getByRole("img").first();
        await image.waitFor({ state: "visible" });
        await eventually(() => image.evaluate(element => element.complete && element.naturalWidth > 0), Boolean, `${label}全景导出没有显示`);
        const data = await image.evaluate(async element => {
            const response = await fetch(element.currentSrc);
            if (!response.ok) throw new Error(`全景结果读取失败：${response.status}`);
            return Array.from(new Uint8Array(await response.arrayBuffer()));
        });
        assert.equal(digest(Uint8Array.from(data)), item.checksum);
    }
}
try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    owner = await context(config.username);
    await open(owner);
    report.step = "原图片工具创建全景查看器";
    await card("panorama-source").click();
    await card("panorama-source").hover();
    const tools = page.getByRole("toolbar", { name: "节点快捷工具", exact: true });
    await tools.waitFor({ state: "visible" });
    await tools.getByRole("button", { name: /^(图片工具|工具)$/ }).click();
    if (config.width >= 640) {
        assert.equal(await page.getByRole("menuitem", { name: "全景图", exact: true }).count(), 0, "保留固定源宽屏隐藏入口");
        await tools.getByRole("button", { name: "图片工具", exact: true }).click();
        await page.setViewportSize({ width: 600, height: 900 });
        await card("panorama-source").hover();
        await tools.getByRole("button", { name: "工具", exact: true }).click();
        report.desktop_creation_hidden_like_source = true;
    }
    await page.getByRole("menuitem", { name: "全景图", exact: true }).click();
    await page.getByRole("button", { name: "创建全景图", exact: true }).click();
    const created = await graph(document => document.nodes.length === 2 && document.nodes.some(node => node.type === "panorama") && document.connections.length === 1);
    report.panorama_node_id = created.nodes.find(node => node.type === "panorama").id;
    assert.equal(created.nodes.find(node => node.id === report.panorama_node_id).metadata.panoramaConfig.sourceMode, "image");
    await page.setViewportSize({ width: config.width, height: 900 });
    const panorama = () => card(report.panorama_node_id);
    await panorama().getByRole("button", { name: "进入环视", exact: true }).click();
    await readyCanvas(panorama());
    report.created_from_original_tool = true;

    report.step = "原全屏、键盘、拖拽与滚轮环视";
    await panorama().getByRole("button", { name: "全屏环视", exact: true }).click();
    const overlay = page.locator("[data-canvas-no-zoom]").filter({ has: page.getByRole("button", { name: "退出全屏", exact: true }) }).filter({ has: page.locator("canvas") });
    const canvas = await readyCanvas(overlay);
    assert.equal(await panorama().locator("canvas").count(), 0, "全屏只保留一个WebGL查看器");
    const before = await pixels(canvas);
    await canvas.locator("..").press("ArrowRight");
    await eventually(() => pixels(canvas), value => value !== before, "方向键没有改变全景视角");
    const keyboardView = await pixels(canvas);
    const bounds = await canvas.boundingBox();
    await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
    await page.mouse.down();
    await page.mouse.move(bounds.x + bounds.width / 2 + 100, bounds.y + bounds.height / 2 + 30, { steps: 8 });
    await page.mouse.up();
    await eventually(() => pixels(canvas), value => value !== keyboardView, "拖拽没有改变全景视角");
    const dragged = await pixels(canvas);
    const viewport = structuredClone((await graph()).viewport);
    await canvas.hover();
    await page.mouse.wheel(0, 120);
    await eventually(() => pixels(canvas), value => value !== dragged, "全屏滚轮没有改变视场角");
    assert.deepEqual((await graph()).viewport, viewport, "全屏缩放不得改变外层画布");
    await overlay.getByRole("button", { name: "重置视角", exact: true }).click();
    report.fullscreen_single_context = true;
    report.keyboard_drag_wheel = true;

    report.step = "真实当前视角和四向PNG永久导出";
    await overlay.getByRole("button", { name: "截图当前视角", exact: true }).click();
    const first = await graph(document => document.nodes.length === 3 && document.nodes.some(node => node.title.endsWith("当前视角") && node.metadata.assetId));
    report.outputs.push(await resource(first.nodes.find(node => node.title.endsWith("当前视角")), "panorama-view.png"));
    await overlay.getByRole("button", { name: "导出四向视图", exact: true }).click();
    const produced = await graph(document => document.nodes.length === 4 && document.nodes.some(node => node.title.endsWith("四向视图") && node.metadata.assetId));
    report.outputs.push(await resource(produced.nodes.find(node => node.title.endsWith("四向视图")), "panorama-quad.png"));
    await page.screenshot({ path: resolve(output, "panorama-fullscreen.png"), fullPage: true });
    await overlay.getByRole("button", { name: "退出全屏", exact: true }).first().click();
    await overlay.waitFor({ state: "hidden" });
    await readyCanvas(panorama());
    await panorama().getByRole("button", { name: "退出环视", exact: true }).click();
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await page.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
    const uploads = report.resource_posts;
    const saved = await graph();
    report.saved_outputs = saved.nodes.filter(node => report.outputs.some(item => item.node_id === node.id));
    assert.equal(uploads, 2);

    report.step = "关闭旧窗口仅cookies恢复";
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(undefined, cookies);
    await open(owner);
    const restored = await graph();
    assert.deepEqual(restored.nodes.filter(node => report.outputs.some(item => item.node_id === node.id)), report.saved_outputs);
    await checkOutputs("本人恢复");
    await panorama().getByRole("button", { name: "进入环视", exact: true }).click();
    await readyCanvas(panorama());
    await page.screenshot({ path: resolve(output, "panorama-restored.png"), fullPage: true });
    report.cookie_only_recovery = true;


    assert.equal(report.resource_posts, uploads, "恢复全景和结果不得重复上传");
    assert.equal(report.task_posts, 0);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    report.step = "complete";
    process.stdout.write(JSON.stringify(report));
} catch (error) {
    report.error = error.stack ?? String(error);
    if (page && !page.isClosed()) {
        await page.screenshot({ path: resolve(output, "failure.png"), fullPage: true }).catch(() => {});
        const content = await page.content().catch(() => undefined);
        if (content) await writeFile(resolve(output, "failure.html"), content).catch(() => {});
    }
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
