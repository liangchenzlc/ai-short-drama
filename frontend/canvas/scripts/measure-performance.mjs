import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { cpus, platform, totalmem } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "playwright";

// 真实生产编辑器 + 隔离HTTP夹具；不测Python、数据库、供应商或GPU呈现帧。
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const frontend = resolve(root, "..");
const args = process.argv.slice(2);
const option = name => args.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3);
const counts = (option("nodes") || "100,1000,5000").split(",").map(Number);
const rounds = Number(option("rounds") || 3);
assert.ok(counts.length && counts.every(count => Number.isSafeInteger(count) && count >= 20 && count <= 5000));
assert.ok(Number.isSafeInteger(rounds) && rounds >= 1 && rounds <= 10);
const profileEnabled = args.includes("--profile");
const arrangeEnabled = args.includes("--arrange");
const graphKind = option("graph") || "grid";
assert.ok(["grid", "chain", "fan"].includes(graphKind));
const output = resolve(option("output") || resolve(root, ".runtime/performance", new Date().toISOString().replace(/[:.]/g, "-")));
const sourceRoot = process.env.BEEFTV_ROOT ? resolve(process.env.BEEFTV_ROOT) : null;
const manifest = JSON.parse(await readFile(resolve(root, "source-manifest.json"), "utf8"));
if (sourceRoot) assert.equal(execFileSync("git", ["-C", sourceRoot, "rev-parse", "HEAD"], { encoding: "utf8", windowsHide: true }).trim(), manifest.commit, "源生产产物必须来自固定commit");
await mkdir(output, { recursive: true });
const mediaDir = process.env.CANVAS_PERF_MEDIA_DIR ? resolve(process.env.CANVAS_PERF_MEDIA_DIR) : resolve(output, "media");
if (!process.env.CANVAS_PERF_MEDIA_DIR) {
    await mkdir(mediaDir, { recursive: true });
    const ffmpeg = process.env.FFMPEG_PATH || "ffmpeg";
    execFileSync(ffmpeg, ["-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-t", "6", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", resolve(mediaDir, "sample.mp4")], { windowsHide: true });
    execFileSync(ffmpeg, ["-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-frames:v", "1", "-y", resolve(mediaDir, "sample.png")], { windowsHide: true });
}
const digest = value => createHash("sha256").update(value).digest("hex");
const mediaHashes = Object.fromEntries(await Promise.all(["sample.png", "sample.mp4"].map(async name => [name, digest(await readFile(resolve(mediaDir, name)))])));
const report = { scope: "生产浏览器/受控HTTP夹具；未验Python、SQL、MinIO、AMQP、供应商、GPU呈现帧或泄漏", commit: manifest.commit, driver_sha256: digest(await readFile(fileURLToPath(import.meta.url))), node_counts: counts, rounds, started_at: new Date().toISOString(), profile_enabled: profileEnabled, arrange_enabled: arrangeEnabled, graph: graphKind, frame_budget_ms: 32, viewport: { width: 1440, height: 900 }, dpr: 1, color_scheme: "dark", reduced_motion: "no-preference", machine: { platform: platform(), cpu: cpus()[0]?.model, logical_processors: cpus().length, ram_bytes: totalmem(), node: process.versions.node }, media_sha256: mediaHashes, cases: [], status: "running" };
const servers = [];
let browser;

function stats(values) {
    const sorted = [...values].sort((left, right) => left - right);
    return { samples: sorted.length, p50_ms: sorted[Math.max(0, Math.ceil(sorted.length * .5) - 1)] || 0, p95_ms: sorted[Math.max(0, Math.ceil(sorted.length * .95) - 1)] || 0, max_ms: sorted.at(-1) || 0 };
}

function scene(count, kind) {
    const resources = new Map();
    const nodes = Array.from({ length: count }, (_, index) => {
        const type = index % 10 === 9 ? "frame" : ["text", "image", "video", "text"][index % 4];
        const node = { id: `node-${index}`, type, title: `性能样本 ${index}`, position: { x: 60 + index % 10 * 300, y: 180 + Math.floor(index / 10) * 230 }, width: 240, height: 160, metadata: { content: `固定文字样本 ${index}` } };
        if (index % 10 === 8) node.parentId = `node-${index + 1}`;
        if (type === "frame") Object.assign(node, { position: { x: node.position.x - 315, y: node.position.y - 36 }, width: 270, height: 210, metadata: { frame: { collapsed: false } } });
        if (type === "image" || type === "video") {
            const id = String(9007199254750000n + BigInt(index) * 2n), previewId = String(BigInt(id) + 1n);
            resources.set(id, type); resources.set(previewId, "image");
            const url = resourceId => `${kind === "source" ? "/api" : "/api/v1/canvas-runtime"}/resources/${resourceId}/file`;
            node.metadata = { content: url(id), storageKey: `resource:${id}`, mediaRole: "result", mediaOrigin: "upload", width: 320, height: 180, naturalWidth: 320, naturalHeight: 180, manualSize: true, mimeType: type === "video" ? "video/mp4" : "image/png" };
            if (type === "video") Object.assign(node.metadata, { hasAudio: false, durationMs: 6000, videoPreview: { content: url(previewId), storageKey: `resource:${previewId}`, sourceKey: `resource:${id}`, width: 320, height: 180, mimeType: "image/png", captureVersion: 2 } });
        }
        return node;
    });
    const candidates = nodes.filter(node => node.type !== "frame" && !node.parentId);
    const connections = graphKind === "grid" ? [] : candidates.slice(1).map((node, index) => ({ id: `connection-${index}`, fromNodeId: candidates[graphKind === "fan" ? 0 : index].id, toNodeId: node.id }));
    return { resources, document: { id: `performance-${count}`, workspaceProjectId: "9007199254740995", revision: kind === "source" ? 1 : "1", title: "组合生产大图基准", canvasTitle: "主画布", createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z", nodes, connections, chatSessions: [], activeChatId: null, directorScenes: [], viewport: { x: 1, y: 1, k: 1 }, backgroundMode: "dots", showImageInfo: false } };
}

async function fixture(page, count, kind) {
    const initial = scene(count, kind);
    let document = initial.document;
    const errors = { unknown_routes: [], page_errors: [], http_errors: [], fixture_errors: [] };
    const commits = [], viewportWrites = [], images = [], videos = [];
    page.on("pageerror", error => errors.page_errors.push(error.stack || error.message));
    page.on("response", response => { if (response.status() >= 400) errors.http_errors.push({ path: new URL(response.url()).pathname, status: response.status() }); });
    const summary = () => ({ id: "9007199254740993", source_key: document.id, project_id: document.workspaceProjectId, title: document.title, row_version: String(document.revision), schema_version: 1, created_at: document.createdAt, updated_at: document.updatedAt });
    await page.route(url => url.pathname.startsWith("/api/"), async route => {
        const request = route.request(), path = new URL(request.url()).pathname.replace(/^\/api(?:\/v1(?:\/canvas-runtime)?)?/, ""), method = request.method();
        try {
            const media = /^\/resources\/(\d+)\/file$/.exec(path);
            if (media && initial.resources.has(media[1])) {
                const video = initial.resources.get(media[1]) === "video";
                (video ? videos : images).push(media[1]);
                await route.fulfill({ path: resolve(mediaDir, video ? "sample.mp4" : "sample.png"), contentType: video ? "video/mp4" : "image/png" });
                return;
            }
            let body;
            if (path === "/auth/me") body = { user: { id: "111", username: "performance-fixture", display_name: "性能样本", email: "performance@example.test" } };
            else if (kind === "source" && path === "/workspace/bootstrap") body = { contractVersion: 1, profile: "local", storageMode: "local", capabilities: { localAssets: true, providerCalls: true }, user: { id: "111", username: "performance-fixture", displayName: "性能样本", role: "user", status: "active" }, workspace: { id: "111", name: "性能样本", owner: "local", storage: "sqlite" }, features: { shortDramaEnabled: true, taskCenterEnabled: true, customChannelsEnabled: true, frontendModelsEnabled: false, pluginCenterEnabled: true, systemPluginsVisibleToUsers: true } };
            else if (path === "/workspace/model-config" && method === "GET") body = kind === "source" ? { config: { channels: [] }, revision: 0, health: "ready", source: "builtin+local" } : { row_version: "0", preferences: {}, models: [] };
            else if (path === "/assets" && method === "GET") body = kind === "source" ? { assets: [], page: 1, pageSize: 100, total: 0, hasMore: false } : [];
            else if (path === "/tasks" && method === "GET") body = kind === "source" ? { tasks: [], page: 1, pageSize: 50, total: 0, hasMore: false } : [];
            else if (path === "/plugins/status" && method === "GET") {
                const ids = ["prompt-optimizer", "ai-art-critique", "runninghub-workflow-provider"];
                body = { statuses: Object.fromEntries(ids.map(id => [id, "disabled"])), states: Object.fromEntries(ids.map(pluginId => [pluginId, { pluginId, platformAvailable: true, userEnabled: false, userConfigured: false, effectiveEnabled: false, canToggle: true, canConfigure: false }])) };
            }
            else if (kind === "source" && path === "/assistant/status") body = { available: false, reason: "model_not_configured" };
            else if (kind === "source" && path === "/assistant/ui-session" && method === "POST") body = { token: "performance-fixture-only", expiresAt: "2100-01-01T00:00:00Z" };
            else if (kind === "source" && path === "/assistant/sessions") body = { currentSessionId: null, sessions: [] };
            else if (kind === "source" && path === "/assistant/history") body = { sessionId: "", turns: [] };
            else if (path === "/resources/normalize" && method === "POST") {
                const ids = request.postDataJSON().resource_ids;
                assert.ok(Array.isArray(ids) && ids.every(id => initial.resources.has(id)));
                body = { resource_map: Object.fromEntries(ids.map(id => [id, id])), resource_aliases: {} };
            }
            else if (path === `/projects/${document.workspaceProjectId}/assets` && method === "GET") body = { items: [], total: 0, offset: 0, limit: 20 };
            else if (path === `/canvas-projects/${document.id}/events` && method === "GET") { await route.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" }); return; }
            else if (kind === "source" && path === "/canvas-projects" && method === "GET") body = { projects: [{ ...document, nodeCount: count, previewNodes: [] }], page: 1, pageSize: 50, total: 1, hasMore: false };
            else if (kind === "source" && path === `/canvas-projects/${document.id}` && method === "GET") body = { project: document };
            else if (kind === "target" && path === "/canvas-workspace" && method === "GET") body = { items: [{ ...summary(), canvas_title: document.canvasTitle, node_count: count, preview_nodes: [], source_document: document }], page: 1, page_size: 50, total: 1, has_more: false };
            else if (kind === "target" && path === `/canvas-workspace/resolve/${document.id}` && method === "GET") body = summary();
            else if (kind === "target" && path.endsWith("/my-document") && method === "GET") body = { ...summary(), source_document: document };
            else if (kind === "target" && path.endsWith("/view-preferences")) { const input = request.postDataJSON(); document = { ...document, ...input.preferences }; body = { preferences: input.preferences, row_version: "1" }; }
            else if (kind === "target" && path.endsWith("/viewport")) { const input = request.postDataJSON(); assert.deepEqual(input.expected_viewport, document.viewport); document.viewport = input.viewport; viewportWrites.push(input); body = { viewport: document.viewport, preferences: {}, row_version: String(viewportWrites.length) }; }
            else if ((kind === "source" && path === "/ops/canvas.document.commit") || (kind === "target" && path.endsWith("/commits"))) {
                const input = request.postDataJSON();
                assert.equal(kind === "source" ? input.params.expectedRevision : input.expected_row_version, document.revision);
                document = { ...(input.params?.document || input.source_document), viewport: document.viewport, revision: kind === "source" ? document.revision + 1 : String(BigInt(document.revision) + 1n) };
                commits.push(input);
                body = kind === "source" ? { op: "canvas.document.commit", opId: input.opId, replayed: false, revision: document.revision, result: { canvasId: document.id, revision: document.revision, title: document.title, updatedAt: document.updatedAt } } : summary();
            } else { errors.unknown_routes.push(`${method} ${path}`); await route.fulfill({ status: 404, json: { error: { code: "not_found", message: "性能夹具未定义此路由" } } }); return; }
            await route.fulfill({ json: kind === "source" ? { code: 0, data: body, msg: "" } : body });
        } catch (error) {
            errors.fixture_errors.push({ path, error: error.stack || String(error) });
            await route.fulfill({ status: 500, json: { error: { code: "performance_fixture_failed", message: `性能夹具失败：${path}` } } });
        }
    });
    const read = () => ({ count, document_bytes: Buffer.byteLength(JSON.stringify(document)), commits: commits.length, viewport_writes: viewportWrites.length, first_position: { ...document.nodes[0].position }, revision: document.revision, viewport: { ...document.viewport }, image_requests: images.length, unique_images: new Set(images).size, video_requests: videos.length, unique_videos: new Set(videos).size, ...structuredClone(errors) });
    read.layout = () => document.nodes.map(node => ({ id: node.id, position: { ...node.position } })).sort((left, right) => left.id.localeCompare(right.id));
    return read;
}

async function instrument(page) {
    await page.addInitScript(() => {
        window.__canvasPerf = { measuring: false, frames: [], long_tasks: [], loading_long_tasks: [], viewports: [], pointer_hits: [], wheel_events: [] };
        let previous;
        const frame = time => { const state = window.__canvasPerf; if (state.measuring) { if (previous !== undefined) state.frames.push(time - previous); previous = time; } else previous = undefined; requestAnimationFrame(frame); };
        requestAnimationFrame(frame);
        if (PerformanceObserver.supportedEntryTypes.includes("longtask")) new PerformanceObserver(list => { const entries = list.getEntries().map(entry => ({ start_ms: entry.startTime, duration_ms: entry.duration })); window.__canvasPerf.loading_long_tasks.push(...entries); if (window.__canvasPerf.measuring) window.__canvasPerf.long_tasks.push(...entries); }).observe({ type: "longtask", buffered: false });
        for (const name of ["pointerdown", "mousedown"]) document.addEventListener(name, event => {
            if (!window.__canvasPerf.measuring) return;
            window.__canvasPerf.pointer_hits.push({ type: event.type, x: event.clientX, y: event.clientY, node_id: event.target.closest("[data-node-id]")?.dataset.nodeId || null, visible_svg: Boolean(event.target.closest("svg")), button: Boolean(event.target.closest("button")), trusted: event.isTrusted });
        }, true);
        document.addEventListener("wheel", event => {
            if (window.__canvasPerf.measuring) window.__canvasPerf.wheel_events.push({ delta_y: event.deltaY, ctrl_key: event.ctrlKey, trusted: event.isTrusted });
        }, { capture: true, passive: true });
    });
}

const nextFrame = page => page.evaluate(() => new Promise(requestAnimationFrame));
const liveViewport = page => page.locator("[data-canvas-viewport]").evaluate(element => ({ x: Number.parseFloat(element.style.getPropertyValue("--canvas-live-x")), y: Number.parseFloat(element.style.getPropertyValue("--canvas-live-y")), k: Number.parseFloat(element.style.getPropertyValue("--canvas-live-scale")) }));
const waitViewport = (page, expected) => page.waitForFunction(expected => {
    const element = document.querySelector("[data-canvas-viewport]");
    return element && !element.dataset.canvasViewportInteracting && [["x", "x"], ["y", "y"], ["k", "scale"]].every(([key, property]) => Math.abs(Number.parseFloat(element.style.getPropertyValue(`--canvas-live-${property}`)) - expected[key]) < .001);
}, expected);
const healthy = audit => { for (const key of ["unknown_routes", "page_errors", "http_errors", "fixture_errors"]) assert.deepEqual(audit[key], [], `${key}必须为空`); };
async function waitAudit(read, predicate, { timeout = 20000, operation = "保存回执" } = {}) {
    const deadline = Date.now() + timeout;
    while (!predicate(read())) { healthy(read()); assert.ok(Date.now() < deadline, `性能夹具等待${operation}超时：${JSON.stringify(read())}`); await new Promise(accept => setTimeout(accept, 50)); }
    return read();
}
async function start(page) { await page.evaluate(() => Object.assign(window.__canvasPerf, { measuring: true, frames: [], long_tasks: [], viewports: [], pointer_hits: [], wheel_events: [] })); }
async function end(page) {
    await nextFrame(page); await nextFrame(page);
    const sample = await page.evaluate(() => { window.__canvasPerf.measuring = false; return window.__canvasPerf; });
    assert.ok(sample.frames.length >= 24, "帧样本不足，不能将未执行的手势计为测量");
    return { frames: stats(sample.frames), frame_intervals_ms: sample.frames, long_tasks: sample.long_tasks, pointer_hits: sample.pointer_hits, live_viewports: sample.viewports, wheel_events: sample.wheel_events };
}
async function landing(page) {
    const icon = page.locator('[data-node-id="node-0"] .canvas-node-external-header > div > svg');
    const point = () => icon.evaluate(element => {
        const bounds = element.getBoundingClientRect();
        // Chromium的mousedown采用整数CSS坐标；落点取整数，确保两端具有同一实际拖拽位移。
        return [0.125, .25, .375, .5, .625, .75, .875].flatMap(y => [.5, .25, .75].map(x => ({ x: Math.floor(bounds.x + x * bounds.width), y: Math.floor(bounds.y + y * bounds.height) }))).find(point => { const hit = document.elementFromPoint(point.x, point.y); return (hit === element || element.contains(hit)) && !hit.closest("button"); });
    });
    for (let attempt = 0; attempt < 3; attempt++) { const hit = await point(); assert.ok(hit, "原标题SVG没有可见拖拽区域"); await page.mouse.move(hit.x, hit.y); await nextFrame(page); const settled = await point(); if (settled) { await page.mouse.move(settled.x, settled.y); return settled; } }
    throw new Error("悬停后原标题SVG没有可见拖拽区域");
}

const resources = page => page.evaluate(() => performance.getEntriesByType("resource").filter(entry => entry.initiatorType === "script" || entry.initiatorType === "link" || /\.woff2(?:$|\?)/.test(entry.name)).map(entry => ({ path: new URL(entry.name).pathname, same_origin: new URL(entry.name).origin === location.origin, duration_ms: entry.duration, encoded_bytes: entry.encodedBodySize, decoded_bytes: entry.decodedBodySize, transfer_bytes: entry.transferSize })));

async function measure(base, count, kind) {
    const context = await browser.newContext({ viewport: report.viewport, deviceScaleFactor: 1, colorScheme: "dark", reducedMotion: "no-preference" });
    const entry = { kind, count, gestures: [], status: "running" };
    report.cases.push(entry);
    let read, page, cdp;
    try {
        await context.addCookies([{ name: "sd_csrf", value: "performance-fixture", url: base }]);
        page = await context.newPage();
        read = await fixture(page, count, kind); await instrument(page);
        await page.goto(`${base}/${kind === "source" ? "" : "canvas-app/"}canvas/performance-${count}`, { waitUntil: "domcontentloaded", timeout: 60000 });
        await page.locator(".canvas-main-toolbar").waitFor();
        await page.locator('[data-node-id="node-0"]').waitFor({ state: "visible" });
        await nextFrame(page);
        entry.canvas_open_ms = await page.evaluate(() => performance.now());
        await page.evaluate(() => document.fonts.ready);
        await page.getByRole("button", { name: kind === "source" ? "画布保存状态：已保存在本地" : "画布保存状态：已保存", exact: true }).waitFor();
        if (kind === "source") {
            await page.getByRole("button", { name: "关闭助手", exact: true }).click();
            await page.waitForFunction(() => document.querySelector("[data-canvas-viewport]")?.clientWidth >= innerWidth - 1);
            await nextFrame(page); await nextFrame(page);
        }
        // 原侧栏关闭会把视口横移半个侧栏宽度；沿原滚轮平移恢复共同的(1,1,1)。
        const viewport = await liveViewport(page);
        entry.viewport_alignment = { before: viewport };
        if (Math.abs(viewport.x - 1) > .001 || Math.abs(viewport.y - 1) > .001) { await page.mouse.move(1200, 120); await page.mouse.wheel(viewport.x - 1, viewport.y - 1); }
        await page.waitForFunction(() => {
            const container = document.querySelector("[data-canvas-viewport]");
            return container && !container.dataset.canvasViewportInteracting && ["x", "y", "scale"].every(key => Math.abs(Number.parseFloat(container.style.getPropertyValue(`--canvas-live-${key}`)) - 1) < .001);
        });
        const aligned = await liveViewport(page);
        entry.viewport_alignment = { before: viewport, after: aligned };
        for (const key of ["x", "y", "k"]) assert.ok(Math.abs(aligned[key] - 1) < .001, "两端初始实时视口必须对齐");
        await page.locator('[data-node-id="node-0"]').waitFor({ state: "visible" });
        await page.getByRole("button", { name: kind === "source" ? "画布保存状态：已保存在本地" : "画布保存状态：已保存", exact: true }).waitFor();
        entry.first_interactive_ms = await page.evaluate(() => performance.now());
        entry.loading_long_tasks = await page.evaluate(() => window.__canvasPerf.loading_long_tasks.filter(entry => entry.start_ms <= performance.now()));
        entry.loading_resources = await resources(page);
        entry.initial_mounted_nodes = await page.locator("[data-node-id]").count();
        entry.initial_live_viewport = aligned;
        await page.evaluate(() => document.querySelector("[data-canvas-viewport]").addEventListener("canvas:graphics-viewport-preview", event => { if (window.__canvasPerf.measuring) window.__canvasPerf.viewports.push({ ...event.detail }); }));
        let signature, quiet = Date.now();
        // 固定源会预读全部图片；先完成相同预热条件，再测量手势，并单独报告预读耗时。
        await waitAudit(read, audit => { const next = `${audit.commits}:${audit.image_requests}:${audit.document_bytes}`; if (signature !== next) { signature = next; quiet = Date.now(); } return Date.now() - quiet >= 1000; }, { timeout: 120000, operation: "初始图片预读稳定" });
        entry.ready_ms = await page.evaluate(() => performance.now());
        entry.prefetch_settle_ms = entry.ready_ms - entry.first_interactive_ms;
        entry.before = read(); healthy(entry.before);
        assert.equal(entry.before.unique_videos, 0); assert.equal(await page.locator("video").count(), 0);
        if (profileEnabled) { cdp = await context.newCDPSession(page); await cdp.send("Profiler.enable"); await cdp.send("Profiler.setSamplingInterval", { interval: 1000 }); await cdp.send("Profiler.start"); }
        for (let round = 0; round < rounds; round++) {
            const hit = await landing(page), previous = read();
            const dragViewport = await liveViewport(page);
            await start(page); await page.mouse.down();
            for (let step = 1; step <= 48; step++) { await page.mouse.move(hit.x + step * 1.2, hit.y + step * .45); await nextFrame(page); }
            await page.mouse.up();
            const drag = { round, kind: "drag", landing: hit, start_live_viewport: dragViewport, ...await end(page) };
            entry.gestures.push(drag);
            assert.ok(drag.pointer_hits.some(event => event.type === "mousedown" && event.node_id === "node-0" && event.visible_svg && !event.button && event.trusted), `实际mousedown必须命中同一节点的原标题SVG：${JSON.stringify(drag.pointer_hits)}`);
            await page.keyboard.press("Control+s");
            const after = await waitAudit(read, audit => audit.commits > previous.commits);
            assert.equal(after.commits - previous.commits, 1); assert.notEqual(after.first_position.x, previous.first_position.x);
            drag.persisted_position = after.first_position;

            await page.mouse.move(1200, 100);
            assert.ok(await page.evaluate(() => {
                const element = document.elementFromPoint(1200, 100);
                return element?.closest("[data-canvas-viewport]") && !element.closest("[data-node-id],button,[data-canvas-no-zoom],[data-connection-create-menu]");
            }), "平移起点必须是实际画布空白，不能命中节点或浮层");
            const panViewport = await liveViewport(page);
            await page.keyboard.down("Space"); await start(page); await page.mouse.down();
            for (let step = 1; step <= 48; step++) { await page.mouse.move(1200 + Math.sin(step / 48 * Math.PI) * 70, 100 - step * .3); await nextFrame(page); }
            await page.mouse.up(); await page.keyboard.up("Space");
            const pan = { round, kind: "pan", start_live_viewport: panViewport, ...await end(page) };
            entry.gestures.push(pan);
            assert.ok(pan.pointer_hits.some(event => event.type === "pointerdown" && event.node_id === null && !event.button && event.trusted), `平移pointerdown必须命中实际画布空白：${JSON.stringify(pan.pointer_hits)}`);
            assert.ok(pan.live_viewports.length >= 24 && new Set(pan.live_viewports.map(value => `${value.x},${value.y}`)).size >= 12, `平移必须实际改变实时视口：${JSON.stringify({ events: pan.live_viewports.length, first: pan.live_viewports[0], last: pan.live_viewports.at(-1) })}`);
            await waitViewport(page, { ...panViewport, y: panViewport.y - 48 * .3 });
            pan.final_live_viewport = await liveViewport(page);

            const zoomViewport = await liveViewport(page);
            const containerBounds = await page.locator("[data-canvas-viewport]").boundingBox();
            await page.mouse.move(1200, 700); await page.keyboard.down("Control"); await start(page);
            for (let step = 0; step < 24; step++) { await page.mouse.wheel(0, step < 12 ? -24 : 24); await nextFrame(page); }
            await page.waitForFunction(() => window.__canvasPerf.wheel_events.length === 24);
            await page.keyboard.up("Control");
            const zoom = { round, kind: "zoom", start_live_viewport: zoomViewport, ...await end(page) };
            entry.gestures.push(zoom);
            assert.ok(zoom.wheel_events.length === 24 && zoom.wheel_events.every(event => event.ctrl_key && event.trusted), "24次原生滚轮事件必须全部按Ctrl处理");
            const scales = zoom.live_viewports.map(value => value.k);
            assert.ok(scales.length >= 12 && Math.max(...scales) - Math.min(...scales) > .1, "缩放必须实际改变实时倍率");
            let expectedScale = zoomViewport.k;
            for (const event of zoom.wheel_events) expectedScale = Math.min(2, Math.max(.05, expectedScale * Math.pow(1.1, -event.delta_y / 24)));
            const anchor = { x: 1200 - containerBounds.x, y: 700 - containerBounds.y };
            await waitViewport(page, { k: expectedScale, x: anchor.x - (anchor.x - zoomViewport.x) * expectedScale / zoomViewport.k, y: anchor.y - (anchor.y - zoomViewport.y) * expectedScale / zoomViewport.k });
            zoom.final_live_viewport = await liveViewport(page);
        }
        if (cdp) { await writeFile(resolve(output, `${kind}-${count}.cpuprofile`), JSON.stringify((await cdp.send("Profiler.stop")).profile)); await cdp.detach(); cdp = undefined; }
        // 手势全部测完后沿真实空白点击取消单选，避免composer遮挡视频播放按钮。
        const playbackViewport = await liveViewport(page);
        const blank = await page.evaluate(() => {
            const container = document.querySelector("[data-canvas-viewport]");
            const bounds = container.getBoundingClientRect();
            for (let y = Math.ceil(bounds.top + 80); y < Math.min(bounds.bottom - 80, innerHeight - 80); y += 40) {
                for (let x = Math.ceil(bounds.right - 80); x > bounds.left + 80; x -= 40) {
                    const hit = document.elementFromPoint(x, y);
                    if (hit?.closest("[data-canvas-viewport]") && !hit.closest("[data-node-id],button,[data-canvas-no-zoom],[data-connection-create-menu]")) return { x, y };
                }
            }
            return null;
        });
        assert.ok(blank, "媒体检查前必须找到实际画布空白");
        await page.mouse.click(blank.x, blank.y);
        await page.locator("[data-canvas-node-panel]").waitFor({ state: "detached" });
        const deselectedViewport = await liveViewport(page);
        for (const key of ["x", "y", "k"]) assert.ok(Math.abs(deselectedViewport[key] - playbackViewport[key]) < .001, "取消选择不能改变已测手势的最终视口");
        entry.playback_deselection = { blank, before: playbackViewport, after: deselectedViewport };
        entry.playback = [];
        for (const id of ["node-2", "node-10"]) {
            await page.locator(`[data-node-id="${id}"]`).getByRole("button", { name: /^播放 / }).click();
            await page.waitForFunction(() => [...document.querySelectorAll("video")].some(video => video.readyState >= 2 && video.currentTime > .35));
            const videos = await page.evaluate(() => [...document.querySelectorAll("video")].map(video => ({ width: video.videoWidth, height: video.videoHeight, time: video.currentTime, paused: video.paused })));
            assert.equal(videos.length, 1); assert.equal(videos[0].width, 320); assert.equal(videos[0].height, 180); entry.playback.push({ id, videos });
        }
        entry.after = read(); healthy(entry.after); assert.equal(entry.after.commits - entry.before.commits, rounds); assert.equal(entry.after.unique_videos, 2);
        entry.final_live_viewport = await liveViewport(page);
        entry.final_mounted_nodes = await page.locator("[data-node-id]").count(); assert.ok(entry.final_mounted_nodes <= 720);
        if (arrangeEnabled) {
            const before = read(), previousLayout = read.layout();
            await page.evaluate(() => {
                const button = [...document.querySelectorAll("button")].find(element => element.getAttribute("aria-label") === "自动整理节点");
                if (!button) throw new Error("原自动整理按钮不可达");
                button.addEventListener("click", () => {
                    window.__canvasPerf.arrange = { start_ms: performance.now() };
                    requestAnimationFrame(() => requestAnimationFrame(() => { window.__canvasPerf.arrange.frame_ms = performance.now(); }));
                }, { capture: true, once: true });
            });
            await page.getByRole("button", { name: "自动整理节点", exact: true }).click();
            await page.waitForFunction(() => window.__canvasPerf.arrange?.frame_ms !== undefined);
            await page.keyboard.press("Control+s");
            const saved = await waitAudit(read, audit => audit.commits > before.commits);
            assert.equal(saved.commits - before.commits, 1, "整理必须只保存一次完整文档");
            const timing = await page.evaluate(() => ({ ...window.__canvasPerf.arrange, saved_ms: performance.now() }));
            const arranged = read.layout();
            const changed = arranged.filter((node, index) => JSON.stringify(node.position) !== JSON.stringify(previousLayout[index].position));
            assert.ok(changed.length >= 2, "真实整理必须改变多个节点的位置");
            entry.arrangement = { first_frame_ms: timing.frame_ms - timing.start_ms, saved_ms: timing.saved_ms - timing.start_ms, changed_nodes: changed.length, layout_sha256: digest(JSON.stringify(arranged)) };
            await writeFile(resolve(output, `${kind}-${count}-arranged.json`), JSON.stringify(arranged));
            // 保存请求到达HTTP夹具不代表原180ms分组历史已落成；两端都存在早撤销边界。
            // 首帧与保存耗时在上方已记录，这段独立等待仅用于完整历史撤销校验。
            await page.getByRole("button", { name: kind === "source" ? "画布保存状态：已保存在本地" : "画布保存状态：已保存", exact: true }).waitFor();
            await nextFrame(page); await nextFrame(page);
            await page.evaluate(() => new Promise(accept => setTimeout(accept, 180)));
            entry.arrangement.history_settle_wait_ms = await page.evaluate(saved => performance.now() - saved, timing.saved_ms);
            entry.arrangement.undo_contract = "client_saved_then_source_history_debounce_180ms";
            await page.keyboard.press("Control+z");
            await page.keyboard.press("Control+s");
            const undone = await waitAudit(read, audit => audit.commits > saved.commits);
            assert.equal(undone.commits - saved.commits, 1);
            assert.deepEqual(read.layout(), previousLayout, "撤销整理必须恢复所有节点的原坐标");
            const after = await liveViewport(page);
            for (const key of ["x", "y", "k"]) assert.ok(Math.abs(after[key] - entry.final_live_viewport[key]) < .001, "整理及撤销不能改变视口");
            entry.arrangement.undo_restored = true;
            healthy(undone);
        }
        entry.resources = await resources(page);
        assert.ok(entry.resources.every(resource => resource.same_origin && !resource.path.includes("/@vite/")), "必须测量同源生产静态资源");
        await page.screenshot({ path: resolve(output, `${kind}-${count}.png`) });
        await page.goto("about:blank"); entry.video_elements_after_leave = await page.locator("video").count(); assert.equal(entry.video_elements_after_leave, 0);
        entry.status = "measured";
    } catch (error) { entry.status = "failed"; entry.error = error.stack || String(error); entry.after = read?.(); if (page) { entry.failed_live_viewport = await liveViewport(page).catch(() => null); await page.screenshot({ path: resolve(output, `${kind}-${count}-failed.png`) }); } throw error; }
    finally { await cdp?.detach(); await context.close(); await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n"); }
    process.stdout.write(JSON.stringify({ kind, count, ready_ms: entry.ready_ms, frames: entry.gestures.map(gesture => ({ round: gesture.round, kind: gesture.kind, ...gesture.frames })) }) + "\n");
}

try {
    process.env.CI = "true";
    const sites = [...(sourceRoot ? [["source", resolve(sourceRoot, "web"), 4251]] : []), ["target", frontend, 4252]];
    for (const [kind, siteRoot, port] of sites) {
        const requireSite = createRequire(resolve(siteRoot, "package.json"));
        const { preview } = await import(pathToFileURL(requireSite.resolve("vite")).href);
        const html = await readFile(resolve(siteRoot, "dist", kind === "source" ? "index.html" : "canvas-app/index.html"), "utf8");
        assert.ok(!html.includes("/@vite/client")); report[`${kind}_entry_sha256`] = digest(html);
        servers.push(await preview({ root: siteRoot, logLevel: "error", preview: { host: "127.0.0.1", port, strictPort: true } }));
    }
    browser = await chromium.launch(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : { channel: "chrome" });
    report.browser = browser.version();
    for (const count of counts) for (const [kind, , port] of sites) await measure(`http://127.0.0.1:${port}`, count, kind);
    report.relative = counts.map(count => {
        const source = report.cases.find(entry => entry.kind === "source" && entry.count === count), target = report.cases.find(entry => entry.kind === "target" && entry.count === count);
        if (!source) return { count, comparison: "source_not_measured" };
        for (const [index, gesture] of target.gestures.entries()) {
            const original = source.gestures[index];
            assert.equal(gesture.kind, original.kind); assert.equal(gesture.round, original.round);
            for (const key of ["x", "y", "k"]) assert.ok(Math.abs(gesture.start_live_viewport[key] - original.start_live_viewport[key]) < .001, "两端每组手势的起始视口必须一致");
            if (gesture.persisted_position) for (const key of ["x", "y"]) assert.ok(Math.abs(gesture.persisted_position[key] - original.persisted_position[key]) < .001, "两端每次拖拽的持久位置必须一致");
        }
        for (const key of ["x", "y"]) assert.ok(Math.abs(source.after.first_position[key] - target.after.first_position[key]) < .001, "两端同一手势的持久位置必须一致");
        for (const key of ["x", "y", "k"]) assert.ok(Math.abs(source.final_live_viewport[key] - target.final_live_viewport[key]) < .001, "两端同一手势的最终视口必须一致");
        if (arrangeEnabled) assert.equal(target.arrangement.layout_sha256, source.arrangement.layout_sha256, "两端整理后所有节点坐标必须完全一致");
        return { count, gestures: target.gestures.map((gesture, index) => ({ round: gesture.round, kind: gesture.kind, source_p95_ms: source.gestures[index].frames.p95_ms, target_p95_ms: gesture.frames.p95_ms, ratio: gesture.frames.p95_ms / source.gestures[index].frames.p95_ms })) };
    });
    const budgetCases = report.cases.filter(entry => entry.kind === "target" && entry.count === 1000);
    report.frame_budget_1000_passed = profileEnabled || !budgetCases.length ? null : budgetCases.every(entry => entry.gestures.filter(gesture => gesture.kind === "drag" || gesture.kind === "zoom").every(gesture => gesture.frames.p95_ms <= 32));
    report.status = profileEnabled ? "diagnostic_profile" : report.frame_budget_1000_passed === false ? "frame_budget_failed" : "measured";
    if (report.frame_budget_1000_passed === false) process.exitCode = 1;
} catch (error) { report.status = "failed"; report.error = error.stack || String(error); process.exitCode = 1; }
finally {
    await browser?.close();
    for (const server of servers.reverse()) await new Promise((accept, reject) => server.httpServer.close(error => error ? reject(error) : accept()));
    report.finished_at = new Date().toISOString();
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    process.stdout.write(JSON.stringify({ status: report.status, frame_budget_1000_passed: report.frame_budget_1000_passed, report: resolve(output, "report.json"), error: report.error }) + "\n");
}
