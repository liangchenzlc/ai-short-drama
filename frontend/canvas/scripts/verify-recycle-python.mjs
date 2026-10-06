import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4189";
const output = resolve(".runtime/recycle-python-browser");
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
async function login() {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    context.setDefaultTimeout(20000);
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username: config.username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return context;
}
async function graph(context, path, predicate) {
    const deadline = Date.now() + 20000;
    do {
        const document = (await read(await context.request.get(`${path}/my-document`))).source_document;
        if (predicate(document)) return document;
        await new Promise(resolve => setTimeout(resolve, 100));
    } while (Date.now() < deadline);
    assert.fail(`画布保存未完成：${report.step}`);
}
async function openCanvas(target, key) {
    await target.goto(`${origin}/canvas-app/canvas/${key}`);
    await target.locator("[data-canvas-viewport]").waitFor({ timeout: 60000 });
    const close = target.getByRole("button", { name: "关闭助手", exact: true });
    if (await close.isVisible()) await close.click();
}
async function openDrawing(target, id) {
    await target.locator(`[data-node-id="${id}"]`).dblclick();
    await target.getByRole("button", { name: "保存绘图", exact: true }).waitFor({ timeout: 60000 });
    await target.waitForFunction(() => [...document.querySelectorAll("button")].some(button => button.textContent === "保存绘图" && !button.disabled));
}
async function stroke(target, offset = 0) {
    const pen = target.getByRole("radio", { name: "自由书写", exact: true });
    await pen.locator("..").click();
    const box = await target.locator(".excalidraw__canvas.interactive").boundingBox();
    assert.ok(box);
    await target.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.5 + offset);
    await target.mouse.down();
    await target.mouse.move(box.x + box.width * 0.56, box.y + box.height * 0.58 + offset, { steps: 12 });
    await target.mouse.up();
}
async function saveDrawing(target, drawingPath) {
    const saved = target.waitForResponse(response => new URL(response.url()).pathname === drawingPath && response.request().method() === "PUT");
    await target.getByRole("button", { name: "保存绘图", exact: true }).click();
    return (await read(await saved)).drawing;
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await login();
    const project = await read(await owner.request.post(`${origin}/api/v1/projects`, {
        headers: { "Idempotency-Key": "recycle-browser-project" }, data: { name: "回收站双画布", aspect: "16:9", workspace_mode: "infinite_canvas" },
    }));
    const path = `${origin}/api/v1/projects/${project.id}/canvases/${project.primary_canvas_id}`;
    const initial = await read(await owner.request.get(`${path}/my-document`));
    const second = await read(await owner.request.post(`${origin}/api/v1/projects/${project.id}/canvases`, {
        headers: { "Idempotency-Key": "recycle-browser-secondary" }, data: { title: "第二画布", source_key: "recycle-browser-secondary" },
    }));
    const secondPath = `${origin}/api/v1/projects/${project.id}/canvases/${second.id}`;
    page = await owner.newPage();
    observe(page);
    await openCanvas(page, initial.source_key);
    report.step = "draw and save through original editor";
    await page.mouse.click(640, 390, { button: "right" });
    await page.getByRole("button", { name: "添加节点", exact: true }).last().click();
    await page.getByRole("button", { name: "搜索节点", exact: true }).click();
    await page.getByRole("textbox", { name: "搜索节点", exact: true }).fill("绘图");
    await page.getByRole("button", { name: "绘图", exact: true }).click();
    const created = await graph(owner, path, document => document.nodes.some(node => node.type === "drawing"));
    const node = created.nodes.find(node => node.type === "drawing");
    const drawingPath = `/api/v1/canvas-runtime/canvas-projects/${initial.source_key}/drawings/${node.metadata.drawingId}`;
    await openDrawing(page, node.id);
    await stroke(page);
    await saveDrawing(page, drawingPath);
    await page.getByRole("button", { name: "关闭绘图编辑器", exact: true }).click();
    await page.locator(".canvas-drawing-editor-modal").waitFor({ state: "hidden" });
    await graph(owner, path, doc => doc.nodes[0]?.metadata.drawingShapeCount === 1);
    const drawing = (await read(await owner.request.get(`${origin}${drawingPath}`))).drawing;
    report.step = "upload original image";
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: "recycle.png", mimeType: "image/png", buffer: Buffer.from(config.image, "base64") });
    const withImage = await graph(owner, path, doc => doc.nodes.some(item => item.type === "image" && item.metadata.storageKey && item.metadata.assetId));
    const imageNode = withImage.nodes.find(item => item.type === "image");
    const imageBox = await page.locator(`[data-node-id="${imageNode.id}"]`).boundingBox();
    await page.mouse.move(imageBox.x + imageBox.width / 2, imageBox.y + 15);
    await page.mouse.down();
    await page.mouse.move(1110, 100, { steps: 12 });
    await page.mouse.up();
    await graph(owner, path, doc => doc.nodes.find(item => item.id === imageNode.id)?.position.x !== imageNode.position.x);
    const imageId = imageNode.metadata.storageKey.replace(/^resource:/, "");
    const media = new Map();
    for (const id of [imageId, drawing.previewResourceId, drawing.render.resourceId]) {
        media.set(id, await (await owner.request.get(`${origin}/api/v1/canvas-runtime/resources/${id}/file`)).body());
    }
    // Folder mechanics have a separate UI suite; set up this restoration scenario through the real API.
    const foldersPath = `${origin}/api/v1/canvas-runtime/canvas-folders`;
    await read(await owner.request.put(foldersPath + "/recycle-folder", { data: { folder: { id: "recycle-folder", name: "回收验证文件夹" } } }));
    await page.goto(`${origin}/canvas-app/canvas`);
    await page.getByRole("button", { name: "新建文件夹", exact: true }).waitFor();
    for (const [index, targetPath] of [path, secondPath].entries()) {
        const doc = (await read(await owner.request.get(`${targetPath}/my-document`))).source_document;
        await read(await owner.request.post(`${targetPath}/commits`, {
            headers: { "Idempotency-Key": `recycle-folder-${index}` }, data: { expected_row_version: doc.revision, source_document: { ...doc, folderId: "recycle-folder" } },
        }));
    }
    await page.reload();
    await page.getByRole("button", { name: "回收验证文件夹 文件夹操作", exact: true }).click();
    await page.getByRole("menuitem", { name: "删除文件夹", exact: true }).click();
    await page.getByText("文件夹及其中 2 个项目已移入回收站", { exact: true }).waitFor({ state: "attached" });
    assert.equal((await owner.request.get(path)).status(), 404);
    assert.equal((await owner.request.get(secondPath)).status(), 404);
    await page.getByRole("button", { name: "回收站", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "回收站", exact: true });
    assert.equal(await dialog.locator(".recycle-bin-card").count(), 2);
    report.original_folder_delete_and_two_recycle_cards = true;
    await page.screenshot({ path: resolve(output, "deleted.png") });

    report.step = "fresh browser reads server recycle catalog and real preview";
    const otherDevice = await login();
    page = await otherDevice.newPage();
    observe(page);
    await page.goto(`${origin}/canvas-app/canvas`);
    const catalog = page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/recycle-bin"));
    await page.getByRole("button", { name: "回收站", exact: true }).click();
    assert.equal((await read(await catalog)).items.length, 2);
    await page.locator(".recycle-bin-card").nth(1).waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll(".recycle-bin-preview img")]
        .some(image => image.complete && image.naturalWidth === 37 && image.src.includes("recycle_archive_key=")));
    report.fresh_device_lists_deleted_canvases_and_loads_preview = true;
    const remoteDialog = page.getByRole("dialog", { name: "回收站", exact: true });

    report.step = "committed restore loses response, then server catalog reconciles on reload";
    const requests = [];
    let loseAck = true;
    await page.route("**/canvas-runtime/canvas-projects/*/recycle-restore", async route => {
        requests.push({ path: new URL(route.request().url()).pathname, body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
        const response = await route.fetch();
        assert.equal(response.status(), 200, await response.text());
        if (loseAck) {
            loseAck = false;
            await route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "恢复回执测试中断" } } });
        } else await route.fulfill({ response });
    });
    await remoteDialog.getByRole("checkbox", { name: "全选回收站项目", exact: true }).check();
    await remoteDialog.getByRole("button", { name: "恢复到项目列表", exact: true }).click();
    await page.getByText("服务暂时不可用，请稍后重试。", { exact: true }).waitFor({ state: "attached" });
    assert.equal(await remoteDialog.locator(".recycle-bin-card").count(), 2);
    await page.reload();
    const refreshedCatalog = page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/recycle-bin"));
    await page.getByRole("button", { name: "回收站", exact: true }).click();
    assert.equal((await read(await refreshedCatalog)).items.length, 1);
    await page.waitForFunction(() => document.querySelectorAll(".recycle-bin-card").length === 1);
    assert.equal(requests.length, 1, "刷新只能读取目录，不能重发已成功的恢复");
    await remoteDialog.getByRole("checkbox", { name: "全选回收站项目", exact: true }).check();
    await remoteDialog.getByRole("button", { name: "恢复到项目列表", exact: true }).click();
    await remoteDialog.getByText("回收站是空的", { exact: true }).waitFor();
    assert.equal(requests.length, 2);
    assert.notEqual(requests[0].path, requests[1].path);
    report.lost_restore_ack_reconciles_without_duplicate_write = true;
    const works = (await read(await owner.request.get(`${origin}/api/v1/canvas-workspace?include_documents=true`))).items;
    assert.equal(works.length, 2);
    assert.ok(works.every(item => item.project_id === project.id && !item.source_document.folderId));
    assert.deepEqual(new Set(works.map(item => item.id)), new Set([project.primary_canvas_id, second.id]));
    report.both_canvases_restored_under_original_project = true;
    assert.deepEqual((await read(await owner.request.get(`${origin}${drawingPath}`))).drawing, drawing);
    for (const [id, bytes] of media) {
        assert.deepEqual(await (await owner.request.get(`${origin}/api/v1/canvas-runtime/resources/${id}/file`)).body(), bytes);
    }
    report.drawing_and_media_bytes_retained = true;
    await otherDevice.close();
    await owner.close();
    const fresh = await login();
    page = await fresh.newPage();
    observe(page);
    await openCanvas(page, initial.source_key);
    report.step = "fresh browser edits restored strokes";
    await openDrawing(page, node.id);
    await stroke(page, 65);
    const updated = await saveDrawing(page, drawingPath);
    assert.equal(updated.shapeCount, 2);
    assert.equal(BigInt(updated.revision), BigInt(drawing.revision) + 1n);
    report.fresh_browser_can_edit_restored_drawing = true;
    await page.getByRole("button", { name: "关闭绘图编辑器", exact: true }).click();
    await page.locator(".canvas-drawing-editor-modal").waitFor({ state: "hidden" });
    await graph(fresh, path, doc => doc.nodes.find(item => item.id === node.id)?.metadata.drawingShapeCount === 2);

    report.step = "original permanent deletion retries the same uncertain request";
    await page.goto(`${origin}/canvas-app/canvas`);
    await page.getByRole("button", { name: "未命名画布 画布操作", exact: true }).click();
    await page.getByRole("menuitem", { name: "删除项目", exact: true }).click();
    await page.getByRole("button", { name: "未命名画布 画布操作", exact: true }).waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "回收站", exact: true }).click();
    const finalDialog = page.getByRole("dialog", { name: "回收站", exact: true });
    await finalDialog.locator(".recycle-bin-card").waitFor();
    await finalDialog.getByRole("checkbox", { name: "全选回收站项目", exact: true }).check();
    await finalDialog.getByRole("button", { name: "彻底删除", exact: true }).click();
    const purgeRequests = [];
    await page.route("**/canvas-runtime/canvas-projects/*/recycle-purge", async route => {
        purgeRequests.push({ path: new URL(route.request().url()).pathname, body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
        if (purgeRequests.length === 1) return route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "请求发送前中断" } } });
        const response = await route.fetch();
        assert.equal(response.status(), 200, await response.text());
        if (purgeRequests.length === 2) return route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "处置回执中断" } } });
        await route.fulfill({ response });
    });
    for (let attempt = 0; attempt < 2; attempt++) {
        const failed = page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/recycle-purge") && response.status() === 503);
        await page.getByRole("button", { name: "确认删除", exact: true }).click();
        await failed;
        await page.waitForFunction(() => [...document.querySelectorAll(".recycle-delete-confirm button")]
            .some(button => button.textContent.replace(/\s/g, "") === "确认删除" && !button.classList.contains("ant-btn-loading")));
        assert.equal(await finalDialog.locator(".recycle-bin-card").count(), 1);
    }
    await page.evaluate(async () => {
        const { CANVAS_HISTORY_STORE_KEY } = await import("/canvas-app/src/stores/canvas/use-canvas-history-store.ts");
        const { scopedStorageKey } = await import("/canvas-app/src/lib/user-scope.ts");
        const key = scopedStorageKey(CANVAS_HISTORY_STORE_KEY);
        const original = IDBObjectStore.prototype.put;
        IDBObjectStore.prototype.put = function (...args) {
            if (this.name === "app_state" && args[1] === key && !window.__recycleStorageFailure) {
                window.__recycleStorageFailure = true;
                throw new DOMException("测试注入：回收记录写入失败", "QuotaExceededError");
            }
            return Reflect.apply(original, this, args);
        };
    });
    await page.getByRole("button", { name: "确认删除", exact: true }).click();
    await page.waitForFunction(() => window.__recycleStorageFailure === true);
    await page.getByText("测试注入：回收记录写入失败", { exact: true }).waitFor({ state: "attached" });
    assert.equal(await finalDialog.locator(".recycle-bin-card").count(), 1, "本机落盘失败仍保留原回收卡片和回执");
    await page.getByRole("button", { name: "确认删除", exact: true }).click();
    await finalDialog.getByText("回收站是空的", { exact: true }).waitFor();
    assert.equal(purgeRequests.length, 4);
    assert.deepEqual(purgeRequests[0], purgeRequests[1]);
    assert.deepEqual(purgeRequests[0], purgeRequests[2]);
    assert.deepEqual(purgeRequests[0], purgeRequests[3]);
    report.failed_local_removal_preserves_card_and_archive_key = true;
    assert.equal((await fresh.request.get(path)).status(), 404);
    assert.equal((await fresh.request.get(secondPath)).status(), 200);
    const denied = await fresh.request.post(`${origin}/api/v1/canvas-runtime/canvas-projects/${initial.source_key}/recycle-restore`, {
        headers: { "Idempotency-Key": "purged-cannot-restore" }, data: purgeRequests[0].body,
    });
    assert.equal(denied.status(), 410);
    await fresh.close();
    const finalDevice = await login();
    page = await finalDevice.newPage();
    observe(page);
    await page.goto(`${origin}/canvas-app/canvas`);
    await page.getByRole("button", { name: "回收站", exact: true }).click();
    await page.getByText("回收站是空的", { exact: true }).waitFor();
    report.permanent_delete_survives_unknown_responses_and_fresh_device = true;
    assert.deepEqual(report.page_errors, []);
    report.step = "complete";
    process.stdout.write(JSON.stringify(report));
} catch (error) {
    if (page && !page.isClosed()) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => undefined);
    process.stderr.write(JSON.stringify(report) + "\n" + (error.stack || String(error)));
    process.exitCode = 1;
} finally {
    await browser?.close();
    await server.close();
}
