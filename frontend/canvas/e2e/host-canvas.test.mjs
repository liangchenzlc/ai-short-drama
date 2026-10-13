import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";
import { chromium } from "playwright";
import { createServer } from "vite";

const testCacheDir = resolve(".runtime/vite-e2e-host-canvas");

// These fixtures verify the real editor and HTTP adapter, not providers or Python persistence.
const initial = {
    id: "parity-canvas", workspaceProjectId: "9007199254740995", revision: "1",
    title: "迁移对照", canvasTitle: "主画布", createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z",
    nodes: [{ id: "text-one", type: "text", title: "文字节点", position: { x: 420, y: 260 }, width: 320, height: 220, metadata: { content: "画布迁移操作验证" } }],
    connections: [], chatSessions: [], activeChatId: null, directorScenes: [],
    viewport: { x: 0, y: 0, k: 1 }, backgroundMode: "dots", showImageInfo: false,
};

async function installApi(page, options = {}) {
    let document = structuredClone(initial);
    if (options.nodes) document.nodes = structuredClone(options.nodes);
    const commits = [];
    const viewportWrites = [];
    const preferenceWrites = [];
    const modelWrites = [];
    const assistantCalls = [];
    const assistantMessages = [];
    const assistantConversation = {
        id: "301", project_id: initial.workspaceProjectId, episode_id: null,
        title: "画布讨论", scope_version: 2, stage: null, subject_type: null,
        subject_id: null, task_type: null, row_version: "1", archived: false,
        last_run_status: null, created_at: initial.createdAt, updated_at: initial.updatedAt,
    };
    const legacyConversation = { ...assistantConversation, id: "401", episode_id: "20", title: "历史画布讨论", scope_version: 1 };
    const legacyMessages = [{ id: "1101", seq: 1, role: "assistant", content: "已保存的旧助手消息", references: [], artifacts: [], created_at: initial.createdAt }];
    let modelPreferences = {};
    let holdCommits = Boolean(options.holdCommits);
    const commitWaiters = [];
    let signalHeldCommit;
    const heldCommit = new Promise(resolve => { signalHeldCommit = resolve; });
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
        else if (path === "/agent/models") body = { items: [{ id: "71", name: "对话模型", row_version: "1", model_key: "fixture", protocol: "chat", verified: false, preferred: true }], preferred_id: "71" };
        else if (path === "/agent/skills") body = { items: [], total: 0, offset: 0, limit: 50 };
        else if (path === "/canvas-runtime/assets" || path === "/canvas-runtime/tasks") body = [];
        else if (path === "/canvas-runtime/plugins/status" && request.method() === "GET") {
            const pluginIds = ["prompt-optimizer", "ai-art-critique", "runninghub-workflow-provider"];
            body = {
                statuses: Object.fromEntries(pluginIds.map(pluginId => [pluginId, "disabled"])),
                states: Object.fromEntries(pluginIds.map(pluginId => [pluginId, {
                    pluginId, platformAvailable: true, userEnabled: false, userConfigured: false,
                    effectiveEnabled: false, canToggle: true, canConfigure: false,
                }])),
            };
        }
        else if (path === `/projects/${initial.workspaceProjectId}/assets`) body = { items: [], total: 0, offset: 0, limit: 20 };
        else if (path === "/agent/conversations/401") body = legacyConversation;
        else if (path === "/agent/conversations/401/messages") body = { items: legacyMessages, total: legacyMessages.length, offset: 0, limit: 50 };
        else if (path.startsWith("/assistant/")) {
            const method = request.method();
            const input = ["POST", "PATCH"].includes(method) ? request.postDataJSON() : null;
            assistantCalls.push({ path, method, body: input, key: request.headers()["idempotency-key"] });
            if (path === "/assistant/conversations/resolve" || path === "/assistant/conversations/301") body = assistantConversation;
            else if (path === "/assistant/conversations") body = { items: [assistantConversation], total: 1, offset: 0, limit: 20 };
            else if (path === "/assistant/legacy-conversations") body = { items: [legacyConversation], total: 1, offset: 0, limit: 20 };
            else if (path.endsWith("/events")) {
                await route.fulfill({ contentType: "text/event-stream", body: ": heartbeat\n\n" });
                return;
            }
            else if (path.endsWith("/state")) body = { conversation_id: "301", cursor: 0, resume_cursor: 0, active_run: null, queued_runs: [] };
            else if (path.endsWith("/runs")) body = { items: [], total: 0, offset: 0, limit: 1 };
            else if (path.endsWith("/attachments")) body = { items: [], total: 0, offset: 0, limit: 50 };
            else if (path.endsWith("/messages") && method === "GET") body = { items: assistantMessages, total: assistantMessages.length, offset: 0, limit: 50 };
            else if (path.endsWith("/messages") && method === "POST") {
                assert.ok(request.headers()["idempotency-key"]);
                assert.equal("task" in input, false);
                assert.equal("mode" in input, false);
                const message = { id: String(1001 + assistantMessages.length), seq: assistantMessages.length + 1, role: "user", content: input.content, references: [], artifacts: [], created_at: initial.createdAt };
                assistantMessages.push(message, { ...message, id: String(1002 + assistantMessages.length), seq: message.seq + 1, role: "assistant", content: "一起分析画面与故事节奏。" });
                body = { message, cursor: 0, run: { id: String(901 + assistantMessages.length), conversation_id: "301", status: "succeeded", phase: "finished", row_version: "1", mode: "discuss", model_config_id: "71", model_name: "对话模型", error: null, usage: {}, budget: {}, review: null, awaiting_artifact_ids: [], created_at: initial.createdAt, updated_at: initial.updatedAt, finished_at: initial.updatedAt, queue_position: 0, waiting_reason: null } };
            } else {
                unimplemented.add(`${method} ${path}`);
                await route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Fixture does not implement this feature" } } });
                return;
            }
        }
        else if (path === "/canvas-runtime/workspace/model-config") {
            if (request.method() === "PUT") {
                const input = request.postDataJSON();
                assert.equal("channels" in input, false);
                modelWrites.push(input);
                if (options.rejectModelPreferences) {
                    await route.fulfill({ status: 409, json: { error: { code: "canvas_model_config_conflict", message: "Model preferences changed" } } });
                    return;
                }
                modelPreferences = input.preferences;
            }
            body = { row_version: String(modelWrites.length), preferences: modelPreferences, models: options.models || [] };
        }
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
            if (holdCommits) await new Promise(resolve => {
                commitWaiters.push(resolve);
                signalHeldCommit();
            });
            if (options.rejectCommits) {
                await route.fulfill({ status: 409, json: { error: { code: "canvas_revision_conflict", message: "画布已变化，当前草稿已保留", details: { current_version: "2" } } } });
                return;
            }
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
    return { commits, viewportWrites, preferenceWrites, modelWrites, assistantCalls, assistantMessages, unimplemented, document: () => document, revoke: () => { accessible = false; },
        heldCommit, waitingCommits: () => commitWaiters.length, releaseCommits: () => { holdCommits = false; for (const release of commitWaiters.splice(0)) release(); } };
}

