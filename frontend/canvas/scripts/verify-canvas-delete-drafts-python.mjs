import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4189";
const output = resolve(".runtime/canvas-delete-drafts-python", config.runId, config.scenario);
await mkdir(output, { recursive: true });
const report = { scenario: config.scenario, run_id: config.runId, step: "start", page_errors: [], deletes: [], drawing_deletes: 0, status_reads: [] };
const server = await createServer({
    server: { host: "127.0.0.1", port: 4189, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } },
    logLevel: "silent",
});
await server.listen();
let browser;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}

async function login(username = config.username) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    context.setDefaultTimeout(20000);
    await read(await context.request.post(`${origin}/api/v1/auth/login`, {
        headers: { Origin: origin }, data: { username, password: config.password },
    }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return context;
}

async function graph(context, predicate) {
    const deadline = Date.now() + 20000;
    do {
        const document = (await read(await context.request.get(origin + config.path + "/my-document"))).source_document;
        if (predicate(document)) return document;
        await new Promise(resolve => setTimeout(resolve, 100));
    } while (Date.now() < deadline);
    assert.fail(`画布保存未完成：${report.step}`);
}

async function openCanvas(key) {
    await page.goto(`${origin}/canvas-app/canvas/${key}`);
    await page.locator("[data-canvas-viewport]").waitFor({ timeout: 60000 });
    const close = page.getByRole("button", { name: "关闭助手", exact: true });
    if (await close.isVisible()) await close.click();
}

async function stroke(offset) {
    await page.getByRole("radio", { name: "自由书写", exact: true }).locator("..").click();
    const box = await page.locator(".excalidraw__canvas.interactive").boundingBox();
    assert.ok(box);
    await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.5 + offset);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.56, box.y + box.height * 0.58 + offset, { steps: 12 });
    await page.mouse.up();
}

async function saveDrawing(drawingPath, expectedStatus) {
    const saved = page.waitForResponse(response => new URL(response.url()).pathname === drawingPath && response.request().method() === "PUT");
    await page.getByRole("button", { name: "保存绘图", exact: true }).click();
    const response = await saved;
    assert.equal(response.status(), expectedStatus, await response.text());
    return expectedStatus === 200 ? (await read(response)).drawing : null;
}

async function localState(drawingId) {
    return page.evaluate(async ({ key, drawingId, scope, otherScope }) => {
        const { readCanvasSyncDrafts } = await import("/canvas-app/src/services/canvas-sync-drafts.ts");
        const { readCanvasDeletion } = await import("/canvas-app/src/services/canvas-deletion-attempt.ts");
        const { localForageInstance, localForageStorageForScope, DRAWING_DOCUMENTS_STORE_NAME, DRAWING_PREVIEWS_STORE_NAME, DRAWING_GENERATION_RENDERS_STORE_NAME } = await import("/canvas-app/src/lib/localforage-storage.ts");
        const cacheKey = `${scope}:${key}:${drawingId}`;
        const documents = localForageInstance(DRAWING_DOCUMENTS_STORE_NAME);
        const cache = await documents.getItem(cacheKey);
        const blobs = [];
        for (const name of [DRAWING_PREVIEWS_STORE_NAME, DRAWING_GENERATION_RENDERS_STORE_NAME]) {
            const storage = localForageInstance(name);
            for (const id of (await storage.keys()).filter(id => id === cacheKey || id.startsWith(cacheKey + "\0g")).sort()) {
                const value = await storage.getItem(id);
                const blob = value instanceof Blob ? value : value?.blob;
                if (!blob) throw new Error("绘图缓存缺少原始字节");
                const hash = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
                blobs.push({ store: name, key: id, type: blob.type, size: blob.size, sha256: [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, "0")).join("") });
            }
        }
        return {
            drafts: await readCanvasSyncDrafts(key, scope), cache, blobs,
            deletion: await readCanvasDeletion(localForageStorageForScope(scope), scope, key),
            other_drafts: await readCanvasSyncDrafts(key, otherScope),
            other_deletion: await readCanvasDeletion(localForageStorageForScope(otherScope), otherScope, key),
            other_cache: await documents.getItem(`${otherScope}:${key}:${drawingId}`),
        };
    }, { key: config.sourceKey, drawingId, scope: config.userId, otherScope: config.otherUserId });
}

