import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";
import { chromium } from "playwright";
import { createServer } from "vite";

// These fixtures verify the real editor and HTTP adapter, not providers or Python persistence.
const initial = {
    id: "parity-canvas", workspaceProjectId: "9007199254740995", revision: "1",
    title: "迁移对照", canvasTitle: "主画布", createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z",
    nodes: [{ id: "text-one", type: "text", title: "文字节点", position: { x: 420, y: 260 }, width: 320, height: 220, metadata: { content: "画布迁移操作验证" } }],
    connections: [], chatSessions: [], activeChatId: null, directorScenes: [],
    viewport: { x: 0, y: 0, k: 1 }, backgroundMode: "dots", showImageInfo: false,
};

async function installApi(page) {
    let document = structuredClone(initial);
    const commits = [];
    const viewportWrites = [];
    const preferenceWrites = [];
    const preferences = () => ({ appearance: document.appearance ?? null, backgroundMode: document.backgroundMode, showImageInfo: document.showImageInfo });
    let accessible = true;
    const unimplemented = new Set();
    const summary = () => ({ id: "9007199254740993", source_key: document.id, project_id: document.workspaceProjectId,
        title: document.title, row_version: document.revision, schema_version: 1, created_at: document.createdAt, updated_at: document.updatedAt });
    await page.route("**/api/v1/**", async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname.replace("/api/v1", "");
        if (!accessible && (path.includes("/canvases/") || path.includes("/canvas-workspace/resolve/"))) {
            await route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Canvas does not exist" } } });
            return;
        }
        let body;
        if (path === "/auth/me") body = { user: { id: "111", username: "canvas-test", display_name: "画布测试", email: "canvas@example.test" } };
        else if (path === "/canvas-runtime/workspace/model-config") body = { row_version: "0", preferences: {}, models: [] };
        else if (path === "/canvas-runtime/canvas-projects/parity-canvas/events") {
            await route.fulfill({ status: 200, contentType: "text/event-stream", body: ": connected\n\n" });
            return;
        }
        else if (path === "/canvas-workspace") body = { items: [{ ...summary(), canvas_title: document.canvasTitle, node_count: document.nodes.length, preview_nodes: [], source_document: document }], page: 1, page_size: 50, total: 1, has_more: false };
        else if (path === "/canvas-workspace/resolve/parity-canvas") body = summary();
        else if (path.endsWith("/my-document")) body = { ...summary(), source_document: document };
        else if (path.endsWith("/viewport")) {
            const input = request.postDataJSON();
            assert.deepEqual(input.expected_viewport, document.viewport);
            document.viewport = input.viewport;
            viewportWrites.push(input);
            body = { viewport: document.viewport, preferences: {}, row_version: String(viewportWrites.length) };
        }
        else if (path.endsWith("/view-preferences")) {
            const input = request.postDataJSON();
            assert.deepEqual(input.expected_preferences, preferences());
            document = { ...document, ...input.preferences };
            preferenceWrites.push(input);
            body = { preferences: preferences(), row_version: String(preferenceWrites.length) };
        }
        else if (path.endsWith("/commits")) {
            const input = request.postDataJSON();
            assert.equal(request.headers()["x-canvas-actor"], "111");
            assert.equal(request.headers()["x-csrf-token"], "browser-fixture");
            assert.ok(request.headers()["idempotency-key"]);
            assert.equal(input.expected_row_version, document.revision);
            document = { ...input.source_document, ...preferences(), viewport: document.viewport, revision: String(BigInt(document.revision) + 1n) };
            commits.push(structuredClone(document));
            body = summary();
        } else {
            unimplemented.add(`${request.method()} ${path}`);
            await route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Fixture does not implement this feature" } } });
            return;
        }
        await route.fulfill({ json: body });
    });
    return { commits, viewportWrites, preferenceWrites, unimplemented, document: () => document, revoke: () => { accessible = false; } };
}