test("图形视口预览不重复测量容器，窗口缩放后选区与DPR保持对齐", { timeout: 120000 }, async () => {
    await mkdir(resolve(".runtime/browser"), { recursive: true });
    const browserBaseUrl = process.env.CANVAS_BROWSER_BASE_URL || "http://127.0.0.1:4182";
    const server = process.env.CANVAS_BROWSER_BASE_URL ? undefined : await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server?.listen();
    let browser;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        for (const deviceScaleFactor of [1, 2]) {
            const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor, colorScheme: "dark", reducedMotion: "no-preference" });
            try {
                await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: browserBaseUrl }]);
                const page = await context.newPage();
                const errors = [];
                page.on("pageerror", error => errors.push(error.message));
                const api = await installApi(page, { nodes: [
                    { ...initial.nodes[0], width: 160, height: 180 },
                    { ...initial.nodes[0], id: "text-two", title: "第二文字节点", position: { x: 620, y: 260 }, width: 160, height: 180 },
                ] });
                await page.addInitScript(() => {
                    window.__graphicsPreview = { calls: 0, reads: 0, viewports: [] };
                    let inPreview = false;
                    const dispatch = EventTarget.prototype.dispatchEvent;
                    EventTarget.prototype.dispatchEvent = function (event) {
                        const previous = inPreview;
                        if (event.type === "canvas:graphics-viewport-preview") {
                            inPreview = true;
                            window.__graphicsPreview.calls++;
                            window.__graphicsPreview.viewports.push({ ...event.detail });
                        }
                        try { return dispatch.call(this, event); }
                        finally { inPreview = previous; }
                    };
                    const bounds = Element.prototype.getBoundingClientRect;
                    Element.prototype.getBoundingClientRect = function () {
                        if (inPreview && (this.hasAttribute("data-canvas-viewport") || this.hasAttribute("data-node-id"))) window.__graphicsPreview.reads++;
                        return bounds.call(this);
                    };
                });
                await page.goto(`${browserBaseUrl}/canvas-app/canvas/parity-canvas`);
                const node = page.locator('[data-node-id="text-one"]');
                await node.waitFor({ state: "visible" });
                await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor();
                await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
                await page.waitForFunction(() => {
                    const container = document.querySelector("[data-canvas-viewport]");
                    const bounds = document.querySelector('[data-node-id="text-one"]').getBoundingClientRect();
                    return !container.dataset.canvasViewportInteracting && bounds.left >= 0 && bounds.right <= innerWidth && bounds.bottom <= innerHeight - 200;
                });
                await node.locator(".canvas-node-external-header > div > svg").click();
                const panel = page.locator("[data-canvas-node-panel]");
                await panel.waitFor({ state: "visible" });
                const panelBefore = await panel.boundingBox();
                const nodeBefore = await node.boundingBox();
                const before = await page.evaluate(() => ({ ...window.__graphicsPreview }));
                const panBounds = await page.locator("[data-canvas-viewport]").boundingBox();
                const panStart = { x: panBounds.x + panBounds.width - 80, y: panBounds.y + panBounds.height - 140 };
                await page.mouse.move(panStart.x, panStart.y);
                await page.keyboard.down("Space");
                await page.mouse.down();
                for (let step = 1; step <= 12; step++) {
                    await page.mouse.move(panStart.x - step * 3, panStart.y - step * 2);
                    await page.evaluate(() => new Promise(requestAnimationFrame));
                }
                await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
                const panelAfter = await panel.boundingBox();
                const nodeAfter = await node.boundingBox();
                for (const coordinate of ["x", "y"]) {
                    assert.ok(Math.abs((panelAfter[coordinate] - panelBefore[coordinate]) - (nodeAfter[coordinate] - nodeBefore[coordinate])) <= 1, `单选浮层${coordinate}必须逐帧跟随真实节点：${JSON.stringify({ panelBefore, panelAfter, nodeBefore, nodeAfter })}`);
                }
                assert.ok(Math.abs(panelAfter.y - (nodeAfter.y + nodeAfter.height) - 16) <= 1, "无遮挡时单选浮层保持原16px间隔");
                // 在合成预览尚未提交时改变实际容器尺寸。
                await page.setViewportSize({ width: 1000, height: 760 });
                await page.mouse.up();
                await page.keyboard.up("Space");
                const zoomBounds = await page.locator("[data-canvas-viewport]").boundingBox();
                await page.mouse.move(zoomBounds.x + zoomBounds.width - 80, zoomBounds.y + zoomBounds.height - 140);
                await page.keyboard.down("Control");
                for (const delta of [-24, -24, -24, 24, 24, 24]) {
                    await page.mouse.wheel(0, delta);
                    await page.evaluate(() => new Promise(requestAnimationFrame));
                }
                await page.keyboard.up("Control");
                await page.waitForFunction(() => !document.querySelector("[data-canvas-viewport]").dataset.canvasViewportInteracting);
                const after = await page.evaluate(() => ({ ...window.__graphicsPreview }));
                const viewports = after.viewports.slice(before.calls);
                const changed = new Set(viewports.map(viewport => viewport.k)).size > 1 && new Set(viewports.map(viewport => `${viewport.x},${viewport.y}`)).size > 1;
                if (!changed) await page.screenshot({ path: resolve(`.runtime/browser/graphics-preview-dpr${deviceScaleFactor}-failed.png`) });
                assert.ok(changed, `原鼠标平移和缩放必须实际改变图形视口：${JSON.stringify(viewports)}`);
                assert.equal(after.reads - before.reads, 0, "视口预览不能在写入变换后同步测量容器");
                await page.keyboard.down("Control");
                await page.mouse.wheel(0, -48);
                await page.keyboard.up("Control");
                await page.waitForFunction(() => !document.querySelector("[data-canvas-viewport]").dataset.canvasViewportInteracting);
                await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
                const zoomedNode = await node.boundingBox();
                const zoomedPanel = await panel.boundingBox();
                assert.ok(Math.abs(zoomedPanel.y - (zoomedNode.y + zoomedNode.height) - 16) <= 1, `缩放提交后浮层必须继续贴合真实节点边界：${JSON.stringify({ zoomedNode, zoomedPanel })}`);

                for (const size of [{ width: 600, height: 900 }, { width: 1440, height: 900 }]) {
                    await page.setViewportSize(size);
                    await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
                    await page.waitForFunction(() => {
                        const container = document.querySelector("[data-canvas-viewport]");
                        const node = document.querySelector('[data-node-id="text-one"]');
                        if (!node || container.dataset.canvasViewportInteracting) return false;
                        const bounds = node.getBoundingClientRect();
                        return bounds.left >= 0 && bounds.right <= innerWidth && bounds.top >= 0 && bounds.bottom <= innerHeight;
                    });
                    await page.keyboard.press("Escape");
                    // Playwright等待原图标稳定并命中；避免复用适合屏幕动画中的旧坐标。
                    await node.locator(".canvas-node-external-header > div > svg").click();
                    await page.locator('[data-node-id="text-two"] .canvas-node-external-header > div > svg').click({ modifiers: ["Shift"] });
                    await page.locator("[data-canvas-selection-bounds]").waitFor();
                    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
                    await page.waitForFunction(() => {
                        const container = document.querySelector("[data-canvas-viewport]");
                        const canvas = document.querySelector("[data-canvas-leafer-overlay] canvas");
                        const bounds = container.getBoundingClientRect();
                        return canvas && Math.abs(canvas.width - bounds.width * devicePixelRatio) <= 1 && Math.abs(canvas.height - bounds.height * devicePixelRatio) <= 1;
                    });
                    const geometry = await page.evaluate(() => {
                        const host = document.querySelector("[data-canvas-leafer-overlay]");
                        const canvas = host.querySelector("canvas");
                        const rect = canvas.getBoundingClientRect();
                        const nodes = [...document.querySelectorAll('[data-node-id][data-node-selected="true"]')].map(node => node.getBoundingClientRect());
                        const bounds = { left: Math.min(...nodes.map(node => node.left)), top: Math.min(...nodes.map(node => node.top)), right: Math.max(...nodes.map(node => node.right)), bottom: Math.max(...nodes.map(node => node.bottom)) };
                        const image = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height);
                        let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
                        for (let y = 0; y < canvas.height; y++) for (let x = 0; x < canvas.width; x++) {
                            if (image.data[(y * canvas.width + x) * 4 + 3] < 20) continue;
                            left = Math.min(left, x); top = Math.min(top, y);
                            right = Math.max(right, x); bottom = Math.max(bottom, y);
                        }
                        return { preview: host.dataset.canvasLeaferPreview || null, painted: Number.isFinite(left), canvas: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }, selection: { left: rect.left + left / devicePixelRatio, top: rect.top + top / devicePixelRatio, right: rect.left + (right + 1) / devicePixelRatio, bottom: rect.top + (bottom + 1) / devicePixelRatio }, node: { left: bounds.left, top: bounds.top, right: bounds.right, bottom: bounds.bottom } };
                    });
                    assert.equal(geometry.preview, null);
                    assert.ok(geometry.painted, "选区描边必须绘制到真实canvas");
                    for (const [side, padding] of [["left", -12], ["top", -12], ["right", 12], ["bottom", 12]]) {
                        const expected = padding < 0 ? Math.max(geometry.canvas[side], geometry.node[side] + padding) : Math.min(geometry.canvas[side], geometry.node[side] + padding);
                        assert.ok(Math.abs(geometry.selection[side] - expected) <= 2, `${size.width}px/DPR${deviceScaleFactor}选区${side}未对齐：${JSON.stringify(geometry)}`);
                    }
                    await page.screenshot({ path: resolve(`.runtime/browser/graphics-selection-dpr${deviceScaleFactor}-${size.width}.png`) });
                }
                assert.deepEqual(errors, []);
                assert.deepEqual([...api.unimplemented], []);
            } finally {
                await context.close();
            }
        }
    } finally {
        await browser?.close();
        await server?.close();
    }
});

