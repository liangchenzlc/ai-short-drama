import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

// Invoked only by the isolated Python integration test; every API request is real.
// Host destinations use an explicit HTML navigation boundary, not the host renderer.
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4186";
const report = { graph_saved: false, appearance_restored: false, other_window_conflict_preserved: false, valid_canvas_opened_without_revocation: false, csrf_failure_preserved_access: false, csrf_failure_diagnostic: null, valid_save_recovered_after_csrf_failure: false, returned_to_host_projects: false, old_site_redirects: [], source_site_modules_loaded: [], host_page_rendering: "navigation_boundary_only", page_errors: [], unimplemented_runtime_routes: [] };
const missing = new Set();
const sourceSiteModules = new Set();
const output = resolve(".runtime/python-browser");
await mkdir(output, { recursive: true });
const server = await createServer({
    plugins: [{
        name: "python-browser-host-navigation-boundary",
        configureServer(current) {
            current.middlewares.use((request, response, next) => {
                const path = new URL(request.url || "/", origin).pathname;
                if (!/^\/(?:projects|ai_config|assets|tasks|media-library|account)(?:\/|$)/u.test(path)) return next();
                response.statusCode = 200;
                response.setHeader("Content-Type", "text/html; charset=utf-8");
                response.end('<!doctype html><html lang="zh-CN"><body><main data-test-host-boundary>宿主导航边界</main></body></html>');
            });
        },
    }],
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
        context.on("request", request => {
            const path = new URL(request.url()).pathname;
            if (/\/src\/pages\/(?:home|projects|assets|settings|tasks|agents|create|admin)(?:\/|\.)/u.test(path)
                || /\/src\/pages\/canvas\/index\.tsx$/u.test(path)) sourceSiteModules.add(path);
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
    assert.equal(await page.getByRole("alert").filter({ hasText: "画布已被归档或访问权限已变化" }).count(), 0);
    assert.equal(await page.evaluate(async key => {
        const { hostCanvasAccess } = await import("/canvas-app/src/services/host-canvas-access.ts");
        const { captureUserScope } = await import("/canvas-app/src/lib/user-scope-guard.ts");
        return hostCanvasAccess.reason(captureUserScope(), key);
    }, initial.source_key), "");
    report.valid_canvas_opened_without_revocation = true;
    const viewportBefore = (await read(await context.request.get(`${path}/my-document`))).source_document.viewport;
    const viewportInput = { expected_viewport: viewportBefore, viewport: viewportBefore || { x: 0, y: 0, k: 1 } };
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    // Keep the legal Origin so module loading and CORS cannot mask the real CSRF 403.
    const invalidCsrf = "invalid-canvas-browser-csrf";
    await context.addCookies([{ ...csrf, value: invalidCsrf }]);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": invalidCsrf });
    try {
        const rejected = await page.evaluate(async ({ key, input }) => {
            const { hostCanvasAccess } = await import("/canvas-app/src/services/host-canvas-access.ts");
            const { captureUserScope } = await import("/canvas-app/src/lib/user-scope-guard.ts");
            try {
                const { http } = await import("/canvas-app/src/services/api/request.ts");
                await http.put(`/canvas-projects/${encodeURIComponent(key)}/viewport`, input);
                return { status: 200, reason: "", accessReason: hostCanvasAccess.reason(captureUserScope(), key) };
            } catch (error) {
                return { status: error.status, reason: error.reason, name: error.name, message: error.message, stack: error.stack, accessReason: hostCanvasAccess.reason(captureUserScope(), key) };
            }
        }, { key: initial.source_key, input: viewportInput });
        report.csrf_failure_diagnostic = rejected;
        const diagnostic = JSON.stringify(rejected);
        assert.equal(rejected.status, 403, `Real viewport write must expose the CSRF status: ${diagnostic}`);
        assert.equal(rejected.reason, "csrf_failed", `Real viewport write must expose the CSRF code: ${diagnostic}`);
        assert.equal(rejected.accessReason, "", `CSRF failure must preserve canvas access: ${diagnostic}`);
        report.csrf_failure_preserved_access = true;
    } finally {
        await context.addCookies([csrf]);
        await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    }
    await page.evaluate(async ({ key, input }) => {
        const { http } = await import("/canvas-app/src/services/api/request.ts");
        await http.put(`/canvas-projects/${encodeURIComponent(key)}/viewport`, input);
    }, { key: initial.source_key, input: viewportInput });
    report.valid_save_recovered_after_csrf_failure = true;
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
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "打开画布菜单", exact: true }).click();
    await page.getByRole("menuitem", { name: "返回工作台", exact: true }).click();
    await page.locator("[data-test-host-boundary]").waitFor();
    assert.equal(new URL(page.url()).pathname, "/projects");
    report.returned_to_host_projects = true;
    const redirects = [
        ["/canvas-app/", "/projects"],
        ["/canvas-app/home", "/projects"],
        ["/canvas-app/canvas", "/projects"],
        ["/canvas-app/projects", "/projects"],
        ["/canvas-app/projects/legacy/overview", "/projects"],
        ["/canvas-app/projects/create", "/projects"],
        ["/canvas-app/create", "/projects"],
        ["/canvas-app/agents", "/projects"],
        ["/canvas-app/admin", "/projects"],
        ["/canvas-app/settings?section=channels", "/ai_config"],
        ["/canvas-app/assets", "/assets"],
        ["/canvas-app/assets/legacy", "/assets"],
        ["/canvas-app/tasks", "/tasks"],
        ["/canvas-app/tasks/legacy", "/tasks"],
    ];
    for (const [from, to] of redirects) {
        await page.goto(origin + from);
        await page.locator("[data-test-host-boundary]").waitFor();
        assert.equal(new URL(page.url()).pathname, to, `${from} must leave the canvas application`);
        assert.equal(await page.locator(".beeftv-home, .app-workspace-sidebar-brand-button").count(), 0);
        report.old_site_redirects.push({ from, to });
    }
    assert.deepEqual([...sourceSiteModules], [], "removed source-site modules must never load");
    assert.deepEqual(report.page_errors, []);
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    throw error;
} finally {
    report.unimplemented_runtime_routes = [...missing].sort();
    report.source_site_modules_loaded = [...sourceSiteModules].sort();
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