function assertDraftsPreserved(before, after) {
    assert.deepEqual(after.drafts, before.drafts);
    assert.deepEqual(after.cache, before.cache);
    assert.deepEqual(after.blobs, before.blobs);
    assert.deepEqual(after.other_drafts, []);
    assert.equal(after.other_deletion, null);
    assert.equal(after.other_cache, null);
}

async function deleteFromMenu() {
    await page.getByRole("button", { name: "打开画布菜单", exact: true }).click();
    await page.getByRole("menuitem", { name: "删除项目", exact: true }).click();
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const context = await login();
    page = await context.newPage();
    page.on("pageerror", error => report.page_errors.push(error.message));
    page.on("request", request => {
        if (request.method() === "DELETE" && new URL(request.url()).pathname.includes("/drawings/")) report.drawing_deletes += 1;
    });
    page.on("response", response => {
        if (new URL(response.url()).pathname.endsWith("/recycle-status")) report.status_reads.push(response.status());
    });
    await openCanvas(config.sourceKey);
    report.step = "create and save an original drawing through the editor";
    await page.mouse.click(640, 390, { button: "right" });
    await page.getByRole("button", { name: "添加节点", exact: true }).last().click();
    await page.getByRole("button", { name: "搜索节点", exact: true }).click();
    await page.getByRole("textbox", { name: "搜索节点", exact: true }).fill("绘图");
    await page.getByRole("button", { name: "绘图", exact: true }).click();
    const created = await graph(context, document => document.nodes.some(node => node.type === "drawing"));
    const node = created.nodes.find(node => node.type === "drawing");
    const drawingPath = `/api/v1/canvas-runtime/canvas-projects/${config.sourceKey}/drawings/${node.metadata.drawingId}`;
    await page.locator(`[data-node-id="${node.id}"]`).dblclick();
    await page.getByRole("button", { name: "保存绘图", exact: true }).waitFor({ timeout: 60000 });
    await page.waitForFunction(() => [...document.querySelectorAll("button")].some(button => button.textContent === "保存绘图" && !button.disabled));
    await stroke(0);
    const original = await saveDrawing(drawingPath, 200);
    assert.equal(original.shapeCount, 1);
    report.original_snapshot = original.snapshot;
    report.drawing_id = node.metadata.drawingId;
    report.drawing_revision = original.revision;
    report.original_media = [];
    for (const id of new Set([original.previewResourceId, original.render?.resourceId].filter(Boolean))) {
        const file = await context.request.get(`${origin}/api/v1/canvas-runtime/resources/${id}/file?proxy=1`);
        assert.ok(file.ok(), await file.text());
        report.original_media.push({ id, bytes: (await file.body()).toString("base64") });
    }

    report.step = "retain an unacknowledged private drawing and a separate graph draft";
    let draftPuts = 0;
    await page.route(origin + drawingPath, route => {
        if (route.request().method() !== "PUT") return route.continue();
        draftPuts += 1;
        return route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "测试注入：绘图提交未受理" } } });
    });
    await stroke(80);
    await saveDrawing(drawingPath, 503);
    await page.waitForFunction(async ({ key, id, scope }) => {
        const { peekCanvasDrawingCacheForTests } = await import("/canvas-app/src/lib/canvas/canvas-drawing-storage.ts");
        const cache = await peekCanvasDrawingCacheForTests(key, id, scope);
        return cache?.draft?.document.shapeCount === 2 && cache.committed?.shapeCount === 1;
    }, { key: config.sourceKey, id: node.metadata.drawingId, scope: config.userId });
    const unknownDrawing = await localState(node.metadata.drawingId);
    await page.reload();
    await page.locator("[data-canvas-viewport]").waitFor({ timeout: 60000 });
    const reloadedDrawing = await localState(node.metadata.drawingId);
    assert.deepEqual(reloadedDrawing.cache, unknownDrawing.cache);
    assert.deepEqual(reloadedDrawing.blobs, unknownDrawing.blobs);
    assert.equal(draftPuts, 1);
    report.drawing_reload_keeps_unknown_draft = true;
    const savedGraph = await graph(context, document => document.nodes.some(node => node.metadata?.drawingShapeCount === 1));
    await page.evaluate(async key => {
        const { openLocalCanvasProject } = await import("/canvas-app/src/services/local-workspace-repository.ts");
        const { preserveCanvasSyncDraft } = await import("/canvas-app/src/services/canvas-sync-drafts.ts");
        const snapshot = structuredClone(openLocalCanvasProject(key));
        snapshot.nodes[0].metadata.prompt = "本人独立保存的待处理草稿";
        await preserveCanvasSyncDraft(snapshot);
    }, config.sourceKey);
    const before = await localState(node.metadata.drawingId);
    assert.equal(before.drafts.length, 1);
    assert.equal(before.cache.draft.document.shapeCount, 2);
    assert.ok(before.blobs.length >= 4 && before.blobs.every(blob => blob.size > 0));
    assert.equal(before.deletion, null);
    report.canvas_revision = savedGraph.revision;
    let failures = 1;
    await page.route(origin + config.path, async route => {
        if (route.request().method() !== "DELETE") return route.continue();
        report.deletes.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() });
        if (failures-- > 0) {
            if (config.scenario !== "before_dispatch") {
                const response = await route.fetch();
                assert.equal(response.status(), 200, await response.text());
            }
            return route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "测试注入：删除结果未知" } } });
        }
        const response = await route.fetch();
        assert.equal(response.status(), 200, await response.text());
        return route.fulfill({ response });
    });
    report.step = "delete through original menu with an unknown result";
    await deleteFromMenu();
    await page.getByText("删除画布失败：服务暂时不可用，请稍后重试。", { exact: true }).waitFor({ state: "attached" });
    const frozen = await localState(node.metadata.drawingId);
    assertDraftsPreserved(before, frozen);
    assert.equal(report.deletes.length, 1);
    assert.equal(frozen.deletion.project.revision, savedGraph.revision);
    assert.equal(frozen.deletion.archiveKey, report.deletes[0].key);
    report.archive_key = frozen.deletion.archiveKey;

    if (config.scenario === "superseded") {
        const peer = await login();
        await read(await peer.request.post(`${origin}/api/v1/canvas-runtime/canvas-projects/${config.sourceKey}/recycle-restore`, {
            headers: { "Idempotency-Key": "delete-drafts-peer-restore" }, data: { archive_key: frozen.deletion.archiveKey },
        }));
        await peer.close();
    }

    report.step = "reload and reconcile through reads without replaying writes";
    await page.goto(`${origin}/canvas-app/canvas/${config.siblingKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ timeout: 60000 });
    await page.evaluate(async () => {
        const { hydrateLocalCanvasProjectsFromBackend } = await import("/canvas-app/src/services/local-workspace-repository.ts");
        try { await hydrateLocalCanvasProjectsFromBackend({ strict: true }); }
        catch (error) { return error.message; }
    });
    const afterReload = await localState(node.metadata.drawingId);
    assertDraftsPreserved(before, afterReload);
    assert.equal(report.deletes.length, 1, "刷新只能核对回执，不能重发删除");
    assert.equal(draftPuts, 1, "刷新不能重发未知绘图提交");
    assert.equal(report.drawing_deletes, 0, "独立草稿引用的绘图不能被整图删除清理");
    assert.ok(report.status_reads.length > 0);
    if (config.scenario === "before_dispatch") {
        assert.deepEqual(afterReload.deletion, frozen.deletion);
        assert.ok(report.status_reads.includes(404));
    } else {
        assert.equal(afterReload.deletion, null);
        assert.ok(report.status_reads.includes(200));
    }

    if (config.scenario === "before_dispatch") {
        report.step = "explicitly retry the original frozen request";
        await openCanvas(config.sourceKey);
        await deleteFromMenu();
        await page.waitForURL(`**/canvas/${config.siblingKey}`);
        const afterRetry = await localState(node.metadata.drawingId);
        assertDraftsPreserved(before, afterRetry);
        assert.equal(afterRetry.deletion, null);
        assert.equal(report.deletes.length, 2);
        assert.deepEqual(report.deletes[0], report.deletes[1]);
    }
    report.private_graph_and_drawing_drafts_preserved = true;
    report.original_blobs_preserved = true;
    report.no_automatic_write_replay = true;
    report.account_isolation = true;
    report.drawing_puts = draftPuts;
    report.blob_count = before.blobs.length;
    assert.deepEqual(report.page_errors, []);
    report.step = "complete";
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    process.stdout.write(JSON.stringify(report));
} catch (error) {
    if (page && !page.isClosed()) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => undefined);
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    const diagnostic = Object.fromEntries(Object.entries(report).filter(([key]) => !["original_snapshot", "original_media"].includes(key)));
    process.stderr.write(JSON.stringify(diagnostic) + "\n" + (error.stack || String(error)));
    process.exitCode = 1;
} finally {
    await browser?.close();
    await server.close();
}
