import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { preview } from "vite";

// 统一生产产物、原上传/连接点、本人工具开关与恢复；仅一次明确503故障注入。
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const tools = "/api/v1/canvas-runtime/plugins";
const tasks = "/api/v1/canvas-runtime/tasks";
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = { step: "start", protocol: config.protocol, page_errors: [], failed_routes: [], task_post_count: 0 };
const server = await preview({
    root: resolve(".."), configFile: resolve("../vite.config.ts"),
    preview: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } },
    logLevel: "silent",
});
let browser;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
function observe(target) {
    target.on("pageerror", error => report.page_errors.push(error.message));
    target.on("request", request => {
        if (request.method() === "POST" && new URL(request.url()).pathname === tasks) report.task_post_count++;
    });
    target.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (route.startsWith("/api/") && response.status() >= 400) report.failed_routes.push({ route, status: response.status() });
    });
}
async function login(username, cookies) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    context.setDefaultTimeout(25000);
    context.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return context;
}
async function graph(context, predicate) {
    let value;
    for (let i = 0; i < 150; i++) {
        value = (await read(await context.request.get(`${origin}${path}/my-document`))).source_document;
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`画布未达到预期：${JSON.stringify(value)}`);
}
async function openCanvas(target) {
    await target.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await target.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await target.evaluate(() => document.fonts.ready);
}
async function save(target) {
    await target.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await target.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
    await target.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await login(config.username);
    const member = await login(config.memberUsername);
    const outsider = await login(config.outsiderUsername);
    page = await owner.newPage();
    observe(page);
    await openCanvas(page);
    assert.equal((await read(await owner.request.get(`${origin}${tools}/status`))).states["ai-art-critique"].effectiveEnabled, false);
    report.step = "原上传与连接点创建图片目标";
    const original = Buffer.from(config.image, "base64");
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: "red-reference.png", mimeType: "image/png", buffer: original });
    const uploaded = await graph(owner, document => document.nodes.length === 1 && document.nodes[0].metadata.storageKey?.startsWith("resource:"));
    const source = uploaded.nodes[0];
    report.source_node_id = source.id;
    report.resource_id = source.metadata.storageKey.slice("resource:".length);
    report.image_sha256 = createHash("sha256").update(original).digest("hex");
    await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    const sourceNode = page.locator(`[data-node-id="${source.id}"]`);
    await sourceNode.click();
    await sourceNode.hover();
    await sourceNode.locator('[data-canvas-connection-rail="right"]').click();
    const create = page.locator("[data-connection-create-menu]");
    await create.getByRole("button", { name: "图片", exact: true }).click();
    const connected = await graph(owner, document => document.nodes.length === 2 && document.connections.length === 1);
    const target = connected.nodes.find(node => node.id !== source.id);
    assert.equal(target.type, "image");
    assert.deepEqual(connected.connections.map(edge => [edge.fromNodeId, edge.toNodeId]), [[source.id, target.id]]);
    report.target_node_id = target.id;
    await page.locator(`[data-node-id="${target.id}"]`).click({ position: { x: 20, y: 12 } });
    await page.getByRole("textbox", { name: "图片提示词", exact: true }).fill(config.originalPrompt);
    await save(page);
    const saved = await graph(owner, document => document.nodes.find(node => node.id === source.id).metadata.assetId && document.nodes.find(node => node.id === target.id).metadata.composerContent === config.originalPrompt);
    const savedSource = saved.nodes.find(node => node.id === source.id);
    assert.equal(await page.getByRole("button", { name: "润色提示词", exact: true }).count(), 0);
    assert.equal(report.task_post_count, 0);

    report.step = "原画布菜单进入宿主个人开关";
    await page.getByRole("button", { name: "打开画布菜单", exact: true }).click();
    await page.getByRole("menuitem", { name: "模型配置", exact: true }).click();
    await page.getByRole("heading", { name: "AI 配置", exact: true }).waitFor();
    assert.ok(new URL(page.url()).pathname === "/ai_config");
    let injected = false;
    const statusRoute = `${origin}${tools}/status`;
    await page.route(statusRoute, async route => {
        if (!injected) {
            injected = true;
            await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "tool_settings_fixture_unavailable", message: "工具状态读取暂时失败" } }) });
        } else await route.continue();
    });
    await page.locator(".config-editor-tools > summary").click();
    await page.getByRole("button", { name: "重新读取工具状态", exact: true }).waitFor();
    assert.equal(await page.getByRole("switch", { name: "提示词优化", exact: true }).count(), 0);
    await page.getByRole("button", { name: "重新读取工具状态", exact: true }).click();
    const toggle = page.getByRole("switch", { name: "AI 审美分析", exact: true });
    await toggle.waitFor();
    assert.equal(await toggle.getAttribute("aria-checked"), "false");
    await page.unroute(statusRoute);
    const activated = page.waitForResponse(response => new URL(response.url()).pathname === `${tools}/ai-art-critique/activation` && response.request().method() === "PUT");
    await toggle.focus();
    await toggle.press("Space");
    assert.equal((await read(await activated)).state.effectiveEnabled, true);
    await page.getByText("AI 审美分析已启用。", { exact: true }).waitFor();
    assert.equal(await toggle.getAttribute("aria-checked"), "true");
    assert.equal(report.task_post_count, 0);
    assert.equal((await read(await member.request.get(`${origin}${tools}/status`))).states["ai-art-critique"].effectiveEnabled, false);
    await page.screenshot({ path: resolve(output, "settings-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.screenshot({ path: resolve(output, "settings-desktop-1280.png"), fullPage: true });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole("button", { name: "返回画布", exact: true }).click();
    await page.locator("[data-canvas-viewport]").waitFor();

    report.step = "固定源隐藏的提示词标题栏保持隐藏";
    await page.locator(`[data-node-id="${target.id}"]`).click({ position: { x: 20, y: 12 } });
    assert.equal(await page.getByRole("textbox", { name: "图片提示词", exact: true }).textContent(), config.originalPrompt);
    assert.equal(await page.locator(".canvas-node-composer--libtv > .canvas-node-composer-header").evaluate(element => getComputedStyle(element).display), "none");
    assert.equal(await page.getByRole("button", { name: "润色提示词", exact: true }).count(), 0);
    const preserved = await graph(owner, document => document.nodes.find(node => node.id === target.id).metadata.composerContent === config.originalPrompt);
    assert.deepEqual(preserved.nodes.find(node => node.id === source.id), savedSource);
    assert.equal(report.task_post_count, 0);
    await save(page);

    report.step = "关闭旧窗口后仅cookies恢复";
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    const fresh = await login(undefined, cookies);
    page = await fresh.newPage();
    observe(page);
    await openCanvas(page);
    await page.locator(`[data-node-id="${target.id}"]`).click({ position: { x: 20, y: 12 } });
    assert.equal(await page.getByRole("textbox", { name: "图片提示词", exact: true }).textContent(), config.originalPrompt);
    assert.equal(await page.getByRole("button", { name: "润色提示词", exact: true }).count(), 0);
    const runtimeRead = page.waitForResponse(response => new URL(response.url()).pathname === `${tools}/status`);
    await page.reload();
    assert.equal((await read(await runtimeRead)).states["ai-art-critique"].effectiveEnabled, true);
    await page.locator("[data-canvas-viewport]").waitFor();
    await page.getByRole("button", { name: "添加节点", exact: true }).click();
    await page.getByRole("button", { name: "AI 审美批改", exact: true }).waitFor();
    report.art_critique_creation_reachable = true;
    await page.waitForFunction(id => {
        const image = document.querySelector(`[data-node-id="${id}"] img`);
        return image instanceof HTMLImageElement && image.complete && image.naturalWidth === 37 && image.naturalHeight === 19;
    }, source.id);
    assert.deepEqual(await (await fresh.request.get(`${origin}/api/v1/canvas-runtime/resources/${report.resource_id}/file`)).body(), original);
    assert.equal((await member.request.get(`${origin}${path}/my-document`)).status(), 404);
    const shared = await read(await fresh.request.get(`${origin}${path}`));
    assert.ok(!JSON.stringify(shared).includes(config.originalPrompt));
    assert.equal((await outsider.request.get(`${origin}${path}/my-document`)).status(), 404);
    assert.equal((await outsider.request.get(`${origin}/api/v1/canvas-runtime/resources/${report.resource_id}/file`)).status(), 404);
    await page.getByRole("button", { name: "添加节点", exact: true }).click();
    await save(page);
    await page.screenshot({ path: resolve(output, "restored.png") });
    assert.equal(report.task_post_count, 0);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.failed_routes, [{ route: `${tools}/status`, status: 503 }]);
    report.step = "complete";
} catch (error) {
    report.failure_stack = error.stack;
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    if (page) await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await new Promise(resolve => server.httpServer.close(resolve));
}
process.stdout.write(JSON.stringify(report));
