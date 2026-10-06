import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

// Invoked only by the isolated Python integration test; no request interception.
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4186";
const report = { graph_saved: false, appearance_restored: false, other_window_conflict_preserved: false, page_errors: [], unimplemented_runtime_routes: [] };
const missing = new Set();
const output = resolve(".runtime/python-browser");
await mkdir(output, { recursive: true });
const server = await createServer({
    server: { host: "127.0.0.1", port: 4186, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } },
    logLevel: "silent",
});
await server.listen();
let browser;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const contexts = [];
    for (let index = 0; index < 2; index++) {
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
        context.setDefaultTimeout(15000);
        context.setDefaultNavigationTimeout(60000);
        context.on("response", response => {
            const path = new URL(response.url()).pathname;
            if (response.status() === 404 && path.startsWith("/api/v1/canvas-runtime/")) missing.add(path);
        });
        await read(await context.request.post(`${origin}/api/v1/auth/login`, {
            headers: { Origin: origin }, data: { username: config.username, password: config.password },
        }));
        const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
        assert.ok(csrf, "real login must establish CSRF cookie");
        await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
        contexts.push(context);
    }
    const context = contexts[0];
    const project = await read(await context.request.post(`${origin}/api/v1/projects`, {
        headers: { "Idempotency-Key": "browser-python-project" },
        data: { name: "真实 Python 浏览器验证", aspect: "16:9", workspace_mode: "infinite_canvas" },
    }));
    const path = `${origin}/api/v1/projects/${project.id}/canvases/${project.primary_canvas_id}`;
    const initial = await read(await context.request.get(`${path}/my-document`));
    const seeded = initial.source_document;
    seeded.nodes = [{ id: "browser-note", type: "text", title: "真实保存", position: { x: 420, y: 260 }, width: 320, height: 220, metadata: { content: "连接 Python 与 MySQL", prompt: "测试作者私人提示词" } }];
    await read(await context.request.post(`${path}/commits`, {
        headers: { "Idempotency-Key": "browser-python-seed" },
        data: { expected_row_version: initial.row_version, source_document: seeded },
    }));
    const url = `${origin}/canvas-app/canvas/${initial.source_key}`;
    page = await context.newPage();
    page.on("pageerror", error => report.page_errors.push(error.message));
    await page.goto(url);
    const node = page.locator('[data-node-id="browser-note"]');
    await node.waitFor({ state: "visible", timeout: 60000 });
    await page.evaluate(() => document.fonts.ready);
    const before = await node.boundingBox();
    await Promise.all([
        page.waitForResponse(response => response.url().endsWith("/commits") && response.ok()),
        (async () => {
            await page.mouse.move(before.x + 20, before.y + 8);
            await page.mouse.down();
            await page.mouse.move(before.x + 130, before.y + 68, { steps: 12 });
            await page.mouse.up();
        })(),
    ]);
    const saved = await read(await context.request.get(`${path}/my-document`));
    assert.ok(saved.source_document.nodes[0].position.x !== seeded.nodes[0].position.x);
    assert.equal(saved.source_document.nodes[0].metadata.prompt, "测试作者私人提示词");
    report.graph_saved = true;
    await page.reload();
    await node.waitFor({ state: "visible" });
    const refreshed = await read(await context.request.get(`${path}/my-document`));
    assert.deepEqual(refreshed.source_document.nodes[0].position, saved.source_document.nodes[0].position);
    const second = await contexts[1].newPage();
    second.on("pageerror", error => report.page_errors.push(error.message));
    await second.goto(url);
    await second.locator('[data-node-id="browser-note"]').waitFor({ state: "visible" });
    await page.getByRole("button", { name: "画布外观", exact: true }).click();
    await Promise.all([
        page.waitForResponse(response => response.url().endsWith("/view-preferences") && response.request().postDataJSON()?.preferences?.appearance?.mode === "light" && response.ok()),
        page.getByRole("button", { name: "切换到浅色主题", exact: true }).click(),
    ]);
    await second.getByRole("button", { name: "画布外观", exact: true }).click();
    await Promise.all([
        second.waitForResponse(response => response.url().endsWith("/view-preferences") && response.status() === 409),
        second.getByRole("button", { name: "切换到自定义主题", exact: true }).click(),
    ]);
    await second.getByRole("alert").filter({ hasText: "外观已在其他窗口更新，本机设置已保留" }).waitFor({ state: "visible" });
    assert.equal(await second.getByRole("button", { name: "切换到自定义主题", exact: true }).getAttribute("aria-pressed"), "true");
    const current = await read(await context.request.get(`${path}/my-document`));
    assert.equal(current.source_document.appearance.mode, "light");
    report.other_window_conflict_preserved = true;
    await page.reload();
    await node.waitFor({ state: "visible" });
    await page.getByRole("button", { name: "画布外观", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "切换到浅色主题", exact: true }).getAttribute("aria-pressed"), "true");
    report.appearance_restored = true;
    assert.deepEqual(report.page_errors, []);
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    throw error;
} finally {
    report.unimplemented_runtime_routes = [...missing].sort();
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
