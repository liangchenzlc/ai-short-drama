import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4188";
const output = resolve(".runtime/tray-clipboard-python-browser");
const report = { step: "start", page_errors: [], failed_routes: [], commits: [] };
await mkdir(output, { recursive: true });
const server = await createServer({ server: { host: "127.0.0.1", port: 4188, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
const responseDetails = [];
function observe(target) {
    target.on("pageerror", error => report.page_errors.push(error.message));
    target.on("response", response => {
        const path = new URL(response.url()).pathname;
        if (response.status() < 400 || !path.startsWith("/api/")) return;
        const entry = { path, status: response.status() };
        report.failed_routes.push(entry);
        responseDetails.push(response.json().then(body => { entry.code = body.error?.code; }).catch(() => {}));
    });
    target.on("request", request => {
        if (new URL(request.url()).pathname.endsWith("/commits")) report.commits.push(request.postDataJSON());
    });
}
async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function createEmpty(context, key, projectId) {
    let canvas;
    if (projectId) {
        canvas = await read(await context.request.post(`${origin}/api/v1/projects/${projectId}/canvases`, {
            headers: { "Idempotency-Key": key }, data: { title: key },
        }));
    } else {
        const project = await read(await context.request.post(`${origin}/api/v1/projects`, {
            headers: { "Idempotency-Key": key }, data: { name: key, aspect: "16:9", workspace_mode: "infinite_canvas" },
        }));
        canvas = { project_id: project.id, id: project.primary_canvas_id };
    }
    const path = `${origin}/api/v1/projects/${canvas.project_id}/canvases/${canvas.id}`;
    const result = await read(await context.request.get(`${path}/my-document`));
    assert.deepEqual(result.source_document.nodes, []);
    return { ...result, path };
}
async function savedDocument(context, canvas, predicate) {
    const deadline = Date.now() + 20000;
    let result;
    do {
        result = await read(await context.request.get(`${canvas.path}/my-document`));
        if (predicate(result.source_document)) return result.source_document;
        await new Promise(resolve => setTimeout(resolve, 100));
    } while (Date.now() < deadline);
    assert.fail(`原版保存未达到预期状态: ${JSON.stringify({ step: report.step, document: result, failed_routes: report.failed_routes })}`);
}
async function openCanvas(target, canvas) {
    await target.bringToFront();
    await target.goto(`${origin}/canvas-app/canvas/${canvas.source_key}`, { timeout: 60000 });
    await target.locator("[data-canvas-viewport]").waitFor({ timeout: 60000 });
    const closeAssistant = target.getByRole("button", { name: "关闭助手", exact: true });
    if (await closeAssistant.isVisible()) await closeAssistant.click();
}
async function readLive(target, canvas) {
    return target.evaluate(async key => {
        const { useCanvasStore } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
        return structuredClone(useCanvasStore.getState().projects.find(item => item.id === key));
    }, canvas.source_key);
}
function checkCopies(source, target) {
    assert.equal(target.nodes.length, source.nodes.length);
    const sourceIds = new Set(source.nodes.map(node => node.id));
    assert.equal(new Set(target.nodes.map(node => node.id)).size, target.nodes.length);
    assert.ok(target.nodes.every(node => !sourceIds.has(node.id)));
    const delta = { x: target.nodes[0].position.x - source.nodes[0].position.x, y: target.nodes[0].position.y - source.nodes[0].position.y };
    for (let i = 0; i < source.nodes.length; i++) {
        assert.equal(target.nodes[i].type, source.nodes[i].type);
        assert.equal(target.nodes[i].metadata.assetId, source.nodes[i].metadata.assetId);
        assert.equal(target.nodes[i].width, source.nodes[i].width);
        assert.equal(target.nodes[i].height, source.nodes[i].height);
        assert.ok(Math.abs(target.nodes[i].position.x - source.nodes[i].position.x - delta.x) < 0.001);
        assert.ok(Math.abs(target.nodes[i].position.y - source.nodes[i].position.y - delta.y) < 0.001);
    }
    assert.equal(target.connections.length, source.connections.length);
    const mappedIds = new Map(source.nodes.map((node, index) => [node.id, target.nodes[index].id]));
    source.connections.forEach((connection, index) => {
        assert.notEqual(target.connections[index].id, connection.id);
        assert.equal(target.connections[index].fromNodeId, mappedIds.get(connection.fromNodeId));
        assert.equal(target.connections[index].toNodeId, mappedIds.get(connection.toNodeId));
    });
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    context.setDefaultTimeout(15000);
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username: config.username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    const uploadedCanvas = await createEmpty(context, "tray-source");
    page = await context.newPage();
    observe(page);
    await openCanvas(page, uploadedCanvas);
    report.step = "source upload";
    const uploaded = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/resources" && response.request().method() === "POST");
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: "tray-image.png", mimeType: "image/png", buffer: Buffer.from(config.image, "base64") });
    const resource = (await read(await uploaded)).resource;
    const uploadDocument = await savedDocument(context, uploadedCanvas, document => document.nodes.length === 1 && document.nodes[0].metadata?.assetId && document.nodes[0].metadata.storageKey === `resource:${resource.id}`);
    const assetId = uploadDocument.nodes[0].metadata.assetId;
    const trayCanvas = await createEmpty(context, "tray-target");
    const sameProjectCanvas = await createEmpty(context, "clipboard-same-project", trayCanvas.project_id);
    const crossProjectCanvas = await createEmpty(context, "clipboard-cross-project");
    await page.close();
    // Only cookies are copied: neither IndexedDB nor session/local storage is seeded.
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies: await context.cookies(), origins: [] }, permissions: ["clipboard-read", "clipboard-write"] });
    fresh.setDefaultTimeout(15000);
    page = await fresh.newPage();
    observe(page);
    await openCanvas(page, trayCanvas);
    report.step = "fresh-context source tray";
    await page.getByRole("button", { name: /^打开素材空间，共/ }).click();
    const tray = page.locator("[data-canvas-asset-tray]");
    await tray.getByRole("button", { name: "素材库 1", exact: true }).waitFor();
    const row = tray.locator("[data-canvas-asset-row]");
    await row.click();
    const clicked = await savedDocument(fresh, trayCanvas, document => document.nodes.length === 1 && document.nodes[0].metadata?.storageKey && document.nodes[0].metadata.storageKey !== `resource:${resource.id}`);
    const targetResource = clicked.nodes[0].metadata.storageKey;
    assert.equal(clicked.nodes[0].metadata.assetId, assetId);
    assert.equal(clicked.nodes[0].metadata.content, `/api/v1/canvas-runtime/resources/${targetResource.slice(9)}/file`);
    await page.waitForFunction(async key => {
        const { hasUnconfirmedCanvasEdits } = await import("/canvas-app/src/services/local-workspace-repository.ts");
        return !hasUnconfirmedCanvasEdits(key);
    }, trayCanvas.source_key);
    report.tray_click_persisted = true;

    report.step = "tray read failure and retry";
    await page.route("**/api/v1/canvas-runtime/assets?*", route => route.fulfill({ status: 503, json: { error: { code: "service_unavailable", message: "素材读取故障注入" } } }), { times: 1 });
    await tray.getByRole("button", { name: "收起素材空间", exact: true }).click();
    await page.getByRole("button", { name: /^打开素材空间，共/ }).click();
    await tray.getByRole("button", { name: "重新读取图片素材", exact: true }).waitFor();
    assert.equal(await row.count(), 0, "failed reads must not present stale rows as confirmed assets");
    await tray.getByRole("button", { name: "重新读取图片素材", exact: true }).click();
    await row.waitFor();
    report.tray_failed_read_retried = true;

    report.step = "source HTML5 tray drag";
    const viewport = page.locator("[data-canvas-viewport]");
    const view = (await readLive(page, trayCanvas)).viewport;
    await row.dragTo(viewport, { targetPosition: { x: 1090, y: 250 } });
    const dragged = await savedDocument(fresh, trayCanvas, document => document.nodes.length === 2 && document.nodes.every(node => node.metadata?.storageKey === targetResource));
    const second = dragged.nodes.find(node => node.id !== clicked.nodes[0].id);
    assert.equal(second.metadata.assetId, assetId);
    assert.ok(Math.abs(second.position.x + second.width / 2 - (1090 - view.x) / view.k) < 3);
    assert.ok(Math.abs(second.position.y + second.height / 2 - (250 - view.y) / view.k) < 3);
    assert.deepEqual(dragged.nodes[0].position, clicked.nodes[0].position);
    report.tray_drag_persisted = true;
    await tray.getByRole("button", { name: "收起素材空间", exact: true }).click();

    report.step = "source pointer connection";
    const firstNode = page.locator(`[data-node-id="${clicked.nodes[0].id}"]`);
    await firstNode.hover();
    const sourcePort = await firstNode.locator('[data-canvas-connection-rail="right"]').boundingBox();
    const destinationPort = await page.locator(`[data-node-id="${second.id}"] [data-canvas-connection-rail="left"]`).boundingBox();
    await page.mouse.move(sourcePort.x + sourcePort.width / 2, sourcePort.y + sourcePort.height / 2);
    await page.mouse.down();
    await page.mouse.move(destinationPort.x + destinationPort.width / 2, destinationPort.y + destinationPort.height / 2, { steps: 16 });
    await page.mouse.up();
    const linked = await savedDocument(fresh, trayCanvas, document => document.connections.length === 1);
    assert.equal(linked.connections[0].fromNodeId, clicked.nodes[0].id);
    assert.equal(linked.connections[0].toNodeId, second.id);
    report.source_connection_persisted = true;

    report.step = "source keyboard copy";
    await viewport.click({ position: { x: 500, y: 600 } });
    await page.keyboard.press("Control+a");
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.keyboard.press("Control+c");
    // copy-to-clipboard 4 uses the asynchronous native Clipboard API.
    await page.waitForFunction(async () => (await navigator.clipboard.readText()).startsWith("open-ai-canvas-nodes:"));
    const marker = await page.evaluate(() => navigator.clipboard.readText());
    assert.ok(marker.startsWith("open-ai-canvas-nodes:"));
    for (const [canvas, flag] of [[sameProjectCanvas, "clipboard_same_project"], [crossProjectCanvas, "clipboard_cross_project"]]) {
        report.step = flag;
        await openCanvas(page, canvas);
        await page.locator("[data-canvas-viewport]").click({ position: { x: 500, y: 600 } });
        await page.keyboard.press("Control+v");
        const pasted = await savedDocument(fresh, canvas, document => document.nodes.length === 2 && document.nodes.every(node => node.metadata?.storageKey?.startsWith("resource:")));
        checkCopies(linked, pasted);
        const locator = pasted.nodes[0].metadata.storageKey;
        assert.ok(pasted.nodes.every(node => node.metadata.storageKey === locator));
        if (canvas === sameProjectCanvas) assert.equal(locator, targetResource);
        else assert.notEqual(locator, targetResource);
        const bytes = await fresh.request.get(`${origin}/api/v1/canvas-runtime/resources/${locator.slice(9)}/file`);
        assert.ok(bytes.ok());
        assert.deepEqual(await bytes.body(), Buffer.from(config.image, "base64"));
        const clean = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies: await context.cookies(), origins: [] } });
        try {
            const restored = await clean.newPage();
            observe(restored);
            await openCanvas(restored, canvas);
            for (const node of pasted.nodes) {
                await restored.waitForFunction(id => {
                    const image = document.querySelector(`[data-node-id="${id}"] img`);
                    return image instanceof HTMLImageElement && image.complete && image.naturalWidth === 37 && image.naturalHeight === 19;
                }, node.id);
            }
            assert.deepEqual((await read(await clean.request.get(`${canvas.path}/my-document`))).source_document.nodes, pasted.nodes);
        } finally {
            await clean.close();
        }
        report[flag] = true;
    }
    const library = await read(await context.request.get(`${origin}/api/v1/canvas-runtime/assets`));
    assert.deepEqual(library.assets.map(asset => asset.id), [assetId]);
    const original = (await read(await context.request.get(`${origin}/api/v1/canvas-runtime/assets/${assetId}`))).asset;
    assert.equal(original.data.storageKey, `resource:${resource.id}`);
    report.original_asset_preserved = true;
    assert.ok(report.commits.every(commit => !JSON.stringify(commit.source_document).includes("blob:")), "new immutable media requests must not contain page-local display addresses");
    report.canonical_media_requests_clean = true;

    report.step = "clipboard across account switch";
    const currentCsrf = (await fresh.cookies()).find(cookie => cookie.name === "sd_csrf");
    await read(await fresh.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin, "X-CSRF-Token": currentCsrf.value }, data: { username: config.otherUsername, password: config.password } }));
    const otherCsrf = (await fresh.cookies()).find(cookie => cookie.name === "sd_csrf");
    await fresh.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": otherCsrf.value });
    const otherCanvas = await createEmpty(fresh, "other-account-canvas");
    await openCanvas(page, otherCanvas);
    await page.locator("[data-canvas-viewport]").click({ position: { x: 500, y: 600 } });
    await page.keyboard.press("Control+v");
    await page.waitForTimeout(800);
    assert.deepEqual((await readLive(page, otherCanvas)).nodes, [], "another account must not restore the original account's node clipboard");
    assert.deepEqual((await read(await fresh.request.get(`${otherCanvas.path}/my-document`))).source_document.nodes, []);
    report.clipboard_account_isolated = true;

    // API setup provides a canonical library larger than one page; the tray itself
    // must fetch, search, and discard deleted cache entries through its original UI.
    report.step = "complete paginated tray library";
    const pagingResource = (await read(await fresh.request.post(`${origin}/api/v1/canvas-runtime/resources`, {
        headers: { "Idempotency-Key": "tray-paging-resource" },
        multipart: { kind: "image", file: { name: "paging.png", mimeType: "image/png", buffer: Buffer.from(config.image, "base64") } },
    }))).resource;
    const pagingUrl = `/api/v1/canvas-runtime/resources/${pagingResource.id}/file`;
    for (let index = 0; index < 101; index++) {
        const id = `paged-${String(index).padStart(3, "0")}`;
        await read(await fresh.request.put(`${origin}/api/v1/canvas-runtime/assets/${id}`, {
            data: { asset: { id, kind: "image", title: id, tags: [], status: "confirmed", coverUrl: pagingUrl,
                data: { dataUrl: pagingUrl, storageKey: `resource:${pagingResource.id}`, width: 37, height: 19, bytes: Buffer.from(config.image, "base64").length, mimeType: "image/png" } } },
        }));
    }
    const secondPage = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname === "/api/v1/canvas-runtime/assets" && url.searchParams.get("page") === "2" && response.ok();
    });
    await openCanvas(page, otherCanvas);
    assert.ok((await read(await secondPage)).assets.some(asset => asset.id === "paged-000"));
    await page.getByRole("button", { name: /^打开素材空间，共/ }).click();
    await tray.getByRole("button", { name: "素材库 101", exact: true }).waitFor();
    await tray.getByRole("searchbox", { name: "搜索图片素材", exact: true }).fill("paged-000");
    await row.getByText("paged-000", { exact: true }).waitFor();
    await read(await fresh.request.delete(`${origin}/api/v1/canvas-runtime/assets/paged-000`));
    await tray.getByRole("button", { name: "收起素材空间", exact: true }).click();
    await page.getByRole("button", { name: /^打开素材空间，共/ }).click();
    await tray.getByRole("button", { name: "素材库 100", exact: true }).waitFor();
    await tray.getByText("没有匹配的图片素材", { exact: true }).waitFor();
    assert.equal(await row.count(), 0);
    report.tray_all_pages_and_deletion_refreshed = true;
    assert.deepEqual(report.page_errors, []);
    report.step = "complete";
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    if (page) await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    throw error;
} finally {
    await Promise.all(responseDetails);
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