test("原版编辑器拖拽保存、视口和外观独立恢复，撤权后停止编辑", { timeout: 90000 }, async () => {
    // 外部统一 preview 用于复测生产产物；默认仍运行独立画布开发服务器。
    const browserBaseUrl = process.env.CANVAS_BROWSER_BASE_URL || "http://127.0.0.1:4182";
    const server = process.env.CANVAS_BROWSER_BASE_URL ? undefined : await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server?.listen();
    let browser;
    let page;
    let api;
    const diagnostics = { errors: [], unimplemented: [], failedRequests: [], httpErrors: [] };
    await mkdir(resolve(".runtime/browser"), { recursive: true });
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
        await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: browserBaseUrl }]);
        page = await context.newPage();
        page.on("pageerror", error => diagnostics.errors.push(error.stack ?? error.message));
        page.on("requestfailed", request => diagnostics.failedRequests.push({
            method: request.method(), path: new URL(request.url()).pathname,
            error: request.failure()?.errorText,
        }));
        page.on("response", response => {
            if (response.status() >= 400) diagnostics.httpErrors.push({
                method: response.request().method(), path: new URL(response.url()).pathname,
                status: response.status(),
            });
        });
        api = await installApi(page);
        await page.goto(new URL("/canvas-app/canvas/parity-canvas", browserBaseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
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
        await page.route("**/projects", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: "<main>宿主项目列表</main>" }));
        await page.getByRole("button", { name: "返回工作台", exact: true }).click();
        await page.waitForURL(new URL("/projects", browserBaseUrl).href);
        await page.getByText("宿主项目列表").waitFor({ state: "visible" });
        diagnostics.unimplemented = [...api.unimplemented];
        assert.deepEqual(diagnostics.errors, []);
        assert.deepEqual(diagnostics.unimplemented, []);
    } catch (error) {
        diagnostics.unimplemented = [...(api?.unimplemented ?? [])];
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

test("助手首次加载中关闭后再次打开可恢复对话", { timeout: 120000 }, async () => {
    const baseUrl = process.env.CANVAS_BROWSER_BASE_URL || "http://127.0.0.1:4182";
    const server = process.env.CANVAS_BROWSER_BASE_URL ? undefined : await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server?.listen();
    let browser;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        for (const width of [1440, 390]) {
            const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
            await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: baseUrl }]);
            const page = await context.newPage();
            const errors = [];
            page.on("pageerror", error => errors.push(error.stack ?? error.message));
            const api = await installApi(page);
            let holdInitial = true;
            let resolveRequests = 0;
            let releaseInitial;
            const pendingInitial = new Promise(resolve => { releaseInitial = resolve; });
            let signalStarted;
            const initialStarted = new Promise(resolve => { signalStarted = resolve; });
            await page.route("**/api/v1/assistant/conversations/resolve", async route => {
                resolveRequests += 1;
                signalStarted();
                if (holdInitial) await pendingInitial;
                await route.fallback();
            });
            try {
                await page.goto(new URL("/canvas-app/canvas/parity-canvas?assistant=1", baseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
                await initialStarted;
                await page.getByRole("button", { name: "关闭助手", exact: true }).click();
                const input = page.getByRole("textbox", { name: "给助手的消息" });
                await input.waitFor({ state: "hidden" });
                const beforeReopen = resolveRequests;
                holdInitial = false;
                await page.getByRole("button", { name: "AI 创作助手", exact: true }).click();
                await input.waitFor({ state: "visible", timeout: 10000 });
                assert.ok(resolveRequests > beforeReopen);
                await input.fill("重新打开后仍可编辑");
                releaseInitial();
                assert.equal(await input.inputValue(), "重新打开后仍可编辑");
                assert.deepEqual([...api.unimplemented], []);
                assert.deepEqual(errors, []);
            } finally {
                releaseInitial();
                await context.close();
            }
        }
    } finally {
        await browser?.close();
        await server?.close();
    }
});

