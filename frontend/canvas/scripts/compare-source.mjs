import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
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
const depthTitleSelected = process.argv.includes("--depth-title-selected");
const depthTitleDrag = process.argv.includes("--depth-title-drag") || depthTitleSelected;
const narrowDrawingSize = process.argv.includes("--drawing-size-narrow");
const drawingSizeCheck = process.argv.includes("--drawing-size-check");
const drawingLoadBoundaries = process.argv.includes("--drawing-load-boundaries");
const drawingSize = process.argv.includes("--drawing-size") || narrowDrawingSize || drawingSizeCheck || drawingLoadBoundaries;
assert.ok(!(drawingSize && depthTitleDrag), "绘图尺寸和标题拖拽专项须分别执行");
const closeSourceAssistant = process.argv.includes("--closed-assistant") || depthTitleDrag || drawingSize;
const commit = execFileSync("git", ["-C", sourceRoot, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
assert.equal(commit, "4ca2a65a7780a8dfcaaa86c33679f84fb04e055c");
assert.equal(execFileSync("git", ["-C", sourceRoot, "status", "--porcelain"], { encoding: "utf8" }).trim(), "", "Source baseline must be clean");
const output = resolve(root, process.env.BEEFTV_COMPARE_OUTPUT_DIR || `${reducedMotion === "reduce" ? ".runtime/parity" : ".runtime/parity-normal-motion"}${depthTitleDrag ? "-depth-title-drag" : closeSourceAssistant ? "-closed-assistant" : ""}`);
await mkdir(output, { recursive: true });
const productionModalPath = resolve(root, "src/components/canvas/canvas-drawing-editor-modal.tsx");
const modalCandidatePath = process.env.BEEFTV_DRAWING_MODAL_CANDIDATE ? resolve(root, process.env.BEEFTV_DRAWING_MODAL_CANDIDATE) : null;
assert.ok(!modalCandidatePath || drawingSize, "隔离绘图候选只能用于绘图尺寸专项");
const modalCandidate = modalCandidatePath ? await readFile(modalCandidatePath, "utf8") : null;

const pendingDepthTask = { id: "9007199254741011", projectId: "parity-canvas", type: "depth_capture",
    operation: "depth_capture", status: "running", stage: "正在生成深度视频", progress: 0, prompt: "", attempts: 1,
    createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z" };
const initial = {
    id: "parity-canvas", workspaceProjectId: "9007199254740995", revision: 1,
    title: "迁移对照", canvasTitle: "主画布", createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z",
    nodes: [{ id: "text-one", type: "text", title: "文字节点", position: { x: -110, y: -190 }, width: 320, height: 220, metadata: { content: "画布迁移操作验证" } }],
    connections: [], chatSessions: [], activeChatId: null, directorScenes: [],
    viewport: { x: 0, y: 0, k: 1 }, backgroundMode: "dots", showImageInfo: false,
};
if (depthTitleDrag) {
    initial.nodes = [
        { id: "video-parent", type: "video", title: "深度捕捉来源", position: { x: -400, y: -120 }, width: 320, height: 180, metadata: {} },
        { id: "text-one", type: "video", title: "处理中测试节点", position: { x: 80, y: -120 }, width: 400, height: 225, metadata: { status: "loading", depthSourceNodeId: "video-parent", taskId: pendingDepthTask.id,
            taskStatus: pendingDepthTask.status, taskStage: pendingDepthTask.stage, processingLabel: pendingDepthTask.stage } },
    ];
}

async function fixtures(page, kind) {
    let document = { ...structuredClone(initial), revision: kind === "source" ? 1 : "1" };
    const historyProject = { ...structuredClone(initial), revision: kind === "source" ? 3 : "3" };
    const history = { id: "101", canvasId: initial.id, revision: kind === "source" ? 3 : "3", title: initial.title, nodeCount: 1, connectionCount: 0, payloadBytes: 1024, reason: "automatic", createdAt: initial.createdAt, contentUpdatedAt: initial.updatedAt };
    const historyDto = { id: history.id, canvas_id: "9007199254740993", row_version: "3", title: history.title, node_count: 1, connection_count: 0, payload_bytes: 1024, reason: history.reason, created_at: history.createdAt, content_updated_at: history.contentUpdatedAt };
    const unknown = new Set();
    const mutations = [];
    const drawings = new Map();
    const drawingRequests = [];
    let drawingReadGate;
    let drawingReadFailure = false;
    const pendingDrawingReads = new Set();
    const resumeDrawingReads = () => {
        drawingReadGate?.release();
        drawingReadGate = undefined;
    };
    let taskReads = 0;
    const summary = () => ({ id: "9007199254740993", source_key: document.id, project_id: document.workspaceProjectId,
        title: document.title, row_version: String(document.revision), schema_version: 1, created_at: document.createdAt, updated_at: document.updatedAt });
    await page.route(url => url.pathname.startsWith("/api/"), async route => {
        const request = route.request();
        const pathname = new URL(request.url()).pathname;
        const path = pathname.replace(/^\/api(?:\/v1(?:\/canvas-runtime)?)?/, "");
        if (!["GET", "HEAD", "OPTIONS"].includes(request.method())) {
            mutations.push({ method: request.method(), path });
        }
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
        else if (depthTitleDrag && path === `/tasks/${pendingDepthTask.id}` && request.method() === "GET") {
            taskReads++;
            body = pendingDepthTask;
        }
        else if (path === "/tasks" && request.method() === "GET") body = { tasks: depthTitleDrag ? [pendingDepthTask] : [], total: depthTitleDrag ? 1 : 0, page: 1, pageSize: 50, hasMore: false };
        else if (path === "/assets" && request.method() === "GET") body = { assets: [], page: 1, pageSize: 100, total: 0, hasMore: false };
        else if (path.endsWith("/history")) body = { snapshots: [history], currentRevision: 7 };
        else if (path.endsWith("/revisions")) body = { items: [historyDto], row_version: "7", drawing_heads: {} };
        else if (path.endsWith("/history/101")) body = { snapshot: history, project: historyProject };
        else if (path.endsWith("/revisions/101")) body = { revision: historyDto, source_document: historyProject };
        else if (/^\/canvas-projects\/[^/]+\/drawings\/[^/]+$/.test(path) && (request.method() === "GET" || (drawingSize && request.method() === "PUT"))) {
            const drawingId = decodeURIComponent(path.split("/").at(-1));
            if (request.method() === "PUT") {
                const input = request.postDataJSON().drawing;
                assert.equal(kind, "target", "源 browser-local 绘图须沿原 IndexedDB 保存");
                assert.equal(input.drawingId, drawingId);
                assert.equal(input.engine, "excalidraw");
                assert.equal(input.revision, drawings.get(drawingId)?.revision ?? "0");
                const drawing = { ...input, revision: String(BigInt(input.revision) + 1n), createdAt: initial.createdAt, updatedAt: initial.updatedAt };
                drawings.set(drawingId, drawing);
                drawingRequests.push({ method: "PUT", drawingId, revision: drawing.revision, shapeCount: drawing.shapeCount, pageCount: drawing.pageCount });
            } else if (drawingSize) {
                const pending = {};
                pendingDrawingReads.add(pending);
                try {
                    if (drawingReadGate) await drawingReadGate.promise;
                    if (drawingReadFailure) {
                        drawingRequests.push({ method: "GET", drawingId, failed: true });
                        await route.fulfill({ status: 503, json: { error: { code: "drawing_probe_failure", message: "绘图诊断受控读取失败" } } });
                        return;
                    }
                } finally {
                    pendingDrawingReads.delete(pending);
                }
                drawingRequests.push({ method: "GET", drawingId, found: drawings.has(drawingId), revision: drawings.get(drawingId)?.revision ?? null });
            }
            if (!drawings.has(drawingId)) {
                await route.fulfill({ status: 404, json: kind === "source" ? { code: 404, reason: "not_found", msg: "绘图不存在" }
                    : { error: { code: "not_found", message: "绘图不存在" } } });
                return;
            }
            body = { drawing: drawings.get(drawingId) };
        }
        else {
            unknown.add(`${request.method()} ${path}`);
            await route.fulfill({ status: 404, json: kind === "source" ? { code: 404, reason: "not_found", msg: "Fixture does not implement this feature" }
                : { error: { code: "not_found", message: "Fixture does not implement this feature" } } });
            return;
        }
        await route.fulfill({ json: kind === "source" ? { code: 0, data: body, msg: "ok" } : body });
    });
    return {
        unknown, mutations, drawingRequests, taskReads: () => taskReads, document: () => document,
        setDrawingReadFailure: value => { drawingReadFailure = value; },
        pauseDrawingReads: () => {
            assert.equal(drawingReadGate, undefined, "不能覆盖尚未释放的绘图读取门");
            let release;
            const promise = new Promise(resolve => { release = resolve; });
            drawingReadGate = { promise, release };
        },
        resumeDrawingReads,
        pendingDrawingReads: () => pendingDrawingReads.size,
    };
}

async function startDrawingSizeSamples(page) {
    await page.evaluate(() => {
        window.__beeftvDrawingSizeSamples = [];
        window.__beeftvDrawingSizeSampling = true;
        let previous = "";
        const startedAt = performance.now();
        const frame = () => {
            if (!window.__beeftvDrawingSizeSampling) return;
            const modal = document.querySelector(".canvas-drawing-editor-modal");
            const editor = modal?.querySelector(".excalidraw");
            const rect = element => {
                if (!element) return null;
                const box = element.getBoundingClientRect();
                return { x: box.x, y: box.y, width: box.width, height: box.height };
            };
            const sample = {
                modal: rect(modal), modalTransform: modal ? getComputedStyle(modal).transform : null,
                editor: rect(editor),
                editorLayout: editor ? { width: editor.clientWidth, height: editor.clientHeight } : null,
                dpr: devicePixelRatio,
                canvases: [...(editor?.querySelectorAll("canvas") ?? [])].map(canvas => {
                    const style = getComputedStyle(canvas);
                    return { className: canvas.className, rect: rect(canvas), cssWidth: Number.parseFloat(style.width), cssHeight: Number.parseFloat(style.height), bitmapWidth: canvas.width, bitmapHeight: canvas.height };
                }),
            };
            const signature = JSON.stringify(sample);
            if (signature !== previous) {
                window.__beeftvDrawingSizeSamples.push({ elapsedMs: performance.now() - startedAt, ...sample });
                previous = signature;
            }
            requestAnimationFrame(frame);
        };
        requestAnimationFrame(frame);
    });
}

async function finishDrawingSizeSamples(page) {
    return page.evaluate(() => {
        window.__beeftvDrawingSizeSampling = false;
        const samples = window.__beeftvDrawingSizeSamples;
        const final = samples.at(-1);
        if (!final?.editor || !final.canvases.length) throw new Error("绘图尺寸诊断未采到实际内部 canvas");
        return {
            samples,
            final,
            finalCssMatchesEditor: final.canvases.every(canvas => canvas.cssWidth === final.editor.width && canvas.cssHeight === final.editor.height),
            finalBitmapMatchesEditor: final.canvases.every(canvas => canvas.bitmapWidth === Math.floor(final.editor.width * final.dpr) && canvas.bitmapHeight === Math.floor(final.editor.height * final.dpr)),
        };
    });
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
    if (depthTitleDrag) {
        // The nonterminal-task fixture keeps its spinner animated. This functional scenario
        // retains one full frame for diagnosis; the 17 static scenarios remain strict.
        captured = await page.screenshot({ caret: "hide" });
    } else {
        for (let attempt = 0; attempt < 12; attempt++) {
            captured = await page.screenshot({ caret: "hide" });
            stableFrames = previous?.equals(captured) ? stableFrames + 1 : 0;
            if (stableFrames >= 2) break;
            previous = captured;
            await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        }
        assert.ok(stableFrames >= 2, `${kind}/${scenario}: screenshot pixels did not settle`);
    }
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

const server = await createServer({
    root,
    server: { host: "127.0.0.1", port: 4184, strictPort: true },
    logLevel: "error",
    plugins: modalCandidate ? [{
        name: "isolated-drawing-modal-candidate",
        enforce: "pre",
        load(id) {
            if (resolve(id.split("?")[0]).toLowerCase() === productionModalPath.toLowerCase()) return modalCandidate;
        },
    }] : [],
});
await server.listen();
let browser;
const report = { commit, scope: "Controlled fixtures: editor layout and recorded pointer/keyboard operations only; no provider or server acceptance", browser: "", viewport: { width: narrowDrawingSize ? 1280 : 1440, height: 900 }, dpr: 1, colorScheme: "dark", reducedMotion, scenarios: {}, errors: {}, unimplementedFixtures: {} };
report.source_assistant_closed_for_comparison = closeSourceAssistant;
report.depth_title_drag = {};
report.depth_title_selected = depthTitleSelected;
report.depth_title_drag_attempts = {};
report.mutationRequests = {};
report.pixel_acceptance_required = !depthTitleDrag && !drawingSize;
if (depthTitleDrag) report.scope = "Controlled nonterminal task GET fixture: visible title-icon hit testing, saved drag and geometry only; full pixel differences retained, no visual parity or server/provider acceptance";
if (drawingSize) {
    report.scope = "Drawing sizing diagnosis only: first open and original save/close/reopen, passive DOM/CSS/bitmap samples and full pixel differences; no visual parity or server/provider acceptance";
    report.drawing_size_diagnosis = {};
    report.drawingRequests = {};
    report.drawing_size_acceptance_required = drawingSizeCheck;
    report.drawing_size_acceptance_scope = "Target internal dimensions only; fixed-source faults and full pixel differences retained";
    report.drawing_load_boundaries = {};
    report.target_modal_candidate = modalCandidate ? {
        path: modalCandidatePath,
        sha256: createHash("sha256").update(modalCandidate).digest("hex"),
        productionSha256: createHash("sha256").update(await readFile(productionModalPath)).digest("hex"),
        productionFileModified: false,
    } : null;
}
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
        if (depthTitleDrag) await page.exposeFunction("__beeftvPersistedTitleNode", () => api.document().nodes.find(item => item.id === "text-one"));
        try {
            await page.goto(`${base}${kind === "source" ? "" : "/canvas-app"}/canvas/parity-canvas`);
            const node = page.locator('[data-node-id="text-one"]');
            await node.waitFor({ state: "visible", timeout: 90000 });
            if (kind === "source" && closeSourceAssistant) {
                const assistant = page.getByRole("button", { name: "助手", exact: true });
                assert.equal(await assistant.getAttribute("aria-pressed"), "true");
                    await page.getByRole("button", { name: "关闭助手", exact: true }).click();
                    assert.equal(await assistant.getAttribute("aria-pressed"), "false");
            }
            await page.locator(narrowDrawingSize ? "#canvas-main" : ".canvas-main-toolbar").waitFor({ state: "visible" });
            const record = async scenario => {
                report.scenarios[scenario] ??= {};
                report.scenarios[scenario][kind] = await capture(page, kind, scenario);
            };
            if (drawingSize) {
                report.drawing_size_diagnosis[kind] = {};
                await settle(page);
                await page.mouse.click(report.viewport.width / 2, 660, { button: "right" });
                await page.getByRole("button", { name: "添加节点", exact: true }).last().click();
                await page.getByRole("button", { name: "搜索节点", exact: true }).click();
                await page.getByRole("textbox", { name: "搜索节点", exact: true }).fill("绘图");
                await page.getByRole("button", { name: "绘图", exact: true }).click();
                if (drawingLoadBoundaries && kind === "target") {
                    await page.exposeFunction("__beeftvPendingDrawingReads", () => api.pendingDrawingReads());
                    const modal = page.locator(".canvas-drawing-editor-modal");
                    const save = page.getByRole("button", { name: "保存绘图", exact: true });
                    report.drawing_load_boundaries.target = { delayedOpens: [] };
                    api.setDrawingReadFailure(true);
                    await page.getByText("打开绘图", { exact: true }).dblclick();
                    await page.getByText("绘图无法打开", { exact: true }).waitFor();
                    assert.equal(await modal.locator(".excalidraw").count(), 0);
                    assert.equal(await save.isDisabled(), true);
                    await page.screenshot({ path: resolve(output, "drawing-load-error.target.png"), caret: "hide" });
                    await page.getByRole("button", { name: "关闭绘图编辑器", exact: true }).click();
                    await modal.waitFor({ state: "hidden" });
                    api.setDrawingReadFailure(false);
                    report.drawing_load_boundaries.target.loadError = { shown: true, editorMounted: false, saveDisabled: true };
                    api.pauseDrawingReads();
                    await page.getByText("打开绘图", { exact: true }).dblclick();
                    await save.waitFor({ state: "visible" });
                    await page.waitForFunction(async () => (await window.__beeftvPendingDrawingReads()) > 0);
                    assert.equal(await save.isDisabled(), true);
                    assert.equal(await modal.locator(".excalidraw").count(), 0);
                    await page.getByRole("button", { name: "关闭绘图编辑器", exact: true }).click();
                    await modal.waitFor({ state: "hidden" });
                    api.resumeDrawingReads();
                    await page.waitForFunction(async () => (await window.__beeftvPendingDrawingReads()) === 0);
                    await settle(page);
                    assert.equal(await modal.isVisible(), false, "关闭后的旧读取响应不能重开弹窗");
                    assert.equal(api.drawingRequests.filter(request => request.method === "PUT").length, 0, "失败及未就绪关闭不能保存空文档");
                    report.drawing_load_boundaries.target.closeWhileLoading = { staleResponseReopened: false, writes: 0 };
                }
                for (const state of ["first", "reopened"]) {
                    if (drawingLoadBoundaries && kind === "target") api.pauseDrawingReads();
                    await startDrawingSizeSamples(page);
                    await page.getByText("打开绘图", { exact: true }).dblclick();
                    const save = page.getByRole("button", { name: "保存绘图", exact: true });
                    await save.waitFor({ state: "visible", timeout: 60000 });
                    if (drawingLoadBoundaries && kind === "target") {
                        await page.waitForFunction(async () => (await window.__beeftvPendingDrawingReads()) > 0);
                        await settle(page);
                        const waiting = await page.locator(".canvas-drawing-editor-modal").evaluate(modal => ({
                            transform: getComputedStyle(modal).transform,
                            editorMounted: Boolean(modal.querySelector(".excalidraw")),
                        }));
                        assert.deepEqual(waiting, { transform: "none", editorMounted: false });
                        assert.equal(await save.isDisabled(), true);
                        report.drawing_load_boundaries.target.delayedOpens.push({ state, ...waiting, saveDisabled: true });
                        api.resumeDrawingReads();
                    }
                    await page.waitForFunction(() => [...document.querySelectorAll("button")].some(button => button.textContent === "保存绘图" && !button.disabled), undefined, { timeout: 60000 });
                    await page.mouse.move(12, report.viewport.height - 12);
                    await record(`drawing-size-${state}`);
                    const result = await finishDrawingSizeSamples(page);
                    report.drawing_size_diagnosis[kind][state] = result;
                    assert.equal(result.final.canvases.length, 2, "须采到原静态和交互两个内部 canvas");
                    assert.equal(result.final.modalTransform, "none", "最终尺寸须在弹窗动效结束后采集");
                    await page.getByRole("button", { name: "关闭绘图编辑器", exact: true }).click();
                    await save.waitFor({ state: "hidden", timeout: 30000 });
                    await settle(page);
                }
                report.drawingRequests[kind] = api.drawingRequests;
                if (kind === "target") {
                    const saves = api.drawingRequests.filter(request => request.method === "PUT");
                    assert.deepEqual(saves.map(request => request.revision), ["1", "2"], "两次原关闭须分别保存版本1和2，不能有额外写入");
                    assert.equal(new Set(api.drawingRequests.map(request => request.drawingId)).size, 1, "重开须读取同一绘图");
                    const firstSave = api.drawingRequests.indexOf(saves[0]);
                    const secondSave = api.drawingRequests.indexOf(saves[1]);
                    const firstReads = api.drawingRequests.slice(0, firstSave).filter(request => !request.failed);
                    const reopenedReads = api.drawingRequests.slice(firstSave + 1, secondSave);
                    assert.ok(firstReads.length && firstReads.every(request => request.method === "GET" && request.found === false && request.revision === null));
                    assert.ok(reopenedReads.length && reopenedReads.every(request => request.method === "GET" && request.found === true && request.revision === "1"), "原预览及重开均须读取已保存的版本1");
                    assert.ok(saves.every(request => request.shapeCount === 0 && request.pageCount === 1));
                }
                assert.ok(api.mutations.every(request => ["/ops/canvas.document.commit", "/assistant/ui-session"].includes(request.path)
                    || /\/(commits|viewport|view-preferences)$/.test(request.path)
                    || /^\/canvas-projects\/[^/]+\/drawings\/[^/]+$/.test(request.path)), "尺寸诊断不得提交生成或上传媒体");
                continue;
            }
            if (depthTitleDrag) {
                if (depthTitleSelected) await node.click({ position: { x: 12, y: 36 } });
                await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
                await page.getByRole("button", { name: kind === "source" ? "画布保存状态：已保存在本地" : "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
                await page.waitForFunction(async () => {
                    const persisted = await window.__beeftvPersistedTitleNode();
                    const element = document.querySelector('[data-node-id="text-one"]');
                    if (!persisted || !element) return false;
                    const transform = new DOMMatrix(element.style.transform);
                    return transform.e === persisted.position.x && transform.f === persisted.position.y
                        && Number.parseFloat(element.style.width) === persisted.width
                        && Number.parseFloat(element.style.height) === persisted.height;
                }, undefined, { timeout: 15000 });
                await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
                await record("depth-loading-fit");
                const icon = node.locator(".canvas-node-external-header > div > svg");
                assert.equal(await icon.count(), 1, "只选择原可见类型图标，不选择隐藏拖动按钮内的SVG");
                const landing = await icon.evaluate((element, selected) => {
                    const box = element.getBoundingClientRect();
                    const describe = (x, y) => {
                        const hit = document.elementFromPoint(x, y);
                        return { x, y, width: box.width, height: box.height, node_id: hit?.closest("[data-node-id]")?.getAttribute("data-node-id"), button: hit?.closest("button")?.getAttribute("aria-label") ?? null, tag: hit?.tagName,
                            cursor: hit ? getComputedStyle(hit).cursor : null, header: Boolean(hit?.closest(".canvas-node-external-header")), icon: hit === element || element.contains(hit) };
                    };
                    const center = describe(box.x + box.width / 2, box.y + box.height / 2);
                    if (!selected) return center;
                    const candidates = [0.25, 0.125, 0.375, 0.5, 0.625, 0.875].flatMap(y => [0.5, 0.25, 0.75].map(x => describe(box.x + box.width * x, box.y + box.height * y)));
                    const exposed = candidates.find(hit => hit.icon && hit.header && hit.button === null);
                    return { ...exposed, center_hit: center, candidates };
                }, depthTitleSelected);
                if (depthTitleSelected) {
                    assert.equal(landing.center_hit.cursor, "nwse-resize");
                    assert.equal(landing.center_hit.header, false);
                    assert.equal(landing.center_hit.icon, false);
                    assert.equal(landing.icon, true, "必须从可见SVG未被缩放手柄覆盖的部分开始拖拽");
                }
                assert.equal(landing.node_id, "text-one");
                assert.equal(landing.button, null);
                assert.ok(landing.width > 0 && landing.height > 0);
                const before = structuredClone(api.document().nodes.find(item => item.id === "text-one").position);
                report.depth_title_drag_attempts[kind] = { before, landing };
                await page.evaluate(() => {
                    window.__beeftvTitlePointerEvents = [];
                    for (const type of ["pointerdown", "mousedown", "mouseup"]) {
                        document.addEventListener(type, event => {
                            const target = event.target;
                            const item = { type, x: event.clientX, y: event.clientY, tag: target?.tagName,
                                node_id: target?.closest?.("[data-node-id]")?.getAttribute("data-node-id") ?? null,
                                button: target?.closest?.("button")?.getAttribute("aria-label") ?? null,
                                icon: Boolean(target?.closest?.(".canvas-node-external-header > div > svg")) };
                            window.__beeftvTitlePointerEvents.push(item);
                            queueMicrotask(() => { item.default_prevented = event.defaultPrevented; });
                        }, { capture: true });
                    }
                });
                const committed = page.waitForResponse(response => /\/(?:ops\/canvas.document.commit|commits)$/.test(new URL(response.url()).pathname) && response.ok(), { timeout: 15000 });
                await page.mouse.move(landing.x, landing.y);
                await page.mouse.down();
                await page.mouse.move(landing.x + 120, landing.y + 40, { steps: 12 });
                await page.mouse.up();
                await committed;
                const pointerEvents = await page.evaluate(() => window.__beeftvTitlePointerEvents);
                assert.deepEqual(pointerEvents.slice(0, 2).map(event => event.type), ["pointerdown", "mousedown"]);
                assert.ok(pointerEvents.slice(0, 2).every(event => event.icon && event.node_id === "text-one" && event.button === null));
                await page.mouse.move(1000, 700);
                await record("depth-title-dragged");
                const after = structuredClone(api.document().nodes.find(item => item.id === "text-one").position);
                const delta = { x: after.x - before.x, y: after.y - before.y };
                assert.ok(Math.hypot(delta.x, delta.y) > 20);
                assert.equal(api.document().nodes.find(item => item.id === "text-one").metadata.status, "loading");
                assert.ok(api.taskReads() > 0, "原恢复路径必须实际读取非终态任务夹具");
                const unexpectedMutations = api.mutations.filter(({ path }) => path !== "/ops/canvas.document.commit"
                    && path !== "/assistant/ui-session"
                    && !/\/(commits|viewport|view-preferences)$/.test(path));
                assert.deepEqual(unexpectedMutations, [], "标题对照只允许作品/视图提交及源助手会话初始化");
                report.depth_title_drag[kind] = { before, after, delta, landing, mutation_requests: api.mutations,
                    unexpected_mutations: unexpectedMutations, task_get_count: api.taskReads() };
                continue;
            }
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
            await page.setViewportSize({ width: 1280, height: 720 });
            await record("desktop-minimum");
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
            report.mutationRequests[kind] = api.mutations;
            if (drawingSize) {
                api.resumeDrawingReads();
                report.drawingRequests[kind] = api.drawingRequests;
                await page.evaluate(() => { window.__beeftvDrawingSizeSampling = false; }).catch(() => {});
            }
            if (depthTitleDrag && report.depth_title_drag_attempts[kind]) {
                report.depth_title_drag_attempts[kind].events = await page.evaluate(() => window.__beeftvTitlePointerEvents);
                report.depth_title_drag_attempts[kind].current_node = api.document().nodes.find(item => item.id === "text-one");
            }
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
    if (!drawingSize) assert.equal(result.geometryEqual, true, `${name}: geometry differs; inspect ${output}/report.json`);
    if (!depthTitleDrag && !drawingSize) assert.equal(result.changedPixels, 0, `${name}: screenshot differs; inspect ${output}/*.diff.png`);
}
if (depthTitleDrag) assert.deepEqual(report.depth_title_drag.source.delta, report.depth_title_drag.target.delta, "可见标题图标两端的实际拖拽位移不同");
if (drawingSizeCheck) {
    for (const [state, result] of Object.entries(report.drawing_size_diagnosis.target)) {
        assert.equal(result.finalCssMatchesEditor, true, `target/${state}: 内部 canvas CSS 尺寸与实际编辑器不一致`);
        assert.equal(result.finalBitmapMatchesEditor, true, `target/${state}: 内部 canvas bitmap 尺寸与实际编辑器不一致`);
    }
}
assert.deepEqual(report.errors, { source: [], target: [] });
assert.deepEqual(report.unimplementedFixtures, { source: [], target: [] });
