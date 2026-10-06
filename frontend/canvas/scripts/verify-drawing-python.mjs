import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";
import { verifyDrawingCreation } from "./verify-drawing-creation-python.mjs";
import { verifyConcurrentDrawingCopy } from "./verify-drawing-copy-concurrency.mjs";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4189";
const output = resolve(".runtime/drawing-python-browser");
const report = { step: "start", page_errors: [], failed_routes: [] };
await mkdir(output, { recursive: true });
const server = await createServer({ server: { host: "127.0.0.1", port: 4189, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
function observe(target) {
    target.on("pageerror", error => report.page_errors.push(error.message));
    target.on("response", response => {
        const path = new URL(response.url()).pathname;
        if (response.status() >= 400 && path.startsWith("/api/")) report.failed_routes.push({ path, status: response.status() });
    });
}
async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function openCanvas(target, key) {
    await target.goto(`${origin}/canvas-app/canvas/${key}`, { timeout: 60000 });
    await target.locator("[data-canvas-viewport]").waitFor({ timeout: 60000 });
    const close = target.getByRole("button", { name: "关闭助手", exact: true });
    if (await close.isVisible()) await close.click();
}
async function openDrawing(target, id) {
    await target.locator(`[data-node-id="${id}"]`).dblclick();
    const save = target.getByRole("button", { name: "保存绘图", exact: true });
    await save.waitFor({ timeout: 60000 });
    await target.waitForFunction(() => [...document.querySelectorAll("button")].some(button => button.textContent === "保存绘图" && !button.disabled), { timeout: 60000 });
}
async function stroke(target, offset = 0) {
    const pen = target.getByRole("radio", { name: "自由书写", exact: true });
    await pen.locator("..").click();
    assert.ok(await pen.isChecked());
    const surface = target.locator(".excalidraw__canvas.interactive");
    const box = await surface.boundingBox();
    assert.ok(box);
    await target.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.5 + offset);
    await target.mouse.down();
    await target.mouse.move(box.x + box.width * 0.56, box.y + box.height * 0.58 + offset, { steps: 12 });
    await target.mouse.up();
}
async function save(target, drawingPath, status = 200) {
    const response = target.waitForResponse(response => new URL(response.url()).pathname === drawingPath && response.request().method() === "PUT");
    await target.getByRole("button", { name: "保存绘图", exact: true }).click();
    const result = await response;
    assert.equal(result.status(), status, await result.text());
    await target.getByRole("button", { name: "保存绘图", exact: true }).waitFor();
    return result.json();
}
async function graph(context, path, predicate) {
    const deadline = Date.now() + 20000;
    do {
        const document = (await read(await context.request.get(`${origin}${path}/my-document`))).source_document;
        if (predicate(document)) return document;
        await new Promise(resolve => setTimeout(resolve, 100));
    } while (Date.now() < deadline);
    assert.fail(`画布自动保存未完成：${report.step}`);
}

async function chooseHistory(target, path, entry) {
    await target.keyboard.press("Control+s");
    await target.waitForFunction(async () => {
        const { hasUnconfirmedCanvasEdits } = await import("/canvas-app/src/services/local-workspace-repository.ts");
        const key = location.pathname.split("/").at(-1);
        return !hasUnconfirmedCanvasEdits(key);
    });
    const panel = target.locator(".canvas-version-sidebar");
    if (!await panel.isVisible()) await target.getByRole("button", { name: "版本记录", exact: true }).click();
    const refreshed = target.waitForResponse(response => new URL(response.url()).pathname === `${path}/revisions`);
    await target.getByRole("button", { name: "刷新版本记录", exact: true }).click();
    assert.equal((await refreshed).status(), 200);
    await panel.locator(".canvas-version-item").filter({ has: target.getByText(`v${entry.row_version}`, { exact: true }) }).click();
    await panel.getByRole("button", { name: "恢复此版本", exact: true }).waitFor();
    await target.waitForFunction(() => [...document.querySelectorAll(".canvas-version-footer button")].some(button => button.textContent === "恢复此版本" && !button.disabled));
}

async function restoreSelected(target, path, status = 200) {
    await target.locator(".canvas-version-sidebar").getByRole("button", { name: "恢复此版本", exact: true }).click();
    const response = target.waitForResponse(response => new URL(response.url()).pathname.startsWith(`${path}/revisions/`) && response.url().endsWith("/restore") && response.request().method() === "POST");
    await target.getByRole("dialog").getByRole("button", { name: "恢复此版本", exact: true }).click();
    const result = await response;
    assert.equal(result.status(), status, await result.text());
    await target.getByRole("dialog").waitFor({ state: "hidden" });
    if (status === 200) await target.locator("[data-canvas-version-preview]").waitFor({ state: "hidden" });
    return result;
}

async function drawingHistory(target, context, path, key, node, drawingPath) {
    report.step = "source history creates frozen drawing backup";
    const original = (await read(await context.request.get(`${origin}${drawingPath}`))).drawing;
    const empty = (await read(await context.request.get(`${origin}${path}/revisions`))).items.find(entry => entry.node_count === 0);
    assert.ok(empty);
    await chooseHistory(target, path, empty);
    await restoreSelected(target, path);
    await graph(context, path, document => document.nodes.length === 0);
    const backup = (await read(await context.request.get(`${origin}${path}/revisions`))).items.find(entry => entry.node_count === 1);
    assert.ok(backup);
    await chooseHistory(target, path, backup);
    const preview = target.locator("[data-canvas-version-preview]").getByAltText("绘图预览", { exact: true });
    await preview.waitFor();
    const previewUrl = await preview.getAttribute("src");
    assert.equal(new URL(previewUrl, origin).pathname, `/api/v1/canvas-runtime/resources/${original.previewResourceId}/file`);
    await preview.dblclick();
    assert.equal(await target.locator(".canvas-drawing-editor-modal").count(), 0);
    await restoreSelected(target, path);
    await target.getByRole("button", { name: "关闭版本记录", exact: true }).click();
    await openDrawing(target, node.id);
    await stroke(target, 85);
    await save(target, drawingPath);
    await target.getByRole("button", { name: "关闭绘图编辑器", exact: true }).click();
    await target.locator(".canvas-drawing-editor-modal").waitFor({ state: "hidden" });
    await graph(context, path, document => document.nodes[0]?.metadata.drawingShapeCount === 2);
    const later = (await read(await context.request.get(`${origin}${drawingPath}`))).drawing;
    assert.notDeepEqual(later.snapshot, original.snapshot);
    report.step = "frozen preview and original restore with lost acknowledgement";
    await chooseHistory(target, path, backup);
    assert.equal(await preview.getAttribute("src"), previewUrl);
    const restoreUrl = `${origin}${path}/revisions/${backup.id}/restore`;
    let lose = true;
    const attempts = [];
    await target.route(restoreUrl, async route => {
        attempts.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
        const response = await route.fetch();
        if (lose && response.status() === 200) {
            lose = false;
            return route.fulfill({ status: 503, json: { error: { code: "ack_lost", message: "测试注入：历史恢复回执丢失" } } });
        }
        return route.fulfill({ response });
    });
    await restoreSelected(target, path, 503);
    await restoreSelected(target, path);
    await target.unroute(restoreUrl);
    assert.equal(attempts.length, 2);
    assert.deepEqual(attempts[0], attempts[1]);
    const restored = (await read(await context.request.get(`${origin}${drawingPath}`))).drawing;
    assert.deepEqual(restored.snapshot, original.snapshot);
    assert.ok(BigInt(restored.revision) > BigInt(later.revision));
    report.history_preview_frozen = report.history_restore_replayed = true;
    await target.getByRole("button", { name: "关闭版本记录", exact: true }).click();
    report.step = "source delete and history restores drawing tombstone";
    await target.locator(`[data-node-id="${node.id}"]`).click();
    const deleted = target.waitForResponse(response => new URL(response.url()).pathname === drawingPath && response.request().method() === "DELETE");
    await target.keyboard.press("Delete");
    assert.equal((await deleted).status(), 200);
    await graph(context, path, document => document.nodes.length === 0);
    assert.equal((await context.request.get(`${origin}${drawingPath}`)).status(), 404);
    await chooseHistory(target, path, backup);
    await restoreSelected(target, path);
    await target.getByRole("button", { name: "关闭版本记录", exact: true }).click();
    await openDrawing(target, node.id);
    const recovered = await target.evaluate(async ({ key, id }) => {
        const { loadCanvasDrawing } = await import("/canvas-app/src/lib/canvas/canvas-drawing-storage.ts");
        return loadCanvasDrawing(key, id);
    }, { key, id: node.metadata.drawingId });
    assert.deepEqual(recovered.snapshot, original.snapshot);
    assert.equal(recovered.origin, "canonical");
    report.history_deleted_drawing_restored = true;
    await target.getByRole("button", { name: "关闭绘图编辑器", exact: true }).click();
    await target.locator(".canvas-drawing-editor-modal").waitFor({ state: "hidden" });
    await graph(context, path, document => document.nodes[0]?.metadata.drawingShapeCount === 1);
    assert.deepEqual((await read(await context.request.get(`${origin}${drawingPath}/versions/${original.revision}`))).drawing.snapshot, original.snapshot);
    report.step = "other browser restores after this browser deleted the drawing";
    const beforeRemoval = (await read(await context.request.get(`${origin}${drawingPath}`))).drawing;
    await target.locator(`[data-node-id="${node.id}"]`).click();
    const removedAgain = target.waitForResponse(response => new URL(response.url()).pathname === drawingPath && response.request().method() === "DELETE");
    await target.keyboard.press("Delete");
    const removal = await read(await removedAgain);
    await graph(context, path, document => document.nodes.length === 0);
    await target.waitForFunction(async ({ key, id, revision }) => {
        const { peekCanvasDrawingCacheForTests } = await import("/canvas-app/src/lib/canvas/canvas-drawing-storage.ts");
        const { captureUserScope } = await import("/canvas-app/src/lib/user-scope-guard.ts");
        const cached = await peekCanvasDrawingCacheForTests(key, id, captureUserScope().userScope);
        return cached?.removedRevision === revision;
    }, { key, id: node.metadata.drawingId, revision: removal.revision });
    for (const revision of [beforeRemoval.revision, removal.revision]) {
        await target.route(`${origin}${drawingPath}`, route => route.request().method() === "GET"
            ? route.fulfill({ json: { drawing: { ...beforeRemoval, revision } } }) : route.continue());
        try {
            const stale = await target.evaluate(async ({ key, id }) => {
                const { loadCanvasDrawing, loadCanvasDrawingBundle, cloneCanvasDrawing } = await import("/canvas-app/src/lib/canvas/canvas-drawing-storage.ts");
                return {
                    saved: await loadCanvasDrawing(key, id),
                    bundle: await loadCanvasDrawingBundle(key, id),
                    copy: await cloneCanvasDrawing(key, id, `${id}-stale-copy`),
                };
            }, { key, id: node.metadata.drawingId });
            assert.deepEqual(stale, { saved: null, bundle: null, copy: null }, "stale or equal-version reads must not resurrect or copy an acknowledged deletion");
            assert.equal((await context.request.get(`${origin}${drawingPath}-stale-copy`)).status(), 404);
        } finally {
            await target.unroute(`${origin}${drawingPath}`);
        }
    }
    report.stale_read_kept_deletion = true;
    report.stale_bundle_and_copy_kept_deletion = true;
    const remoteContext = await context.browser().newContext({ viewport: { width: 1440, height: 900 } });
    remoteContext.setDefaultTimeout(15000);
    const cookies = await context.cookies();
    await remoteContext.addCookies(cookies);
    await remoteContext.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": cookies.find(cookie => cookie.name === "sd_csrf").value });
    try {
        const remote = await remoteContext.newPage();
        observe(remote);
        await openCanvas(remote, key);
        await chooseHistory(remote, path, backup);
        await restoreSelected(remote, path);
    } finally {
        await remoteContext.close();
    }
    const restoredBundle = await target.evaluate(async ({ key, id }) => {
        const { loadCanvasDrawingBundle } = await import("/canvas-app/src/lib/canvas/canvas-drawing-storage.ts");
        const bundle = await loadCanvasDrawingBundle(key, id);
        return bundle ? { snapshot: bundle.saved.snapshot, previewBytes: bundle.preview?.size, renderBytes: bundle.render?.blob.size } : null;
    }, { key, id: node.metadata.drawingId });
    assert.ok(restoredBundle, "an explicitly restored newer version must remain available to copy or export");
    assert.deepEqual(restoredBundle.snapshot, original.snapshot);
    assert.ok(restoredBundle.previewBytes > 0 && restoredBundle.renderBytes > 0);
    report.newer_restored_bundle_available = true;
    await openCanvas(target, key);
    await openDrawing(target, node.id);
    const crossWindow = await target.evaluate(async ({ key, id }) => {
        const { loadCanvasDrawing } = await import("/canvas-app/src/lib/canvas/canvas-drawing-storage.ts");
        return loadCanvasDrawing(key, id);
    }, { key, id: node.metadata.drawingId });
    assert.ok(crossWindow, "server restore must supersede another browser's acknowledged deletion cache");
    assert.deepEqual(crossWindow.snapshot, original.snapshot);
    assert.equal(crossWindow.origin, "canonical");
    report.cross_browser_deleted_drawing_restored = true;
    await target.getByRole("button", { name: "关闭绘图编辑器", exact: true }).click();
    await target.locator(".canvas-drawing-editor-modal").waitFor({ state: "hidden" });
    return (await read(await context.request.get(`${origin}${drawingPath}`))).drawing;
}

async function drawingCopies(target, context, projectId, path, key, node, drawingPath) {
    await openCanvas(target, key);
    const source = (await read(await context.request.get(`${origin}${drawingPath}`))).drawing;
    report.step = "source drawing keyboard copy";
    await target.locator(`[data-node-id="${node.id}"]`).click();
    await target.keyboard.press("Control+c");
    await target.waitForFunction(async () => (await navigator.clipboard.readText()).startsWith("open-ai-canvas-nodes:"));
    for (const sameProject of [true, false]) {
        report.step = sameProject ? "drawing paste in another canvas of same project" : "drawing paste in another project";
        let destination;
        if (sameProject) {
            const canvas = await read(await context.request.post(`${origin}/api/v1/projects/${projectId}/canvases`, {
                headers: { "Idempotency-Key": "drawing-paste-same" }, data: { title: "绘图粘贴验证" },
            }));
            destination = `/api/v1/projects/${projectId}/canvases/${canvas.id}`;
        } else {
            const project = await read(await context.request.post(`${origin}/api/v1/projects`, {
                headers: { "Idempotency-Key": "drawing-paste-cross" }, data: { name: "绘图跨项目粘贴", aspect: "16:9", workspace_mode: "infinite_canvas" },
            }));
            destination = `/api/v1/projects/${project.id}/canvases/${project.primary_canvas_id}`;
        }
        const initial = await read(await context.request.get(`${origin}${destination}/my-document`));
        await openCanvas(target, initial.source_key);
        await target.locator("[data-canvas-viewport]").click({ position: { x: 500, y: 600 } });
        await target.keyboard.press("Control+v");
        const pasted = await graph(context, destination, document => document.nodes.length === 1 && document.nodes[0].metadata.drawingShapeCount === source.shapeCount && document.nodes[0].metadata.drawingRevision !== "0");
        const copy = pasted.nodes[0];
        assert.notEqual(copy.id, node.id);
        assert.notEqual(copy.metadata.drawingId, node.metadata.drawingId);
        assert.equal(copy.width, node.width);
        assert.equal(copy.height, node.height);
        const copiedPath = `/api/v1/canvas-runtime/canvas-projects/${initial.source_key}/drawings/${copy.metadata.drawingId}`;
        const copied = (await read(await context.request.get(`${origin}${copiedPath}`))).drawing;
        assert.deepEqual(copied.snapshot, source.snapshot);
        assert.notEqual(copied.previewResourceId, source.previewResourceId);
        const previewBytes = await context.request.get(`${origin}/api/v1/canvas-runtime/resources/${copied.previewResourceId}/file`);
        assert.deepEqual(await previewBytes.body(), await (await context.request.get(`${origin}/api/v1/canvas-runtime/resources/${source.previewResourceId}/file`)).body());
        assert.notEqual(copied.render.resourceId, source.render.resourceId);
        assert.equal(copied.render.storageKey, `resource:${copied.render.resourceId}`);
        assert.deepEqual(await (await context.request.get(`${origin}/api/v1/canvas-runtime/resources/${copied.render.resourceId}/file`)).body(), await (await context.request.get(`${origin}/api/v1/canvas-runtime/resources/${source.render.resourceId}/file`)).body());
        const clean = await context.browser().newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies: await context.cookies(), origins: [] } });
        clean.setDefaultTimeout(15000);
        try {
            const restored = await clean.newPage();
            observe(restored);
            await openCanvas(restored, initial.source_key);
            await openDrawing(restored, copy.id);
            const document = await restored.evaluate(async ({ key, id }) => {
                const { loadCanvasDrawing } = await import("/canvas-app/src/lib/canvas/canvas-drawing-storage.ts");
                return loadCanvasDrawing(key, id);
            }, { key: initial.source_key, id: copy.metadata.drawingId });
            assert.deepEqual(document.snapshot, source.snapshot);
            await stroke(restored, 85);
            const edited = await save(restored, copiedPath);
            assert.equal(edited.drawing.shapeCount, source.shapeCount + 1);
            assert.deepEqual((await read(await context.request.get(`${origin}${drawingPath}`))).drawing, source);
        } finally {
            await clean.close();
        }
        report[sameProject ? "drawing_same_project_pasted" : "drawing_cross_project_pasted"] = true;
    }
    await openCanvas(target, key);
    report.step = "source create drawing parameter variant";
    await target.locator(`[data-node-id="${node.id}"]`).click({ button: "right" });
    await target.getByRole("button", { name: /^创建参数变体/ }).click();
    const duplicated = await graph(context, path, document => document.nodes.length === 2 && document.nodes.every(node => node.metadata.drawingShapeCount === source.shapeCount && node.metadata.drawingRevision !== "0"));
    const variant = duplicated.nodes.find(item => item.id !== node.id);
    assert.equal(variant.position.x, node.position.x + 36);
    assert.equal(variant.position.y, node.position.y + 36);
    const variantPath = `/api/v1/canvas-runtime/canvas-projects/${key}/drawings/${variant.metadata.drawingId}`;
    assert.deepEqual((await read(await context.request.get(`${origin}${variantPath}`))).drawing.snapshot, source.snapshot);
    report.drawing_parameter_variant_saved = true;
    const deleted = target.waitForResponse(response => new URL(response.url()).pathname === variantPath && response.request().method() === "DELETE");
    await target.keyboard.press("Delete");
    assert.equal((await deleted).status(), 200);
    await graph(context, path, document => document.nodes.length === 1);
    assert.deepEqual((await read(await context.request.get(`${origin}${drawingPath}`))).drawing, source);
    report.drawing_variant_deleted_independently = true;
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ["clipboard-read", "clipboard-write"] });
    context.setDefaultTimeout(15000);
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username: config.username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    const project = await read(await context.request.post(`${origin}/api/v1/projects`, {
        headers: { "Idempotency-Key": "drawing-browser" }, data: { name: "绘图验证", aspect: "16:9", workspace_mode: "infinite_canvas" },
    }));
    const path = `/api/v1/projects/${project.id}/canvases/${project.primary_canvas_id}`;
    const initial = await read(await context.request.get(`${origin}${path}/my-document`));
    page = await context.newPage();
    observe(page);
    await openCanvas(page, initial.source_key);
    report.step = "source create menu";
    await page.mouse.click(640, 390, { button: "right" });
    await page.getByRole("button", { name: "添加节点", exact: true }).last().click();
    await page.getByRole("button", { name: "搜索节点", exact: true }).click();
    await page.getByRole("textbox", { name: "搜索节点", exact: true }).fill("绘图");
    await page.getByRole("button", { name: "绘图", exact: true }).click();
    const created = await graph(context, path, document => document.nodes.some(node => node.type === "drawing"));
    const node = created.nodes.find(node => node.type === "drawing");
    const drawingPath = `/api/v1/canvas-runtime/canvas-projects/${initial.source_key}/drawings/${node.metadata.drawingId}`;
    await openDrawing(page, node.id);
    report.step = "source pointer stroke and lost acknowledgement";
    await stroke(page);
    const attempts = [];
    let loseAck = true;
    await page.route(`${origin}${drawingPath}`, async route => {
        if (route.request().method() !== "PUT") return route.continue();
        attempts.push(route.request().postDataJSON());
        const response = await route.fetch();
        if (loseAck && response.status() === 200) {
            loseAck = false;
            return route.fulfill({ status: 503, json: { error: { code: "test_ack_lost", message: "绘图回执丢失测试" } } });
        }
        await route.fulfill({ response });
    });
    await save(page, drawingPath, 503);
    const acknowledged = await save(page, drawingPath);
    assert.equal(acknowledged.drawing.revision, "1");
    assert.deepEqual(attempts[0], attempts[1]);
    assert.equal(acknowledged.drawing.shapeCount, 1);
    assert.equal(acknowledged.drawing.snapshot.elements.filter(element => !element.isDeleted).length, 1);
    const preview = await context.request.get(`${origin}/api/v1/canvas-runtime/resources/${acknowledged.drawing.previewResourceId}/file`);
    assert.ok(preview.ok());
    assert.equal((await preview.body()).subarray(0, 8).toString("hex"), "89504e470d0a1a0a");
    report.pointer_drawing_saved = report.lost_ack_replayed = true;
    await page.getByRole("button", { name: "关闭绘图编辑器", exact: true }).click();
    await page.locator(".canvas-drawing-editor-modal").waitFor({ state: "hidden" });
    await graph(context, path, document => document.nodes[0]?.metadata.drawingShapeCount === 1);

    const historyCanonical = await drawingHistory(page, context, path, initial.source_key, node, drawingPath);
    await drawingCopies(page, context, project.id, path, initial.source_key, node, drawingPath);
    await verifyDrawingCreation({ page, context, origin, path, key: initial.source_key, projectId: project.id, read, graph, openCanvas, openDrawing, observe, report });

    report.step = "fresh browser restoration";
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    fresh.setDefaultTimeout(15000);
    await fresh.addCookies(await context.cookies());
    await fresh.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    page = await fresh.newPage();
    observe(page);
    await openCanvas(page, initial.source_key);
    const image = page.locator(`[data-node-id="${node.id}"]`).getByAltText("绘图预览", { exact: true });
    await image.waitFor();
    assert.ok(await image.evaluate(image => image.complete && image.naturalWidth > 0));
    await openDrawing(page, node.id);
    const restored = await page.evaluate(async ({ key, id }) => {
        const { loadCanvasDrawing } = await import("/canvas-app/src/lib/canvas/canvas-drawing-storage.ts");
        return loadCanvasDrawing(key, id);
    }, { key: initial.source_key, id: node.metadata.drawingId });
    assert.deepEqual(restored.snapshot, historyCanonical.snapshot);
    report.fresh_context_restored = true;

    report.step = "two windows drawing conflict";
    const other = await fresh.newPage();
    observe(other);
    await openCanvas(other, initial.source_key);
    await openDrawing(other, node.id);
    await stroke(page, 70);
    const first = await save(page, drawingPath);
    await stroke(other, -70);
    await save(other, drawingPath, 409);
    const canonical = (await read(await fresh.request.get(`${origin}${drawingPath}`))).drawing;
    assert.equal(canonical.revision, first.drawing.revision);
    assert.deepEqual(canonical.snapshot, first.drawing.snapshot);
    const draft = await other.evaluate(async ({ key, id }) => {
        const { loadCanvasDrawing } = await import("/canvas-app/src/lib/canvas/canvas-drawing-storage.ts");
        return loadCanvasDrawing(key, id);
    }, { key: initial.source_key, id: node.metadata.drawingId });
    assert.equal(draft.origin, "draft");
    assert.ok(BigInt(draft.revision) < BigInt(canonical.revision));
    assert.equal(draft.shapeCount, 2);
    assert.notDeepEqual(draft.snapshot, canonical.snapshot);
    report.conflict_kept_draft = true;
    await fresh.close();
    for (const open of context.pages()) await open.close();
    page = undefined;
    await verifyConcurrentDrawingCopy({ context, origin, key: initial.source_key, node, drawingPath, read, graph, openCanvas, stroke, save, observe, report, output });
    assert.deepEqual(report.page_errors, []);
    report.step = "complete";
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    if (page) await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