test("设置与工作台导航等待画布保存，409保留草稿", { timeout: 180000 }, async () => {
    const baseUrl = process.env.CANVAS_BROWSER_BASE_URL || "http://127.0.0.1:4182";
    const server = process.env.CANVAS_BROWSER_BASE_URL ? undefined : await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server?.listen();
    let browser;
    let privatePreferenceServer;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        const direct = await browser.newPage();
        await installApi(direct);
        await direct.route("**/ai_config*", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: "<main>宿主模型配置</main>" }));
        for (const [suffix, expected] of [["?section=channels", "/ai_config"], ["?return_to=https%3A%2F%2Fevil.test", "/ai_config"], ["?return_to=%2Fcanvas-app%2Fcanvas%2Fparity-canvas", "/ai_config?return_to=%2Fcanvas-app%2Fcanvas%2Fparity-canvas"]]) {
            await direct.goto(new URL(`/canvas-app/settings${suffix}`, baseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
            await direct.waitForURL(new URL(expected, baseUrl).href, { timeout: 60000 });
            await direct.getByText("宿主模型配置").waitFor({ state: "visible" });
        }
        await direct.close();

        for (const [destination, rejectCommits, editDuringSave] of [["模型配置", false], ["模型配置", true], ["返回工作台", false], ["返回工作台", true], ["返回工作台", false, true]]) {
            const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
            await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: baseUrl }]);
            const page = await context.newPage();
            const errors = [];
            page.on("pageerror", error => errors.push(error.stack ?? error.message));
            const api = await installApi(page, { holdCommits: true, rejectCommits });
            await page.route("**/ai_config*", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: "<main>宿主模型配置</main>" }));
            await page.route("**/projects", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: "<main>宿主项目列表</main>" }));
            await page.goto(new URL("/canvas-app/canvas/parity-canvas", baseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
            const node = page.locator('[data-node-id="text-one"]');
            await node.waitFor({ state: "visible", timeout: 60000 });
            const before = await node.boundingBox();
            await page.mouse.move(before.x + 20, before.y + 8);
            await page.mouse.down();
            await page.mouse.move(before.x + 130, before.y + 68, { steps: 12 });
            await page.mouse.up();
            for (let attempt = 0; api.waitingCommits() === 0 && attempt < 100; attempt++) await new Promise(resolve => setTimeout(resolve, 50));
            assert.equal(api.waitingCommits(), 1);
            await page.getByRole("button", { name: "打开画布菜单", exact: true }).click();
            await page.getByRole("menuitem", { name: destination, exact: true }).click();
            assert.equal(new URL(page.url()).pathname, "/canvas-app/canvas/parity-canvas");
            await page.getByRole("button", { name: "画布保存状态：正在保存", exact: true }).waitFor({ state: "visible" });
            if (editDuringSave) {
                await page.getByRole("button", { name: "编辑节点名称：文字节点", exact: true }).click();
                const title = page.getByRole("textbox", { name: "节点名称", exact: true });
                await title.fill("保存期间的新节点名称");
                await title.press("Enter");
                await page.getByRole("button", { name: "编辑节点名称：保存期间的新节点名称", exact: true }).waitFor({ state: "visible" });
            }
            api.releaseCommits();
            if (editDuringSave) {
                await page.getByRole("alert").filter({ hasText: "保存期间内容再次变化" }).waitFor({ state: "visible" }).catch(async error => {
                    throw new Error(`${error.message}\n${JSON.stringify({ url: page.url(), commits: api.commits, body: await page.locator("body").innerText() })}`, { cause: error });
                });
                assert.equal(new URL(page.url()).pathname, "/canvas-app/canvas/parity-canvas");
                assert.equal(await page.getByRole("button", { name: "编辑节点名称：保存期间的新节点名称", exact: true }).isVisible(), true);
            } else if (rejectCommits) {
                await page.getByRole("button", { name: "画布保存状态：版本冲突 · 未同步", exact: true }).waitFor({ state: "visible" });
                await page.getByRole("alert").waitFor({ state: "visible" });
                await page.getByText("此画布的自动提交已暂停。加载最新版前会保留本地草稿，可下载后从画布列表导入为副本。", { exact: true }).waitFor({ state: "visible" });
                assert.equal(new URL(page.url()).pathname, "/canvas-app/canvas/parity-canvas");
                const draft = await node.boundingBox();
                assert.ok(draft.x > before.x + 80);
                assert.equal(api.commits.length, 0);
            } else {
                await page.waitForURL(new URL(destination === "模型配置" ? "/ai_config?return_to=%2Fcanvas-app%2Fcanvas%2Fparity-canvas" : "/projects", baseUrl).href);
                assert.ok(api.commits.length > 0);
                assert.ok(api.document().nodes[0].position.x > initial.nodes[0].position.x);
                await page.getByText(destination === "模型配置" ? "宿主模型配置" : "宿主项目列表").waitFor({ state: "visible" });
            }
            assert.deepEqual(errors, []);
            await context.close();
        }

        // This section injects an existing private preference through the real development store.
        // The API remains a fixture; this does not verify a user model-selection UI or providers.
        privatePreferenceServer = await createServer({ cacheDir: resolve(".runtime/vite-e2e-private-preferences"), server: { host: "127.0.0.1", port: 4183, strictPort: true }, logLevel: "error" });
        await privatePreferenceServer.listen();
        const preferencesBaseUrl = "http://127.0.0.1:4183";
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
        await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: preferencesBaseUrl }]);
        const page = await context.newPage();
        const models = [
            { id: "91", name: "第一文本模型", model_key: "first", provider: "fixture", service_type: "text", enabled: true, has_api_key: true, is_default: true },
            { id: "92", name: "第二文本模型", model_key: "second", provider: "fixture", service_type: "text", enabled: true, has_api_key: true },
        ];
        const api = await installApi(page, { models, rejectModelPreferences: true });
        await page.goto(new URL("/canvas-app/canvas/parity-canvas", preferencesBaseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
        await page.locator('[data-node-id="text-one"]').waitFor({ state: "visible", timeout: 60000 });
        const preferencesFailed = page.waitForResponse(response => response.url().endsWith("/workspace/model-config") && response.status() === 409);
        await page.evaluate(async () => {
            const { useConfigStore } = await import("/canvas-app/src/stores/use-config-store.ts");
            useConfigStore.getState().updateConfig("textModel", "host-92::second");
        });
        await preferencesFailed;
        await page.getByRole("button", { name: "打开画布菜单", exact: true }).click();
        await page.getByRole("menuitem", { name: "模型配置", exact: true }).click();
        await page.getByRole("button", { name: "画布保存状态：离开画布未完成", exact: true }).waitFor({ state: "visible" });
        await page.getByRole("alert").filter({ hasText: "数据已被修改，请重新加载最新内容后再操作。" }).waitFor({ state: "visible" });
        assert.equal(new URL(page.url()).pathname, "/canvas-app/canvas/parity-canvas");
        assert.equal(api.modelWrites.length, 1);
        assert.equal(api.modelWrites[0].preferences.textModel, "host-92::second");
        assert.equal(api.commits.length, 0);
        assert.equal(await page.evaluate(async () => {
            const { useConfigStore } = await import("/canvas-app/src/stores/use-config-store.ts");
            return useConfigStore.getState().config.textModel;
        }), "host-92::second");
        await context.close();
    } finally {
        await browser?.close();
        await server?.close();
        await privatePreferenceServer?.close();
    }
});


