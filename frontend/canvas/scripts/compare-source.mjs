import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

// Controlled API fixtures compare the two real React editors. They do not certify Python,
// model providers, media exports or assistant execution. All source API requests are intercepted.
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = process.env.BEEFTV_ROOT || "D:/code/BeefTV";
const sourceUrl = process.env.BEEFTV_BASE_URL || "http://127.0.0.1:3000";
const targetUrl = "http://127.0.0.1:4184";
const reducedMotion = process.argv.includes("--normal-motion") ? "no-preference" : "reduce";
const commit = execFileSync("git", ["-C", sourceRoot, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
assert.equal(commit, "4ca2a65a7780a8dfcaaa86c33679f84fb04e055c");
assert.equal(execFileSync("git", ["-C", sourceRoot, "status", "--porcelain"], { encoding: "utf8" }).trim(), "", "Source baseline must be clean");
const output = resolve(root, reducedMotion === "reduce" ? ".runtime/parity" : ".runtime/parity-normal-motion");
await mkdir(output, { recursive: true });

const initial = {
    id: "parity-canvas", workspaceProjectId: "9007199254740995", revision: 1,
    title: "迁移对照", canvasTitle: "主画布", createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z",
    nodes: [{ id: "text-one", type: "text", title: "文字节点", position: { x: -110, y: -190 }, width: 320, height: 220, metadata: { content: "画布迁移操作验证" } }],
    connections: [], chatSessions: [], activeChatId: null, directorScenes: [],
    viewport: { x: 0, y: 0, k: 1 }, backgroundMode: "dots", showImageInfo: false,
};

async function fixtures(page, kind) {
    let document = { ...structuredClone(initial), revision: kind === "source" ? 1 : "1" };
    const historyProject = { ...structuredClone(initial), revision: kind === "source" ? 3 : "3" };
    const history = { id: "101", canvasId: initial.id, revision: kind === "source" ? 3 : "3", title: initial.title, nodeCount: 1, connectionCount: 0, payloadBytes: 1024, reason: "automatic", createdAt: initial.createdAt, contentUpdatedAt: initial.updatedAt };
    const historyDto = { id: history.id, canvas_id: "9007199254740993", row_version: "3", title: history.title, node_count: 1, connection_count: 0, payload_bytes: 1024, reason: history.reason, created_at: history.createdAt, content_updated_at: history.contentUpdatedAt };
    const unknown = new Set();
    const summary = () => ({ id: "9007199254740993", source_key: document.id, project_id: document.workspaceProjectId,
        title: document.title, row_version: String(document.revision), schema_version: 1, created_at: document.createdAt, updated_at: document.updatedAt });
    await page.route(url => url.pathname.startsWith("/api/"), async route => {
        const request = route.request();
        const pathname = new URL(request.url()).pathname;
        const path = pathname.replace(/^\/api(?:\/v1(?:\/canvas-runtime)?)?/, "");
        let body;
        if (path === "/auth/me") body = { user: { id: "111", username: "parity", display_name: "画布对照", email: "parity@example.test" } };
        else if (path === "/workspace/bootstrap") body = {
            contractVersion: 1, profile: "local", storageMode: "local", capabilities: { localAssets: true, providerCalls: true },
            user: { id: "111", username: "parity", displayName: "画布对照", role: "user", status: "active" },
            workspace: { id: "111", name: "画布对照", owner: "local", storage: "sqlite" },
            features: { shortDramaEnabled: true, taskCenterEnabled: true, customChannelsEnabled: true, frontendModelsEnabled: false, pluginCenterEnabled: true, systemPluginsVisibleToUsers: true },
        };
        else if (path === "/workspace/model-config") body = kind === "source"
            ? { config: { channels: [] }, revision: 0, health: "ready", source: "builtin+local" }
            : { row_version: "0", preferences: {}, models: [] };
        else if (path.endsWith("/events")) {
            await route.fulfill({ contentType: "text/event-stream; charset=utf-8", body: ": connected\n\n" });
            return;
        }
        else if (path === "/canvas-workspace") body = { items: [{ ...summary(), canvas_title: document.canvasTitle, node_count: document.nodes.length, preview_nodes: [], source_document: document }], page: 1, page_size: 50, total: 1, has_more: false };
        else if (path === "/canvas-workspace/resolve/parity-canvas") body = summary();
        else if (path === "/canvas-projects") body = { projects: [{ ...document, nodeCount: document.nodes.length, previewNodes: document.nodes }], page: 1, pageSize: 50, total: 1, hasMore: false };
        else if (path === "/canvas-projects/parity-canvas" || path.endsWith("/my-document")) body = kind === "source" ? { project: document } : { ...summary(), source_document: document };
        else if (path.endsWith("/viewport")) {
            const input = request.postDataJSON();
            document.viewport = input.viewport;
            body = { viewport: document.viewport, preferences: {}, row_version: "1" };
        }
        else if (path.endsWith("/view-preferences")) {
            const input = request.postDataJSON();
            document = { ...document, ...input.preferences };
            body = { preferences: input.preferences, row_version: "1" };
        }
        else if (path === "/ops/canvas.document.commit" || path.endsWith("/commits")) {
            const input = request.postDataJSON();
            const next = kind === "source" ? Number(document.revision) + 1 : String(BigInt(document.revision) + 1n);
            document = { ...(input.params?.document ?? input.source_document), viewport: document.viewport, revision: next };
            body = kind === "source" ? { op: "canvas.document.commit", opId: input.opId, replayed: false, revision: next,
                result: { canvasId: document.id, revision: next, title: document.title, updatedAt: document.updatedAt } } : summary();
        }
        else if (path === "/assistant/status") body = { available: false, reason: "model_not_configured" };
        else if (path === "/assistant/ui-session") body = { token: "parity-fixture-only", expiresAt: "2100-01-01T00:00:00Z" };
        else if (path === "/assistant/sessions") body = { currentSessionId: null, sessions: [] };
        else if (path === "/assistant/history") body = { sessionId: "", turns: [] };
        else if (path === "/tasks") body = { tasks: [], total: 0, page: 1, pageSize: 50, hasMore: false };
        else if (path === "/assets" && request.method() === "GET") body = { assets: [], page: 1, pageSize: 100, total: 0, hasMore: false };
        else if (path.endsWith("/history")) body = { snapshots: [history], currentRevision: 7 };
        else if (path.endsWith("/revisions")) body = { items: [historyDto], row_version: "7", drawing_heads: {} };
        else if (path.endsWith("/history/101")) body = { snapshot: history, project: historyProject };
        else if (path.endsWith("/revisions/101")) body = { revision: historyDto, source_document: historyProject };
        else if (request.method() === "GET" && /^\/canvas-projects\/[^/]+\/drawings\/[^/]+$/.test(path)) {
            await route.fulfill({ status: 404, json: kind === "source" ? { code: 404, reason: "not_found", msg: "绘图不存在" }
                : { error: { code: "not_found", message: "绘图不存在" } } });
            return;
        }
        else {
            unknown.add(`${request.method()} ${path}`);
            await route.fulfill({ status: 404, json: kind === "source" ? { code: 404, reason: "not_found", msg: "Fixture does not implement this feature" }
                : { error: { code: "not_found", message: "Fixture does not implement this feature" } } });
            return;
        }
        await route.fulfill({ json: kind === "source" ? { code: 0, data: body, msg: "ok" } : body });
    });
    return { unknown, document: () => document };
}

async function settle(page) {
    await page.evaluate(async () => {
        await document.fonts.ready;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        await Promise.all(document.getAnimations().filter(animation => Number.isFinite(animation.effect?.getComputedTiming().endTime))
            .map(animation => animation.finished.catch(() => undefined)));
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        await new Promise((resolve, reject) => {
            let previous = "";
            let stable = 0;
            const deadline = performance.now() + 5000;
            const frame = () => {
                const signature = [...document.querySelectorAll('#canvas-main, .canvas-main-toolbar, .canvas-main-toolbar *, [data-node-id="text-one"], header, [role="dialog"][aria-label="画布外观"], [role="dialog"][aria-label="画布外观"] *, [data-canvas-asset-tray], [data-canvas-asset-tray] *, .canvas-drawing-editor-modal, .canvas-drawing-editor-modal *, .canvas-create-menu-surface, .canvas-create-menu-surface *, .canvas-version-sidebar, .canvas-version-sidebar *, [data-canvas-version-preview], [data-canvas-version-preview] *')]
                    .map(element => {
                        const r = element.getBoundingClientRect();
                        const style = getComputedStyle(element);
                        return [r.x, r.y, r.width, r.height, style.color, style.backgroundColor, style.borderColor, style.opacity, style.transform, style.boxShadow];
                    }).join("|");
                stable = signature === previous ? stable + 1 : 0;
                previous = signature;
                if (stable >= 8) resolve();
                else if (performance.now() > deadline) reject(new Error("对照页面几何未稳定"));
                else requestAnimationFrame(frame);
            };
            requestAnimationFrame(frame);
        });
    });
}

async function capture(page, kind, scenario) {
    await settle(page);
    // DOM geometry can settle before Chrome finishes compositing a modal shadow.
    // Require the actual pixels to stop changing before comparing the two apps.
    let previous;
    let captured;
    let stableFrames = 0;
    for (let attempt = 0; attempt < 12; attempt++) {
        captured = await page.screenshot({ caret: "hide" });
        stableFrames = previous?.equals(captured) ? stableFrames + 1 : 0;
        if (stableFrames >= 2) break;
        previous = captured;
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    }
    assert.ok(stableFrames >= 2, `${kind}/${scenario}: screenshot pixels did not settle`);
    const geometry = await page.evaluate(() => {
        const selectors = ["#canvas-main", ".canvas-main-toolbar", '[data-node-id="text-one"]', "header", "[data-canvas-asset-tray]", ".canvas-create-menu-surface", ".canvas-drawing-editor-modal", ".excalidraw", ".canvas-version-sidebar", "[data-canvas-version-preview]"];
        return Object.fromEntries(selectors.map(selector => [selector, [...document.querySelectorAll(selector)].map(element => {
            const box = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return { x: box.x, y: box.y, width: box.width, height: box.height, font: style.font, color: style.color, background: style.backgroundColor };
        })]));
    });
    await writeFile(resolve(output, `${scenario}.${kind}.png`), captured);
    return geometry;
}

const server = await createServer({ root, server: { host: "127.0.0.1", port: 4184, strictPort: true }, logLevel: "error" });
await server.listen();
let browser;
const report = { commit, scope: "Controlled fixtures: editor layout and recorded pointer/keyboard operations only; no provider or server acceptance", browser: "", viewport: { width: 1440, height: 900 }, dpr: 1, colorScheme: "dark", reducedMotion, scenarios: {}, errors: {}, unimplementedFixtures: {} };
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    report.browser = browser.version();
    for (const kind of ["source", "target"]) {
        const context = await browser.newContext({ viewport: report.viewport, deviceScaleFactor: 1, colorScheme: "dark", reducedMotion, locale: "zh-CN", timezoneId: "UTC" });
        const base = kind === "source" ? sourceUrl : targetUrl;
        await context.addCookies([{ name: "sd_csrf", value: "parity-fixture-only", url: base }]);
        const page = await context.newPage();
        report.errors[kind] = [];
        page.on("pageerror", error => report.errors[kind].push(error.message));
        const api = await fixtures(page, kind);
        try {
            await page.goto(`${base}${kind === "source" ? "" : "/canvas-app"}/canvas/parity-canvas`);
            const node = page.locator('[data-node-id="text-one"]');
            await node.waitFor({ state: "visible", timeout: 90000 });
            await page.locator(".canvas-main-toolbar").waitFor({ state: "visible" });
            const record = async scenario => {
                report.scenarios[scenario] ??= {};
                report.scenarios[scenario][kind] = await capture(page, kind, scenario);
            };
            await page.getByPlaceholder(/创作|描述|输入/).first().waitFor({ state: "attached", timeout: 5000 }).catch(() => {});
            await record("desktop-default");
            const box = await node.boundingBox();
            await page.mouse.click(box.x + 20, box.y + 8);
            await record("node-selected");
            await page.mouse.click(box.x + 20, box.y + 8, { button: "right" });
            await record("node-context-menu");
            await page.keyboard.press("Escape");
            const committed = page.waitForResponse(response => /\/(?:ops\/canvas.document.commit|commits)$/.test(new URL(response.url()).pathname) && response.ok());
            await page.mouse.move(box.x + 20, box.y + 8);
            await page.mouse.down();
            await page.mouse.move(box.x + 130, box.y + 68, { steps: 12 });
            await page.mouse.up();
            await committed;
            await page.mouse.move(900, 600);
            await record("node-dragged");
            await page.keyboard.press("Control+z");
            await record("node-undo");
            await page.getByRole("button", { name: "画布外观", exact: true }).click();
            await page.mouse.move(900, 600);
            await record("appearance-dark");
            await page.getByRole("button", { name: "切换到浅色主题", exact: true }).click();
            await page.mouse.move(900, 600);
            await record("appearance-light");
            await page.getByRole("dialog", { name: "画布外观" }).locator("label.ant-segmented-item").filter({ hasText: /^线$/ }).click();
            await page.mouse.move(900, 600);
            await record("appearance-lines");
            await page.getByRole("button", { name: "切换到自定义主题", exact: true }).click();
            await page.mouse.move(900, 600);
            await record("appearance-custom");
            await page.getByRole("button", { name: "切换到深色主题", exact: true }).click();
            await page.getByRole("dialog", { name: "画布外观" }).locator("label.ant-segmented-item").filter({ hasText: /^点$/ }).click();
            await page.keyboard.press("Escape");
            await page.getByRole("button", { name: /^打开素材空间，共/ }).click();
            const tray = page.locator("[data-canvas-asset-tray]");
            await tray.getByText("没有匹配的图片素材", { exact: true }).waitFor();
            await page.mouse.move(900, 600);
            await record("asset-tray-library");
            const resize = await tray.getByRole("button", { name: "从顶部调整素材托盘高度", exact: true }).boundingBox();
            await page.mouse.move(resize.x + resize.width / 2, resize.y + resize.height / 2);
            await page.mouse.down();
            await page.mouse.move(resize.x + resize.width / 2, resize.y - 80, { steps: 12 });
            await page.mouse.up();
            await page.mouse.move(900, 600);
            await record("asset-tray-resized");
            await tray.getByRole("button", { name: "当前画布 0", exact: true }).click();
            await page.mouse.move(900, 600);
            await record("asset-tray-canvas");
            await tray.getByRole("button", { name: "收起素材空间", exact: true }).click();
            await page.setViewportSize({ width: 390, height: 844 });
            await record("narrow-default");
            await page.setViewportSize({ width: 1440, height: 900 });
            await page.getByRole("button", { name: "版本记录", exact: true }).click();
            const historyItem = page.locator(".canvas-version-item").filter({ has: page.getByText("v3", { exact: true }) });
            await historyItem.waitFor();
            await page.mouse.move(900, 600);
            await record("history-list");
            await historyItem.click();
            await page.locator('[data-node-id="version-preview:text-one"]').waitFor();
            await page.mouse.move(900, 600);
            await record("history-preview");
            await page.getByRole("button", { name: "关闭版本记录", exact: true }).click();
            await page.mouse.click(380, 660, { button: "right" });
            await page.getByRole("button", { name: "添加节点", exact: true }).last().click();
            await page.getByRole("button", { name: "搜索节点", exact: true }).click();
            await page.getByRole("textbox", { name: "搜索节点", exact: true }).fill("绘图");
            await record("drawing-create-menu");
            await page.getByRole("button", { name: "绘图", exact: true }).click();
            await page.getByText("打开绘图", { exact: true }).dblclick();
            await page.getByRole("button", { name: "保存绘图", exact: true }).waitFor({ timeout: 60000 });
            await page.waitForFunction(() => [...document.querySelectorAll("button")].some(button => button.textContent === "保存绘图" && !button.disabled));
            await page.mouse.move(900, 600);
            await record("drawing-editor-empty");
            report.unimplementedFixtures[kind] = [...api.unknown];
            report.scenarios["node-dragged"][`${kind}DocumentPosition`] = api.document().nodes[0].position;
        } catch (error) {
            await page.screenshot({ path: resolve(output, `failure.${kind}.png`) }).catch(() => {});
            report.errors[kind].push(error.message, await page.locator("body").innerText().catch(() => ""));
            throw error;
        } finally {
            report.unimplementedFixtures[kind] = [...api.unknown];
            await context.close();
        }
    }
    for (const scenario of Object.values(report.scenarios)) {
        scenario.geometryEqual = JSON.stringify(scenario.source) === JSON.stringify(scenario.target);
    }
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}

const python = resolve(root, "../../backend/.venv/Scripts/python.exe");
const comparison = execFileSync(python, ["-c", `
import json, sys
from pathlib import Path
from PIL import Image, ImageChops
root = Path(sys.argv[1])
report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
for name, result in report['scenarios'].items():
    source = Image.open(root / (name + '.source.png')).convert('RGB')
    target = Image.open(root / (name + '.target.png')).convert('RGB')
    diff = ImageChops.difference(source, target)
    diff.save(root / (name + '.diff.png'))
    pixels = diff.tobytes()
    changed = sum(any(pixels[index:index + 3]) for index in range(0, len(pixels), 3))
    result['changedPixels'] = changed
    result['totalPixels'] = source.width * source.height
    result['pixelEqual'] = changed == 0
(root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')
sys.stdout.write(json.dumps({name: {'geometryEqual': result['geometryEqual'], 'changedPixels': result['changedPixels']} for name, result in report['scenarios'].items()}))
`, output], { encoding: "utf8" });
process.stdout.write(comparison + "\n");
const compared = JSON.parse(comparison);
for (const [name, result] of Object.entries(compared)) {
    assert.equal(result.geometryEqual, true, `${name}: geometry differs; inspect .runtime/parity/report.json`);
    assert.equal(result.changedPixels, 0, `${name}: screenshot differs; inspect .runtime/parity/*.diff.png`);
}
assert.deepEqual(report.errors, { source: [], target: [] });
assert.deepEqual(report.unimplementedFixtures, { source: [], target: [] });