test("原版编辑器拖拽保存、视口和外观独立恢复，撤权后停止编辑", { timeout: 90000 }, async () => {
    // 外部统一 preview 用于复测生产产物；默认仍运行独立画布开发服务器。
    const browserBaseUrl = process.env.CANVAS_BROWSER_BASE_URL || "http://127.0.0.1:4182";
    const server = process.env.CANVAS_BROWSER_BASE_URL ? undefined : await createServer({ server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server?.listen();
    let browser;
    let page;
    const diagnostics = { errors: [], unimplemented: [] };
    await mkdir(resolve(".runtime/browser"), { recursive: true });
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
        await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: browserBaseUrl }]);
        page = await context.newPage();
        page.on("pageerror", error => diagnostics.errors.push(error.stack ?? error.message));
        const api = await installApi(page);
        await page.goto(new URL("/canvas-app/canvas/parity-canvas", browserBaseUrl).href);
        const node = page.locator('[data-node-id="text-one"]');
        await node.waitFor({ state: "visible", timeout: 60000 });
        await page.locator(".canvas-main-toolbar").waitFor({ state: "visible" });
        await page.evaluate(() => document.fonts.ready);
        const before = await node.boundingBox();
        assert.ok(before);
        const saved = page.waitForResponse(response => response.url().endsWith("/commits") && response.status() === 200);
        await page.mouse.move(before.x + 20, before.y + 8);
        await page.mouse.down();
        await page.mouse.move(before.x + 130, before.y + 68, { steps: 12 });
        await page.mouse.up();
        await page.waitForFunction(() => {
            const node = document.querySelector('[data-node-id="text-one"]');
            return node && node.getBoundingClientRect().x > 480;
        });
        await saved;
        assert.ok(api.commits.some(value => value.nodes[0].position.x !== initial.nodes[0].position.x));
        const after = await node.boundingBox();
        await page.screenshot({ path: resolve(".runtime/browser/canvas-saved.png") });
        await page.reload();
        await node.waitFor({ state: "visible" });
        const restored = await node.boundingBox();
        assert.ok(Math.abs(restored.x - after.x) < 1 && Math.abs(restored.y - after.y) < 1);
        const graphRevision = api.document().revision;
        const viewportSaved = page.waitForResponse(response => response.url().endsWith("/viewport") && response.status() === 200);
        await page.mouse.move(900, 550);
        await page.keyboard.down("Control");
        await page.mouse.wheel(0, -250);
        await page.keyboard.up("Control");
        await viewportSaved;
        assert.equal(api.document().revision, graphRevision);
        assert.ok(api.viewportWrites.length > 0);
        const movedViewport = structuredClone(api.document().viewport);
        await page.reload();
        await node.waitFor({ state: "visible" });
        assert.deepEqual(api.document().viewport, movedViewport);
        const beforeAppearance = api.document().revision;
        await page.getByRole("button", { name: "画布外观", exact: true }).click();
        const lightSaved = page.waitForResponse(response => response.url().endsWith("/view-preferences")
            && response.request().postDataJSON()?.preferences?.appearance?.mode === "light" && response.ok());
        await page.getByRole("button", { name: "切换到浅色主题", exact: true }).click();
        await lightSaved;
        const linesSaved = page.waitForResponse(response => response.url().endsWith("/view-preferences")
            && response.request().postDataJSON()?.preferences?.backgroundMode === "lines" && response.ok());
        await page.getByRole("dialog", { name: "画布外观" }).locator("label.ant-segmented-item").filter({ hasText: /^线$/ }).click();
        await linesSaved;
        assert.equal(api.document().revision, beforeAppearance);
        await page.reload();
        await node.waitFor({ state: "visible" });
        await page.getByRole("button", { name: "画布外观", exact: true }).click();
        assert.equal(await page.getByRole("button", { name: "切换到浅色主题", exact: true }).getAttribute("aria-pressed"), "true");
        assert.equal(await page.getByRole("dialog", { name: "画布外观" }).getByRole("radio", { name: "线", exact: true }).isChecked(), true);
        await page.keyboard.press("Escape");
        const commitsBeforeRevoke = api.commits.length;
        api.revoke();
        await page.getByRole("alert").filter({ hasText: "访问权限已变化" }).waitFor({ state: "visible", timeout: 15000 });
        assert.equal(await node.isVisible(), false);
        assert.equal(api.commits.length, commitsBeforeRevoke);
        diagnostics.unimplemented = [...api.unimplemented];
        assert.deepEqual(diagnostics.errors, []);
    } catch (error) {
        if (page) {
            await page.screenshot({ path: resolve(".runtime/browser/canvas-failure.png") }).catch(() => {});
            diagnostics.body = await page.locator("body").innerText().catch(() => "");
        }
        throw new Error(`${error.message}\n${JSON.stringify(diagnostics)}`, { cause: error });
    } finally {
        await browser?.close();
        await server?.close();
    }
});