test("共用创作助手保存后引用画布、保留草稿并只读打开旧记录", { timeout: 120000 }, async () => {
    const baseUrl = process.env.CANVAS_BROWSER_BASE_URL || "http://127.0.0.1:4182";
    const server = process.env.CANVAS_BROWSER_BASE_URL ? undefined : await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server?.listen();
    let browser;
    let page;
    const errors = [];
    const legacyRequests = [];
    await mkdir(resolve(".runtime/browser"), { recursive: true });
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
        await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: baseUrl }]);
        page = await context.newPage();
        page.on("pageerror", error => errors.push(error.stack ?? error.message));
        page.on("request", request => {
            if (request.url().includes("/agent/conversations/")) legacyRequests.push(request.url());
        });
        const api = await installApi(page, { holdCommits: true });
        await page.goto(new URL("/canvas-app/canvas/parity-canvas", baseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
        const node = page.locator('[data-node-id="text-one"]');
        await node.waitFor({ state: "visible", timeout: 60000 });
        await page.evaluate(() => document.fonts.ready);
        const before = await node.boundingBox();
        assert.ok(before);
        const startedCommit = page.waitForRequest(request => request.url().endsWith("/commits"));
        await page.mouse.move(before.x + 20, before.y + 8);
        await page.mouse.down();
        await page.mouse.move(before.x + 130, before.y + 68, { steps: 12 });
        await page.mouse.up();
        await startedCommit;
        await api.heldCommit;
        assert.equal(api.waitingCommits(), 1);
        await page.getByRole("button", { name: "AI 创作助手", exact: true }).click();
        const input = page.getByRole("textbox", { name: "给助手的消息" });
        await input.waitFor({ state: "visible" });
        await input.fill("@文字");
        await page.getByRole("option", { name: "文字节点", exact: true }).click();
        await page.getByRole("button", { name: "移除引用文字节点", exact: true }).waitFor({ state: "visible" });
        await input.fill("一起分析这个文字节点");
        await page.getByRole("button", { name: "发送", exact: true }).click();
        await page.getByText("正在保存并核对作品…", { exact: true }).waitFor({ state: "visible" });
        assert.equal(api.assistantCalls.filter(call => call.method === "POST" && call.path.endsWith("/messages")).length, 0);
        const received = page.waitForResponse(response => response.url().endsWith("/assistant/conversations/301/messages") && response.request().method() === "POST");
        api.releaseCommits();
        await received;
        await page.waitForFunction(() => document.querySelector('.ai-assistant-mention-input textarea')?.value === "");
        let sends = api.assistantCalls.filter(call => call.method === "POST" && call.path.endsWith("/messages"));
        assert.equal(sends.length, 1);
        assert.deepEqual(sends[0].body.context, { kind: "canvas", id: "parity-canvas", revision: api.document().revision, include_document: true, selected: [{ kind: "node", id: "text-one" }] });
        assert.equal(typeof sends[0].body.context.revision, "string");
        assert.match(sends[0].body.context.revision, /^\d+$/);
        assert.equal(api.document().revision, "2");
        await page.getByRole("button", { name: "移除引用文字节点", exact: true }).click();
        await page.getByRole("button", { name: "这条消息不带当前作品", exact: true }).click();
        await input.fill("@文字");
        await page.getByRole("option", { name: "文字节点", exact: true }).click();
        await page.getByRole("button", { name: "移除引用文字节点", exact: true }).waitFor({ state: "visible" });
        await input.fill("仅讨论手工引用的节点");
        const selectedReceived = page.waitForResponse(response => response.url().endsWith("/assistant/conversations/301/messages") && response.request().method() === "POST");
        await page.getByRole("button", { name: "发送", exact: true }).click();
        await selectedReceived;
        await page.waitForFunction(() => document.querySelector('.ai-assistant-mention-input textarea')?.value === "");
        sends = api.assistantCalls.filter(call => call.method === "POST" && call.path.endsWith("/messages"));
        assert.equal(sends.length, 2);
        assert.deepEqual(sends[1].body.context, { kind: "canvas", id: "parity-canvas", revision: api.document().revision, include_document: false, selected: [{ kind: "node", id: "text-one" }] });
        assert.notEqual(sends[0].key, sends[1].key);
        await input.fill("保留的画布对话草稿");
        await page.getByRole("button", { name: "关闭助手", exact: true }).click();
        await input.waitFor({ state: "hidden" });
        await page.getByRole("button", { name: "AI 创作助手", exact: true }).click();
        await input.waitFor({ state: "visible" });
        assert.equal(await input.inputValue(), "保留的画布对话草稿");
        const docked = await page.locator("aside.creative-assistant-canvas").boundingBox();
        assert.ok(docked && docked.width === 380 && docked.x + docked.width <= 1440);
        await page.screenshot({ path: resolve(".runtime/browser/shared-canvas-assistant-1440.png") });
        await page.setViewportSize({ width: 390, height: 900 });
        await input.waitFor({ state: "visible" });
        assert.equal(await input.inputValue(), "保留的画布对话草稿");
        await page.waitForFunction(() => {
            const panel = document.querySelector(".ant-drawer-body .ai-assistant-panel");
            const bounds = panel?.getBoundingClientRect();
            return bounds && bounds.x >= 0 && bounds.right <= window.innerWidth + 1;
        });
        const mobile = await page.locator(".ant-drawer-body .ai-assistant-panel").boundingBox();
        assert.ok(mobile && mobile.x >= 0 && mobile.width <= 391);
        await page.screenshot({ path: resolve(".runtime/browser/shared-canvas-assistant-390.png") });
        await page.goto(new URL("/canvas-app/canvas/parity-canvas?agent=1&conversation=401", baseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
        const legacy = page.getByRole("dialog", { name: "历史画布讨论", exact: true });
        await legacy.waitFor({ state: "visible" });
        await legacy.getByText("已保存的旧助手消息", { exact: true }).waitFor({ state: "visible" });
        assert.equal(await legacy.getByRole("button", { name: "发送", exact: true }).count(), 0);
        const params = new URL(page.url()).searchParams;
        assert.equal(params.get("assistant"), "1");
        assert.equal(params.get("legacy_conversation"), "401");
        assert.equal(params.has("agent"), false);
        assert.notEqual(params.get("conversation"), "401");
        assert.equal(api.assistantCalls.filter(call => call.method === "POST" && call.path.endsWith("/messages")).length, 2);
        assert.deepEqual([...api.unimplemented], []);
        assert.deepEqual(errors, []);
        await context.close();
    } catch (error) {
        if (page) await page.screenshot({ path: resolve(".runtime/browser/shared-canvas-assistant-failure.png") }).catch(() => {});
        throw new Error(`${error.message}\n${JSON.stringify({ errors, url: page?.url(), legacyRequests, body: await page?.locator("body").innerText().catch(() => "") })}`, { cause: error });
    } finally {
        await browser?.close();
        await server?.close();
    }
});
