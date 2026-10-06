import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

// 正常裁切使用原 UI 与真实 Python/MySQL/MinIO；仅指定失败请求注入 HTTP 503。
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const resources = "/api/v1/canvas-runtime/resources";
const output = resolve(".runtime/image-tools-python-browser");
const report = { step: "start", page_errors: [], http: [], failure_injections: [] };
const responseDetails = [];
await mkdir(output, { recursive: true });
const server = await createServer({
    server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } },
    logLevel: "silent",
});
await server.listen();
let browser;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}

function observe(target) {
    target.on("pageerror", error => report.page_errors.push(error.message));
    target.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (!route.startsWith("/api/")) return;
        const entry = { path: route, method: response.request().method(), status: response.status() };
        report.http.push(entry);
        if (response.status() >= 400) responseDetails.push(response.json().then(body => {
            entry.code = body.error?.code;
        }).catch(() => {}));
    });
}

async function login(username) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference" });
    context.setDefaultTimeout(20000);
    context.setDefaultNavigationTimeout(60000);
    await read(await context.request.post(`${origin}/api/v1/auth/login`, {
        headers: { Origin: origin }, data: { username, password: config.password },
    }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf, "真实登录应创建 CSRF cookie");
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return context;
}

async function graph(context, predicate) {
    let current;
    for (let attempt = 0; attempt < 100; attempt++) {
        current = (await read(await context.request.get(`${origin}${path}/my-document`))).source_document;
        if (predicate(current)) return current;
        await new Promise(resolve => setTimeout(resolve, 150));
    }
    assert.fail(`真实 API 画布没有达到预期状态：${JSON.stringify(current)}`);
}

async function openCanvas(target) {
    await target.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await target.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await target.evaluate(() => document.fonts.ready);
}

async function openCrop(target, sourceId) {
    const source = target.locator(`[data-node-id="${sourceId}"]`);
    await source.waitFor({ state: "visible" });
    // 原 toolbar 依据单选节点显示。刷新不恢复上传时的选中状态，先执行真实单击。
    await source.click();
    await source.hover();
    await target.getByRole("button", { name: "图片工具", exact: true }).click();
    await target.getByRole("menuitem", { name: "裁切", exact: true }).click();
    const crop = source.locator('[data-image-crop-inline="true"]');
    await crop.waitFor({ state: "visible" });
    assert.equal(await crop.getByRole("button", { name: /^resize-/ }).count(), 8);
    return crop;
}

async function selectedRect(crop) {
    return crop.locator(".cursor-move").evaluate(element => ({
        x: parseFloat(element.style.left) / 100,
        y: parseFloat(element.style.top) / 100,
        width: parseFloat(element.style.width) / 100,
        height: parseFloat(element.style.height) / 100,
    }));
}

async function cachedProject(target) {
    return target.evaluate(async key => {
        const { CANVAS_STORE_KEY, useCanvasStore } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
        const { localForageStorageForScope } = await import("/canvas-app/src/lib/localforage-storage.ts");
        const { getActiveUserScope } = await import("/canvas-app/src/lib/user-scope.ts");
        const cache = JSON.parse(await localForageStorageForScope(getActiveUserScope()).getItem(CANVAS_STORE_KEY));
        return { scope: getActiveUserScope(), memory: useCanvasStore.getState().projects.find(item => item.id === key), cached: cache?.state?.projects?.find(item => item.id === key) };
    }, config.sourceKey);
}

