import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { unzipSync, zipSync, strFromU8 } from "fflate";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4189";
const canvasCreation = /\/api\/v1\/(?:canvas-workspace|projects\/[^/]+\/canvases)$/;
const output = resolve(process.env.CANVAS_ARCHIVE_BROWSER_OUTPUT || ".runtime/archive-python-browser", `${config.runtimeMode}-${config.archiveWidth}`);
const report = { step: "start", runtime_mode: config.runtimeMode, archive_width: config.archiveWidth, page_errors: [], failed_routes: [] };
await mkdir(output, { recursive: true });
const frontend = resolve("..");
const hostRequire = createRequire(resolve(frontend, "package.json"));
const { createServer: createHostServer, preview: previewHost } = await import(pathToFileURL(hostRequire.resolve("vite")).href);
let canvasServer;
let server;
if (config.runtimeMode === "production") {
    server = await previewHost({ root: frontend, preview: { host: "127.0.0.1", port: 4189, strictPort: true,
        proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
} else {
    canvasServer = await createServer({ server: { host: "127.0.0.1", port: 4190, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
    await canvasServer.listen();
    server = await createHostServer({ root: frontend, server: { host: "127.0.0.1", port: 4189, strictPort: true,
        proxy: { "/canvas-app": { target: "http://127.0.0.1:4190", changeOrigin: true }, "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
    await server.listen();
}
let browser;
let page;
function observe(target) {
    target.on("pageerror", error => report.page_errors.push(error.message));
    target.on("response", response => {
        const path = new URL(response.url()).pathname;
        if (path === "/canvas-app/host-archive.js" && response.ok()) report.production_bridge_loaded = true;
        if (response.status() >= 400 && path.startsWith("/api/")) report.failed_routes.push({ path, status: response.status() });
    });
}
async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function login(username) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    context.setDefaultTimeout(20000);
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return context;
}
async function openArchive(target) {
    await target.setViewportSize({ width: config.archiveWidth, height: 900 });
    await target.goto(`${origin}/projects`);
    await target.getByRole("button", { name: "画布导入导出", exact: true }).click();
    const dialog = target.getByRole("dialog", { name: "画布导入导出", exact: true });
    await dialog.waitFor();
    await dialog.getByText("正在加载画布项目…", { exact: true }).waitFor({ state: "hidden" });
    const box = await dialog.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= config.archiveWidth);
    assert.ok(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth));
    report.archive_no_horizontal_overflow = true;
    await target.screenshot({ path: resolve(output, `archive-${config.archiveWidth}.png`) });
    return dialog;
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
    assert.ok(await pen.isChecked());
    const box = await target.locator(".excalidraw__canvas.interactive").boundingBox();
    assert.ok(box);
    await target.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.5 + offset);
    await target.mouse.down();
    await target.mouse.move(box.x + box.width * 0.56, box.y + box.height * 0.58 + offset, { steps: 12 });
    await target.mouse.up();
}
async function save(target, path) {
    const response = target.waitForResponse(response => new URL(response.url()).pathname === path && response.request().method() === "PUT");
    await target.getByRole("button", { name: "保存绘图", exact: true }).click();
    return (await read(await response)).drawing;
}
async function graph(context, path, predicate) {
    const deadline = Date.now() + 20000;
    do {
        const document = (await read(await context.request.get(`${path}/my-document`))).source_document;
        if (predicate(document)) return document;
        await new Promise(resolve => setTimeout(resolve, 100));
    } while (Date.now() < deadline);
    assert.fail(`画布自动保存未完成：${report.step}`);
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await login(config.username);
    const project = await read(await owner.request.post(`${origin}/api/v1/projects`, {
        headers: { "Idempotency-Key": "archive-browser" }, data: { name: "归档绘图验证", aspect: "16:9", workspace_mode: "infinite_canvas" },
    }));
    const path = `${origin}/api/v1/projects/${project.id}/canvases/${project.primary_canvas_id}`;
    const initial = await read(await owner.request.get(`${path}/my-document`));
    for (const [index, title] of ["归档分组第二画布", "归档分组第三画布"].entries()) {
        await read(await owner.request.post(`${origin}/api/v1/projects/${project.id}/canvases`, {
            headers: { "Idempotency-Key": `archive-group-${index}` }, data: { title },
        }));
    }
    page = await owner.newPage();
    observe(page);
    await openCanvas(page, initial.source_key);
    report.step = "source drawing menu and pointer stroke";
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
    await save(page, drawingPath);
    await page.getByRole("button", { name: "关闭绘图编辑器", exact: true }).click();
    await page.locator(".canvas-drawing-editor-modal").waitFor({ state: "hidden" });
    await graph(owner, path, document => document.nodes[0]?.metadata.drawingShapeCount === 1);
    const original = (await read(await owner.request.get(`${origin}${drawingPath}`))).drawing;
    const originalMedia = [];
    for (const [index, file] of config.media.entries()) {
        report.step = `source ${file.kind} upload beside drawing`;
        await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: file.name, mimeType: file.mimeType, buffer: Buffer.from(file.content, "base64") });
        const withMedia = await graph(owner, path, document => document.nodes.some(item => item.type === file.kind && item.metadata.storageKey && item.metadata.assetId));
        const mediaNode = withMedia.nodes.find(item => item.type === file.kind);
        const mediaBox = await page.locator(`[data-node-id="${mediaNode.id}"]`).boundingBox();
        await page.mouse.move(mediaBox.x + mediaBox.width / 2, mediaBox.y + 15);
        await page.mouse.down();
        await page.mouse.move(1110, 100 + index * 230, { steps: 12 });
        await page.mouse.up();
        await graph(owner, path, document => document.nodes.find(item => item.id === mediaNode.id)?.position.x !== mediaNode.position.x);
        originalMedia.push({ file, node: mediaNode });
    }
    const concurrentEditor = await owner.newPage();
    observe(concurrentEditor);
    await openCanvas(concurrentEditor, initial.source_key);
    await openDrawing(concurrentEditor, node.id);
    await stroke(concurrentEditor, 70);
    let sourceAfterExport;
    let drawingReads = 0;
    await page.route(`${origin}${drawingPath}`, async route => {
        if (route.request().method() !== "GET") return route.continue();
        drawingReads += 1;
        const response = await route.fetch();
        // Complete an actual edit after the export captured the old scene,
        // before it downloads the preview and generation image for that scene.
        if (drawingReads === 1) {
            sourceAfterExport = await save(concurrentEditor, drawingPath);
            assert.equal(sourceAfterExport.shapeCount, 2);
            await concurrentEditor.close();
        }
        return route.fulfill({ response });
    });

    report.step = "source export selection and download";
    const archiveDialog = await openArchive(page);
    await archiveDialog.getByRole("radio", { name: "导出项目", exact: true }).check();
    const exportProject = archiveDialog.getByRole("combobox", { name: "导出项目", exact: true });
    await exportProject.focus();
    await exportProject.press("ArrowDown");
    await exportProject.press("Enter");
    assert.equal(await archiveDialog.locator(".ant-select-selection-item").textContent(), "归档绘图验证");
    const download = page.waitForEvent("download");
    await archiveDialog.getByRole("button", { name: "导出 ZIP", exact: true }).click();
    const archive = await download;
    const archivePath = resolve(output, "drawing.zip");
    await archive.saveAs(archivePath);
    const zip = unzipSync(await readFile(archivePath));
    const manifest = JSON.parse(strFromU8(zip["projects.json"]));
    assert.equal(manifest.projects.length, 3);
    assert.ok(manifest.projects[0].drawingDocuments?.length);
    const exported = manifest.projects[0].drawingDocuments[0];
    for (const { node: source, file } of originalMedia) {
        const exportedMedia = manifest.projects[0].files.find(file => file.storageKey === source.metadata.storageKey);
        assert.ok(exportedMedia);
        assert.deepEqual(Buffer.from(zip[exportedMedia.path]), Buffer.from(file.content, "base64"));
    }
    assert.equal(drawingReads, 1);
    assert.deepEqual(exported.snapshot, original.snapshot);
    const bytes = async (context, id) => (await context.request.get(`${origin}/api/v1/canvas-runtime/resources/${id}/file`)).body();
    assert.deepEqual(Buffer.from(zip[exported.previewPath]), await bytes(owner, original.previewResourceId));
    assert.deepEqual(Buffer.from(zip[exported.generationRender.path]), await bytes(owner, original.render.resourceId));
    report.source_zip_contains_drawing = true;
    assert.notEqual(sourceAfterExport.render.resourceId, original.render.resourceId);
    report.concurrent_export_kept_one_version = true;

    report.step = "unrelated account imports original zip";
    const importer = await login(config.importer);
    page = await importer.newPage();
    observe(page);
    await openArchive(page);
    const published = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-workspace" && response.request().method() === "POST");
    await page.locator('input[type="file"][accept="application/zip,.zip"]').setInputFiles(archivePath);
    const imported = await read(await published);
    await page.getByText("已导入 3 个画布。", { exact: true }).waitFor({ state: "attached" });
    const importedDrawingPath = `/api/v1/canvas-runtime/canvas-projects/${imported.source_key}/drawings/${node.metadata.drawingId}`;
    const restored = (await read(await importer.request.get(`${origin}${importedDrawingPath}`))).drawing;
    assert.deepEqual(restored.snapshot, original.snapshot);
    assert.notEqual(restored.previewResourceId, original.previewResourceId);
    assert.notEqual(restored.render.resourceId, original.render.resourceId);
    assert.deepEqual(await bytes(importer, restored.previewResourceId), Buffer.from(zip[exported.previewPath]));
    assert.deepEqual(await bytes(importer, restored.render.resourceId), Buffer.from(zip[exported.generationRender.path]));
    assert.equal((await importer.request.get(`${origin}${drawingPath}`)).status(), 404);
    const importedGraph = await read(await importer.request.get(`${origin}/api/v1/projects/${imported.project_id}/canvases/${imported.id}/my-document`));
    for (const { node: source, file } of originalMedia) {
        const importedNode = importedGraph.source_document.nodes.find(item => item.id === source.id);
        assert.ok(importedNode?.metadata.assetId);
        assert.notEqual(importedNode.metadata.assetId, source.metadata.assetId);
        assert.notEqual(importedNode.metadata.storageKey, source.metadata.storageKey);
        assert.deepEqual(await bytes(importer, importedNode.metadata.storageKey.slice("resource:".length)), Buffer.from(file.content, "base64"));
        const asset = (await read(await importer.request.get(`${origin}/api/v1/canvas-runtime/assets/${importedNode.metadata.assetId}`))).asset;
        assert.equal(asset.data.storageKey, importedNode.metadata.storageKey);
        for (const name of ["naturalWidth", "naturalHeight", "durationMs", "bytes", "mimeType"]) assert.deepEqual(importedNode.metadata[name], source.metadata[name]);
        if (source.metadata.videoPreview) {
            const preview = importedNode.metadata.videoPreview;
            assert.equal(preview.sourceKey, importedNode.metadata.storageKey);
            assert.notEqual(preview.storageKey, source.metadata.videoPreview.storageKey);
            assert.deepEqual(await bytes(importer, preview.storageKey.slice("resource:".length)), await bytes(owner, source.metadata.videoPreview.storageKey.slice("resource:".length)));
        }
    }
    report.zip_image_and_library_binding_restored = true;
    report.zip_audio_video_and_library_bindings_restored = true;
    report.zip_import_atomic_drawing = true;
    await page.close();

    report.step = "fresh browser edits imported drawing";
    const fresh = await login(config.importer);
    page = await fresh.newPage();
    observe(page);
    await openCanvas(page, imported.source_key);
    await openDrawing(page, node.id);
    await stroke(page, 70);
    const changed = await save(page, importedDrawingPath);
    assert.equal(changed.shapeCount, 2);
    assert.deepEqual((await read(await owner.request.get(`${origin}${drawingPath}`))).drawing, sourceAfterExport);
    report.fresh_import_editable = true;
    await fresh.close();

    report.step = "lost import acknowledgement preserves immutable request";
    page = await importer.newPage();
    observe(page);
    await page.goto(`${origin}/projects`);
    await page.getByRole("button", { name: "画布导入导出", exact: true }).click();
    let accepted;
    const committed = [];
    const attempts = [];
    const deletes = [];
    page.on("request", request => { if (request.method() === "DELETE") deletes.push(request.url()); });
    await page.route(canvasCreation, async route => {
        if (route.request().method() !== "POST") return route.continue();
        attempts.push({ path: new URL(route.request().url()).pathname, body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
        const response = await route.fetch();
        if (response.ok()) committed.push(await response.json());
        if (!accepted && response.ok() && attempts.length === 2) {
            accepted = committed.at(-1);
            return route.fulfill({ status: 503, json: { error: { code: "test_archive_ack_lost", message: "归档回执丢失验证" } } });
        }
        return route.fulfill({ response });
    });
    await page.locator('input[type="file"][accept="application/zip,.zip"]').setInputFiles(archivePath);
    await page.getByText(/已保留导入草稿，请在工作区检查保存状态/).waitFor({ state: "attached" });
    assert.ok(accepted);
    assert.deepEqual(deletes, []);
    assert.equal(attempts.length, 2);
    assert.equal(attempts[0].path, "/api/v1/canvas-workspace");
    assert.equal(attempts[1].path, `/api/v1/projects/${accepted.project_id}/canvases`);
    assert.equal(committed[0].project_id, accepted.project_id);
    const firstCommitted = committed[0];
    const committedBeforeReload = await read(await importer.request.get(`${origin}/api/v1/projects/${firstCommitted.project_id}/canvases/${firstCommitted.id}/my-document`));
    assert.equal(committedBeforeReload.source_document.nodes[0].metadata.drawingRevision, "1");
    const replay = page.waitForResponse(response => canvasCreation.test(new URL(response.url()).pathname) && response.request().method() === "POST" && response.status() === 201);
    await page.reload();
    await page.getByRole("button", { name: "画布导入导出", exact: true }).click();
    await page.getByRole("button", { name: "核对导入", exact: true }).click();
    await read(await replay);
    await page.getByText(/已核对 2 个画布。还有 1 个未导入/).waitFor({ state: "attached" });
    if (config.runtimeMode === "development") await page.waitForFunction(async key => {
        const { readInitialCanvasWrite } = await import("/canvas-app/src/services/canvas-initial-write.ts");
        const { localForageStorageForScope } = await import("/canvas-app/src/lib/localforage-storage.ts");
        const { captureUserScope } = await import("/canvas-app/src/lib/user-scope-guard.ts");
        return await readInitialCanvasWrite(localForageStorageForScope(captureUserScope().userScope), key) === null;
    }, accepted.source_key);
    assert.equal(attempts.length, 3);
    assert.deepEqual(attempts[1], attempts[2]);
    await page.locator('input[type="file"][accept="application/zip,.zip"]').setInputFiles(archivePath);
    await page.getByText("已导入 3 个画布。", { exact: true }).waitFor({ state: "attached" });
    assert.equal(attempts.length, 4);
    assert.equal(committed.at(-1).project_id, firstCommitted.project_id);
    assert.notEqual(attempts[3].key, attempts[0].key);
    const resumedGroup = await read(await importer.request.get(`${origin}/api/v1/projects/${firstCommitted.project_id}/canvases`));
    assert.equal(resumedGroup.items.length, 3);
    assert.deepEqual(new Set(resumedGroup.items.map(item => item.source_key)), new Set([firstCommitted.source_key, accepted.source_key, committed.at(-1).source_key]));
    report.multi_canvas_resume_kept_group = true;
    assert.deepEqual(deletes, []);
    const recovered = (await read(await importer.request.get(`${origin}/api/v1/canvas-runtime/canvas-projects/${firstCommitted.source_key}/drawings/${node.metadata.drawingId}`))).drawing;
    assert.deepEqual(recovered.snapshot, original.snapshot);
    const recoveredGraph = await graph(importer, `${origin}/api/v1/projects/${firstCommitted.project_id}/canvases/${firstCommitted.id}`, document => originalMedia.every(({ node }) => document.nodes.some(item => item.id === node.id && item.metadata.assetId)));
    for (const { node } of originalMedia) {
        const recoveredNode = recoveredGraph.nodes.find(item => item.id === node.id);
        const recoveredAsset = (await read(await importer.request.get(`${origin}/api/v1/canvas-runtime/assets/${recoveredNode.metadata.assetId}`))).asset;
        assert.equal(recoveredAsset.data.storageKey, recoveredNode.metadata.storageKey);
    }
    assert.equal((await read(await importer.request.get(`${origin}/api/v1/canvas-workspace`))).total, 6);
    report.unknown_import_replayed_once = true;

    report.step = "missing zip media is rejected before upload or publication";
    const broken = { ...zip };
    delete broken[exported.previewPath];
    const mutations = [];
    const recordMutation = request => {
        if (request.method() !== "GET" && new URL(request.url()).pathname.startsWith("/api/")) mutations.push(request.url());
    };
    page.on("request", recordMutation);
    await page.locator('input[type="file"][accept="application/zip,.zip"]').setInputFiles({ name: "broken.zip", mimeType: "application/zip", buffer: Buffer.from(zipSync(broken)) });
    await page.getByText(/导入失败：压缩包缺少媒体文件/).waitFor({ state: "attached" });
    page.off("request", recordMutation);
    assert.deepEqual(mutations, []);
    report.invalid_zip_rejected_before_writes = true;
    assert.deepEqual(report.page_errors, []);
    report.step = "complete";
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    if (page) await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    if (config.runtimeMode === "production") await new Promise((resolve, reject) => server.httpServer.close(error => error ? reject(error) : resolve()));
    else await server.close();
    await canvasServer?.close();
}
process.stdout.write(JSON.stringify(report));
