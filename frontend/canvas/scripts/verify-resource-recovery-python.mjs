import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4196";
const resourcePath = "/api/v1/canvas-runtime/resources";
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = { step: "start", scenario: config.scenario, width: config.width, page_errors: [], http_errors: [], uploads: [], task_posts: 0 };
const files = config.media.map(file => ({ ...file, buffer: Buffer.from(file.content, "base64") }));
const sha = value => createHash("sha256").update(value).digest("hex");
const server = await createServer({ cacheDir: resolve(output, "node_modules/.vite"), server: { host: "127.0.0.1", port: 4196, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let owner;
let page;
let releaseUpload;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}

async function eventually(reader, predicate, timeout = 60000) {
    const deadline = Date.now() + timeout;
    let value;
    while (Date.now() < deadline) {
        value = await reader();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`资源恢复未达到预期：${report.step} ${JSON.stringify(value)}`);
}

async function context(cookies) {
    const value = await browser.newContext({ viewport: { width: config.width, height: 900 }, ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    value.setDefaultTimeout(30000);
    value.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await value.request.post(origin + "/api/v1/auth/login", { headers: { Origin: origin }, data: { username: config.username, password: config.password } }));
    const csrf = (await value.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await value.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return value;
}

async function graph(predicate = () => true) {
    return eventually(async () => (await read(await owner.request.get(origin + config.path + "/my-document"))).source_document, predicate);
}

async function assertNoRemoteImage() {
    const saved = await graph();
    assert.ok(saved.nodes.length <= 1);
    for (const node of saved.nodes) {
        assert.equal(node.type, "image");
        assert.ok(!node.metadata?.storageKey && !node.metadata?.content && !node.metadata?.assetId);
    }
}

async function local() {
    return page.evaluate(async ({ key, otherScope }) => {
        const { openLocalCanvasProject, pendingExternalCanvasRevision } = await import("/canvas-app/src/services/local-workspace-repository.ts");
        const { peekCanvasOperationJournal } = await import("/canvas-app/src/services/canvas-operation-journal.ts");
        const { getImageBlob } = await import("/canvas-app/src/services/image-storage.ts");
        const { readCanvasSyncDrafts } = await import("/canvas-app/src/services/canvas-sync-drafts.ts");
        const project = openLocalCanvasProject(key);
        const images = [];
        for (const node of project?.nodes || []) {
            if (node.type !== "image" || !node.metadata?.storageKey?.startsWith("image:")) continue;
            const blob = await getImageBlob(node.metadata.storageKey);
            if (!blob) continue;
            const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
            images.push({ node_id: node.id, key: node.metadata.storageKey, size: blob.size, mime: blob.type, name: blob instanceof File ? blob.name : null, sha256: [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("") });
        }
        return { project, images, journal: peekCanvasOperationJournal(key), external: pendingExternalCanvasRevision(key), other_drafts: await readCanvasSyncDrafts(key, otherScope) };
    }, { key: config.sourceKey, otherScope: config.otherUserId });
}

async function open() {
    page = await owner.newPage();
    page.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    page.on("request", request => {
        const path = new URL(request.url()).pathname;
        if (request.method() === "POST" && path === resourcePath) {
            report.uploads.push({ key: request.headers()["x-idempotency-key"], canvas_key: request.headers()["x-canvas-key"] });
        }
        if (request.method() === "POST" && path.endsWith("/tasks")) report.task_posts++;
    });
    page.on("response", response => {
        if (response.status() >= 400 && new URL(response.url()).pathname.startsWith("/api/")) report.http_errors.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
    });
    await page.goto(origin + "/canvas-app/canvas/" + config.sourceKey);
    await page.locator("[data-canvas-editor] [data-canvas-viewport]").waitFor();
    const close = page.getByRole("button", { name: "关闭助手", exact: true });
    if (await close.isVisible()) await close.click();
    await page.evaluate(() => document.fonts.ready);
}

async function fit() {
    const focus = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focus.isVisible()) await focus.getByRole("button", { name: "退出专注模式", exact: true }).press("Control+0");
    else await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
}

async function preview(node, label) {
    await fit();
    const card = page.locator(`[data-node-id="${node.id}"]`);
    await card.waitFor({ state: "visible" });
    if (node.type === "image") {
        await eventually(() => card.locator("img").first().evaluate(element => element.complete && element.naturalWidth === 37 && element.naturalHeight === 19), Boolean);
    } else if (node.type === "video") {
        await card.getByRole("button", { name: /^播放 / }).click();
        await eventually(() => card.locator("video").evaluate(element => !element.paused && element.currentTime > 0.1 && element.readyState >= 2 && element.videoWidth === 160 && element.videoHeight === 90), Boolean);
        report[label + "_video"] = await card.locator("video").evaluate(element => ({ duration: element.duration, width: element.videoWidth, height: element.videoHeight, time: element.currentTime }));
    } else if (node.type === "audio") {
        await card.getByRole("button", { name: /^播放/ }).click();
        const progress = card.getByRole("slider", { name: /播放进度$/ });
        await eventually(async () => Number(await progress.inputValue()), value => value > 100 && value < 2000);
        report[label + "_audio_time_ms"] = Number(await progress.inputValue());
    }
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    owner = await context();
    await open();
    if (config.scenario === "durable-media") {
        report.step = "original multi-file upload grid";
        await page.locator('input[type="file"][accept^="image/"]').setInputFiles(files.map(file => ({ name: file.name, mimeType: file.mimeType, buffer: file.buffer })));
        const uploaded = await graph(document => document.nodes.length === files.length && document.nodes.every(node => node.metadata?.storageKey?.startsWith("resource:") && node.metadata.assetId));
        for (const file of files) {
            report.step = "original grid playback " + file.kind;
            await preview(uploaded.nodes.find(node => node.type === file.kind), "original");
        }
    } else {
        report.step = "original image upload without a usable receipt";
        let acceptedResource;
        const uploadGate = new Promise(resolve => { releaseUpload = resolve; });
        await page.route(origin + resourcePath, async route => {
            if (route.request().method() !== "POST") return route.continue();
            if (config.scenario === "lost-upload-receipt") {
                const accepted = await route.fetch();
                acceptedResource = (await read(accepted)).resource;
                assert.ok(acceptedResource.id);
            }
            await uploadGate;
            return route.fulfill({ status: 503, json: { error: { code: "storage_unavailable", message: "测试注入：原素材上传没有可用回执" } } });
        });
        const file = files[0];
        await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: file.name, mimeType: file.mimeType, buffer: file.buffer });
        const placeholder = await graph(document => document.nodes.length === 1 && document.nodes[0].metadata?.fileUpload === "uploading");
        await eventually(local, value => !value.journal?.inFlight && value.journal?.confirmedRevision === placeholder.revision);
        report.placeholder_revision_confirmed_before_failure = placeholder.revision;
        releaseUpload();
        const pending = await eventually(local, value => value.images.length === 1 && !value.project.nodes[0].metadata.fileUpload);
        assert.equal(pending.images[0].sha256, sha(file.buffer));
        assert.equal(pending.images[0].size, file.buffer.length);
        assert.ok(pending.images[0].key.startsWith(`image:${config.userId}:`));
        assert.deepEqual(pending.other_drafts, []);
        assert.equal(report.uploads.length, 1);
        assert.equal(report.uploads[0].key, pending.images[0].key);
        await assertNoRemoteImage();
        await preview(pending.project.nodes[0], "pending");
        await page.screenshot({ path: resolve(output, "pending.png") });
        report.step = "reload preserves local original bytes without resubmission";
        await page.reload();
        await page.locator("[data-canvas-editor] [data-canvas-viewport]").waitFor();
        const reloaded = await eventually(local, value => value.images.length === 1);
        assert.deepEqual(reloaded.images, pending.images);
        assert.deepEqual(reloaded.other_drafts, []);
        assert.equal(report.uploads.length, 1);
        await assertNoRemoteImage();
        report.pending_image_original_bytes_after_reload = true;
        await page.unroute(origin + resourcePath);
        report.step = "explicit original save retries same image upload";
        const retried = page.waitForResponse(response => new URL(response.url()).pathname === resourcePath && response.request().method() === "POST");
        await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
        const resource = (await read(await retried)).resource;
        await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
        const recovered = await graph(document => document.nodes.length === 1 && document.nodes[0].metadata?.storageKey === "resource:" + resource.id && document.nodes[0].metadata.assetId);
        assert.equal(recovered.nodes[0].id, pending.images[0].node_id);
        assert.equal(report.uploads.length, 2);
        assert.equal(report.uploads[1].key, report.uploads[0].key);
        assert.equal(report.uploads[1].canvas_key, config.sourceKey);
        report.explicit_save_reused_upload_key = true;
        if (acceptedResource) {
            assert.equal(resource.id, acceptedResource.id);
            report.explicit_save_reused_ready_identity = true;
        }
        report.pending_image = pending.images[0];
    }
    report.step = "durable bytes and original display before fresh context";
    const saved = await graph(document => document.nodes.length === files.length && document.nodes.every(node => node.metadata?.storageKey?.startsWith("resource:") && node.metadata.assetId));
    report.nodes = saved.nodes.map(node => ({ id: node.id, kind: node.type, storage_key: node.metadata.storageKey, asset_id: node.metadata.assetId }));
    for (const file of files) {
        const node = saved.nodes.find(node => node.type === file.kind);
        const identity = node.metadata.storageKey.slice("resource:".length);
        const download = await owner.request.get(origin + resourcePath + "/" + identity + "/file?proxy=1");
        assert.ok(download.ok());
        assert.equal(sha(await download.body()), sha(file.buffer));
    }
    const before = report.uploads.length;
    const cookies = await owner.cookies();
    await owner.close();
    owner = await context(cookies);
    report.step = "cookies-only fresh context loads original permanent resources";
    await open();
    for (const node of saved.nodes) await preview(node, "fresh");
    assert.deepEqual((await graph()).nodes.map(node => ({ id: node.id, kind: node.type, storage_key: node.metadata.storageKey, asset_id: node.metadata.assetId })), report.nodes);
    assert.equal(report.uploads.length, before);
    report.cookies_only_recovery_without_reupload = true;
    assert.equal(report.task_posts, 0);
    assert.deepEqual(report.page_errors, []);
    assert.ok(report.http_errors.every(error => (config.scenario !== "durable-media" && error.path === resourcePath && error.status === 503) || (error.path === config.path && error.status === 422)), JSON.stringify(report.http_errors));
    await page.screenshot({ path: resolve(output, "fresh.png") });
    report.step = "complete";
    process.stdout.write(JSON.stringify(report));
} catch (error) {
    report.failure = error.stack;
    if (page) report.local_failure_state = await local().catch(() => null);
    if (page) await page.screenshot({ path: resolve(output, "failed.png") }).catch(() => undefined);
    process.stderr.write(JSON.stringify(report) + "\n");
    throw error;
} finally {
    releaseUpload?.();
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    await owner?.close();
    await browser?.close();
    await server.close();
}