async function newLocalChild(target, previousIds) {
    let snapshot;
    for (let attempt = 0; attempt < 100; attempt++) {
        snapshot = await cachedProject(target);
        const child = snapshot.memory?.nodes.find(node => !previousIds.includes(node.id));
        if (child && snapshot.cached?.nodes.some(node => node.id === child.id)) return { child, snapshot };
        await new Promise(resolve => setTimeout(resolve, 150));
    }
    assert.fail(`派生图未保留在本人内存和 IndexedDB 草稿：${JSON.stringify(snapshot)}`);
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await login(config.username);
    const member = await login(config.memberUsername);
    const outsider = await login(config.outsiderUsername);
    page = await owner.newPage();
    observe(page);
    await openCanvas(page);
    report.step = "原上传 input 准备图片";
    const originalBytes = Buffer.from(config.image, "base64");
    const uploaded = page.waitForResponse(response => new URL(response.url()).pathname === resources && response.request().method() === "POST");
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: "crop-pixel-pattern.png", mimeType: "image/png", buffer: originalBytes });
    report.source_resource_id = (await read(await uploaded)).resource.id;
    const original = await graph(owner, document => document.nodes.length === 1 && document.nodes[0].metadata?.assetId);
    const source = original.nodes[0];
    report.source_node_id = source.id;
    const uploadedOriginal = await owner.request.get(`${origin}${resources}/${report.source_resource_id}/file`);
    assert.ok(uploadedOriginal.ok());
    assert.deepEqual(await uploadedOriginal.body(), originalBytes);

    report.step = "原裁切拖拽与取消";
    let crop = await openCrop(page, source.id);
    assert.deepEqual(await selectedRect(crop), { x: 0.1, y: 0.1, width: 0.8, height: 0.8 });
    const selection = await crop.locator(".cursor-move").boundingBox();
    await page.mouse.move(selection.x + selection.width / 2, selection.y + selection.height / 2);
    await page.mouse.down();
    await page.mouse.move(selection.x + selection.width / 2 + 5, selection.y + selection.height / 2 + 3, { steps: 8 });
    await page.mouse.up();
    assert.notDeepEqual(await selectedRect(crop), { x: 0.1, y: 0.1, width: 0.8, height: 0.8 });
    const cancelStart = report.http.length;
    await crop.getByRole("button", { name: "取消裁切", exact: true }).click();
    await crop.waitFor({ state: "hidden" });
    assert.equal((await graph(owner, document => document.nodes.length === 1)).connections.length, 0);
    assert.equal(report.http.slice(cancelStart).filter(entry => entry.path === resources && entry.method === "POST").length, 0);
    report.cancel_kept_source = true;

    report.step = "原确认裁切与真实派生保存";
    crop = await openCrop(page, source.id);
    report.crop_rect = await selectedRect(crop);
    assert.deepEqual(report.crop_rect, { x: 0.1, y: 0.1, width: 0.8, height: 0.8 });
    const croppedUpload = page.waitForResponse(response => new URL(response.url()).pathname === resources && response.request().method() === "POST");
    await crop.getByRole("button", { name: "确认裁切", exact: true }).click();
    const resource = (await read(await croppedUpload)).resource;
    assert.equal(resource.width, 30);
    assert.equal(resource.height, 16);
    const saved = await graph(owner, document => document.nodes.length === 2 && document.nodes.every(node => node.metadata?.assetId));
    const child = saved.nodes.find(node => node.id !== source.id);
    assert.equal(child.position.x, source.position.x + source.width + 96);
    assert.equal(child.position.y, source.position.y);
    assert.equal(child.title, `${source.title} · 裁剪`);
    assert.equal(child.metadata.generatedFromNodeId, source.id);
    assert.equal(child.metadata.resultOrigin, "derived");
    assert.equal(child.metadata.storageKey, `resource:${resource.id}`);
    assert.ok(saved.connections.some(edge => edge.fromNodeId === source.id && edge.toNodeId === child.id));
    assert.deepEqual(saved.nodes.find(node => node.id === source.id), source);
    report.crop_node_id = child.id;
    report.crop_resource_id = resource.id;
    report.crop_asset_id = child.metadata.assetId;
    const cropBytes = await (await owner.request.get(`${origin}${resources}/${resource.id}/file`)).body();
    report.crop_sha256 = createHash("sha256").update(cropBytes).digest("hex");
    report.crop_bytes_base64 = cropBytes.toString("base64");
    report.source_sha256 = createHash("sha256").update(originalBytes).digest("hex");
    report.crop_saved = true;

    report.step = "无 IndexedDB 的新 context 刷新真实媒体";
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies: await owner.cookies(), origins: [] } });
    fresh.setDefaultTimeout(20000);
    const freshPage = await fresh.newPage();
    observe(freshPage);
    const freshDownloads = new Set();
    freshPage.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (response.ok() && route.endsWith("/file")) freshDownloads.add(route);
    });
    await openCanvas(freshPage);
    await freshPage.waitForFunction(id => {
        const image = document.querySelector(`[data-node-id="${id}"] img`);
        return image instanceof HTMLImageElement && image.complete && image.naturalWidth === 30 && image.naturalHeight === 16;
    }, child.id);
    assert.ok(freshDownloads.has(`${resources}/${resource.id}/file`), "新 context 应读取真实 MinIO 资源接口");
    await fresh.close();
    report.fresh_context_restored = true;

    report.step = "原裁切保存失败保留草稿";
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    crop = await openCrop(page, source.id);
    const commitUrl = `${origin}${path}/commits`;
    await page.route(commitUrl, route => {
        if (route.request().method() !== "POST") return route.continue();
        report.failure_injections.push({ stage: "canvas_commit", path: `${path}/commits`, status: 503, key: route.request().headers()["idempotency-key"] });
        return route.fulfill({ status: 503, json: { error: { code: "test_canvas_commit_unavailable", message: "仅画布提交 HTTP 503 故障注入" } } });
    });
    const failedUpload = page.waitForResponse(response => new URL(response.url()).pathname === resources && response.request().method() === "POST");
    const failedCommit = page.waitForResponse(response => new URL(response.url()).pathname === `${path}/commits` && response.status() === 503);
    await crop.getByRole("button", { name: "确认裁切", exact: true }).click();
    const privateResource = (await read(await failedUpload)).resource;
    await failedCommit;
    // 源 CSS 隐藏全局 toast；保存状态自动打开持续可见的就近错误。
    await page.getByRole("button", { name: "画布保存状态：云端未保存", exact: true }).waitFor({ state: "visible" });
    const saveError = page.getByRole("alert").filter({ hasText: "服务暂时不可用，请稍后重试。" });
    await saveError.waitFor({ state: "visible" });
    report.commit_failure_visible_error = await saveError.innerText();
    const draft = await newLocalChild(page, saved.nodes.map(node => node.id));
    assert.equal(draft.child.metadata.storageKey, `resource:${privateResource.id}`);
    const canonical = await graph(owner, document => document.nodes.length === 2);
    assert.ok(!canonical.nodes.some(node => node.id === draft.child.id));
    assert.ok(!canonical.connections.some(edge => edge.toNodeId === draft.child.id));
    assert.equal((await member.request.get(`${origin}${resources}/${privateResource.id}/file`)).status(), 404);
    report.commit_failure_resource_id = privateResource.id;
    report.commit_failure_node_id = draft.child.id;
    report.commit_failure_kept_draft = true;
    report.commit_failure_no_shared_product = true;
    await page.unroute(commitUrl);
    const beforeRecoveryUploads = report.http.filter(entry => entry.path === resources && entry.method === "POST").length;
    await page.locator("[data-canvas-viewport]").click({ position: { x: 700, y: 650 } });
    await page.keyboard.press("Control+s");
    const recovered = await graph(owner, document => document.nodes.length === 3 && document.nodes.some(node => node.id === draft.child.id));
    assert.equal(report.http.filter(entry => entry.path === resources && entry.method === "POST").length, beforeRecoveryUploads, "恢复保存应沿用派生图，不重新上传/裁切");
    assert.equal(recovered.nodes.filter(node => node.id === draft.child.id).length, 1);
    assert.equal((await member.request.get(`${origin}${resources}/${privateResource.id}/file`)).status(), 200);
    report.commit_failure_recovered_same_child = true;

    report.step = "原裁切上传失败与提交失败区分";
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    crop = await openCrop(page, source.id);
    const failureStart = report.http.length;
    await page.route(`${origin}${resources}`, route => {
        if (route.request().method() !== "POST") return route.continue();
        report.failure_injections.push({ stage: "resource_upload", path: resources, status: 503, key: route.request().headers()["x-idempotency-key"] });
        return route.fulfill({ status: 503, json: { error: { code: "test_resource_upload_unavailable", message: "仅资源上传 HTTP 503 故障注入" } } });
    });
    const uploadFailure = page.waitForResponse(response => new URL(response.url()).pathname === resources && response.status() === 503);
    await crop.getByRole("button", { name: "确认裁切", exact: true }).click();
    await uploadFailure;
    await page.getByRole("button", { name: "画布保存状态：云端未保存", exact: true }).waitFor({ state: "visible" });
    const uploadError = page.getByRole("alert").filter({ hasText: "画布媒体尚未持久化，原有草稿已保留，请完成上传后重试" });
    await uploadError.waitFor({ state: "visible" });
    report.upload_failure_visible_error = await uploadError.innerText();
    const localOnly = await newLocalChild(page, recovered.nodes.map(node => node.id));
    assert.ok(localOnly.child.metadata.storageKey.startsWith(`image:${localOnly.snapshot.scope}:`));
    const localBytes = await page.evaluate(async storageKey => {
        const { getImageBlob } = await import("/canvas-app/src/services/image-storage.ts");
        const blob = await getImageBlob(storageKey);
        return blob ? Array.from(new Uint8Array(await blob.arrayBuffer())) : null;
    }, localOnly.child.metadata.storageKey);
    assert.deepEqual(Buffer.from(localBytes), cropBytes);
    const afterUploadFailure = await graph(owner, document => document.nodes.length === 3);
    assert.ok(!afterUploadFailure.nodes.some(node => node.id === localOnly.child.id));
    report.upload_failure_node_id = localOnly.child.id;
    report.upload_failure_kept_local_bytes = true;
    report.upload_failure_no_saved_product = true;
    report.upload_failure_commit_responses = report.http.slice(failureStart).filter(entry => entry.path === `${path}/commits`);
    assert.ok(!report.upload_failure_commit_responses.some(entry => entry.status < 400), "上传未确认时不能提交成功产物");

    report.step = "共享作品与私人资产权限";
    assert.deepEqual(await (await member.request.get(`${origin}${resources}/${resource.id}/file`)).body(), cropBytes);
    assert.equal((await member.request.get(`${origin}/api/v1/canvas-runtime/assets/${child.metadata.assetId}`)).status(), 404);
    assert.equal((await outsider.request.get(`${origin}${path}/my-document`)).status(), 404);
    assert.equal((await outsider.request.get(`${origin}${resources}/${resource.id}/file`)).status(), 404);
    assert.equal((await outsider.request.get(`${origin}/api/v1/canvas-runtime/assets/${child.metadata.assetId}`)).status(), 404);
    assert.deepEqual(await (await owner.request.get(`${origin}${resources}/${report.source_resource_id}/file`)).body(), originalBytes);
    report.permissions_checked = true;
    await Promise.all(responseDetails);
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
