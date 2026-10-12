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
const report = { step: "start", kind: config.kind, scenario: config.scenario, width: config.width, failure_timing: config.immediateFailure ? "immediate" : "confirmed-placeholder", page_errors: [], http_errors: [], uploads: [], task_posts: 0 };
const file = { ...config.media, buffer: Buffer.from(config.media.content, "base64") };
const sha = value => createHash("sha256").update(value).digest("hex");
const prefix = `${config.kind}:${config.userId}:`;
const server = await createServer({ cacheDir: resolve(output, "node_modules/.vite"), server: { host: "127.0.0.1", port: 4196, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let owner;
let page;
let releaseUpload;
let injectFailure = true;

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
    assert.fail(`音视频恢复未达到预期：${report.step} ${JSON.stringify(value)}`);
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

async function assertNoRemoteMedia() {
    for (const node of (await graph()).nodes) {
        assert.equal(node.type, config.kind);
        assert.ok(!node.metadata?.storageKey && !node.metadata?.content && !node.metadata?.assetId);
    }
}

async function local() {
    return page.evaluate(async ({ key, otherScope, kind }) => {
        const { openLocalCanvasProject, pendingExternalCanvasRevision } = await import("/canvas-app/src/services/local-workspace-repository.ts");
        const { peekCanvasOperationJournal } = await import("/canvas-app/src/services/canvas-operation-journal.ts");
        const { getMediaBlob } = await import("/canvas-app/src/services/file-storage.ts");
        const { readCanvasSyncDrafts } = await import("/canvas-app/src/services/canvas-sync-drafts.ts");
        const { useAssetStore } = await import("/canvas-app/src/stores/use-asset-store.ts");
        const project = openLocalCanvasProject(key);
        const media = [];
        for (const node of project?.nodes || []) {
            if (node.type !== kind || !node.metadata?.storageKey?.startsWith(kind + ":")) continue;
            const blob = await getMediaBlob(node.metadata.storageKey);
            if (!blob) continue;
            const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
            media.push({ node_id: node.id, key: node.metadata.storageKey, size: blob.size, mime: blob.type, name: blob instanceof File ? blob.name : null, sha256: [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("") });
        }
        return { project, media, assets: useAssetStore.getState().assets.filter(asset => asset.kind === kind), journal: peekCanvasOperationJournal(key), external: pendingExternalCanvasRevision(key), other_drafts: await readCanvasSyncDrafts(key, otherScope) };
    }, { key: config.sourceKey, otherScope: config.otherUserId, kind: config.kind });
}

async function open() {
    page = await owner.newPage();
    page.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    page.on("request", request => {
        const path = new URL(request.url()).pathname;
        if (request.method() === "POST" && path === resourcePath) report.uploads.push({ key: request.headers()["x-idempotency-key"], canvas_key: request.headers()["x-canvas-key"] });
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

async function preview(node, label) {
    const focus = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focus.isVisible()) await focus.getByRole("button", { name: "退出专注模式", exact: true }).press("Control+0");
    else await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    const card = page.locator(`[data-node-id="${node.id}"]`);
    await card.waitFor({ state: "visible" });
    if (config.kind === "video") {
        await card.getByRole("button", { name: /^播放 / }).click();
        await eventually(() => card.locator("video").evaluate(element => !element.paused && element.currentTime > 0.1 && element.readyState >= 2 && element.videoWidth === 160 && element.videoHeight === 90), Boolean);
        report[label + "_video"] = await card.locator("video").evaluate(element => ({ duration: element.duration, width: element.videoWidth, height: element.videoHeight, time: element.currentTime }));
    } else {
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
    report.step = "original media upload without a usable receipt";
    let acceptedResource;
    const declarations = [];
    const uploadGate = new Promise(resolve => { releaseUpload = resolve; });
    await page.route(origin + resourcePath, async route => {
        const request = route.request();
        if (request.method() !== "POST" || !request.headers()["x-idempotency-key"]?.startsWith(prefix)) return route.continue();
        const form = await new Response(request.postDataBuffer(), { headers: { "content-type": request.headers()["content-type"] } }).formData();
        const original = form.get("file");
        declarations.push({ kind: form.get("kind"), name: original.name, sha256: sha(Buffer.from(await original.arrayBuffer())), width: form.get("width"), height: form.get("height"), durationMs: form.get("durationMs") });
        if (!injectFailure) return route.continue();
        if (config.scenario === "lost-upload-receipt") {
            acceptedResource = (await read(await route.fetch())).resource;
            assert.ok(acceptedResource.id);
        }
        if (!config.immediateFailure) await uploadGate;
        return route.fulfill({ status: 503, json: { error: { code: "storage_unavailable", message: "测试注入：原音视频上传没有可用回执" } } });
    });
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: file.name, mimeType: file.mimeType, buffer: file.buffer });
    if (!config.immediateFailure) {
        const placeholder = await graph(document => document.nodes.length === 1 && document.nodes[0].metadata?.fileUpload === "uploading");
        await eventually(local, value => !value.journal?.inFlight && value.journal?.confirmedRevision === placeholder.revision);
        report.placeholder_revision_confirmed_before_failure = placeholder.revision;
    }
    releaseUpload();
    const pending = await eventually(local, value => value.media.length === 1 && !value.project.nodes[0].metadata.fileUpload);
    report.confirmed_revision_after_failure = pending.journal?.confirmedRevision;
    report.in_flight_after_failure = Boolean(pending.journal?.inFlight);
    assert.equal(pending.media[0].sha256, sha(file.buffer));
    assert.equal(pending.media[0].size, file.buffer.length);
    assert.equal(pending.media[0].name, file.name);
    assert.ok(pending.media[0].key.startsWith(prefix));
    assert.deepEqual(pending.other_drafts, []);
    const mediaUploads = () => report.uploads.filter(upload => upload.key?.startsWith(prefix));
    assert.equal(mediaUploads().length, 1);
    assert.equal(mediaUploads()[0].key, pending.media[0].key);
    const pendingNode = pending.project.nodes[0];
    assert.equal(pendingNode.metadata.durationMs, 2000);
    if (config.kind === "video") {
        assert.equal(pendingNode.metadata.hasAudio, true);
        assert.equal(pendingNode.metadata.naturalWidth, 160);
        assert.equal(pendingNode.metadata.naturalHeight, 90);
        assert.ok(pendingNode.metadata.videoPreview?.storageKey.startsWith("resource:"));
        report.pending_preview = pendingNode.metadata.videoPreview;
    }
    await assertNoRemoteMedia();
    await preview(pendingNode, "pending");
    await page.screenshot({ path: resolve(output, "pending.png") });
    report.step = "reload preserves local original bytes without resubmission";
    await page.reload();
    await page.locator("[data-canvas-editor] [data-canvas-viewport]").waitFor();
    const reloaded = await eventually(local, value => value.media.length === 1);
    assert.deepEqual(reloaded.media, pending.media);
    assert.deepEqual(reloaded.other_drafts, []);
    assert.equal(mediaUploads().length, 1);
    await assertNoRemoteMedia();
    report.pending_media_original_bytes_after_reload = true;
    if (config.kind === "video") report.reloaded_preview = reloaded.project.nodes[0].metadata.videoPreview;
    injectFailure = false;
    report.step = "explicit original save retries same media upload";
    const retried = page.waitForResponse(response => new URL(response.url()).pathname === resourcePath && response.request().method() === "POST" && response.request().headers()["x-idempotency-key"] === pending.media[0].key);
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    const resource = (await read(await retried)).resource;
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
    const saved = await graph(document => document.nodes.length === 1 && document.nodes[0].metadata?.storageKey === "resource:" + resource.id && document.nodes[0].metadata.assetId);
    let recoveredNode = saved.nodes[0];
    assert.equal(recoveredNode.id, pending.media[0].node_id);
    assert.equal(recoveredNode.metadata.durationMs, pendingNode.metadata.durationMs);
    if (config.kind === "video") {
        assert.equal(recoveredNode.metadata.hasAudio, true);
        assert.ok(recoveredNode.metadata.videoPreview?.storageKey.startsWith("resource:"));
        report.step = "original canonical video preview hydration";
        const hydrated = await graph(document => document.nodes[0].metadata?.videoPreview?.captureVersion === 2 && document.nodes[0].metadata.videoPreview.sourceKey === recoveredNode.metadata.storageKey);
        recoveredNode = hydrated.nodes[0];
        await eventually(local, value => !value.journal?.inFlight && value.journal?.confirmedRevision === hydrated.revision);
        assert.equal(recoveredNode.metadata.videoPreview.width, 160);
        assert.equal(recoveredNode.metadata.videoPreview.height, 90);
        report.recovered_preview = recoveredNode.metadata.videoPreview;
    }
    assert.equal(mediaUploads().length, 2);
    assert.equal(mediaUploads()[1].key, mediaUploads()[0].key);
    assert.equal(mediaUploads()[1].canvas_key, config.sourceKey);
    assert.equal(declarations.length, 2);
    assert.deepEqual(declarations[1], declarations[0]);
    assert.equal(declarations[0].sha256, sha(file.buffer));
    assert.equal(declarations[0].name, file.name);
    report.upload_declarations = declarations;
    report.explicit_save_reused_upload_key = true;
    if (acceptedResource) {
        assert.equal(resource.id, acceptedResource.id);
        report.explicit_save_reused_ready_identity = true;
    }
    report.pending_media = pending.media[0];
    report.step = "durable original media before fresh context";
    const identity = recoveredNode.metadata.storageKey.slice("resource:".length);
    const download = await owner.request.get(origin + resourcePath + "/" + identity + "/file?proxy=1");
    assert.ok(download.ok());
    assert.equal(sha(await download.body()), sha(file.buffer));
    const canonicalAssets = (await local()).assets;
    assert.ok(canonicalAssets.some(asset => asset.id === recoveredNode.metadata.assetId && asset.data.storageKey === recoveredNode.metadata.storageKey));
    for (const asset of pending.assets.filter(asset => asset.data.storageKey === pending.media[0].key)) assert.ok(canonicalAssets.some(item => item.id === asset.id && item.data.storageKey === recoveredNode.metadata.storageKey));
    await preview(recoveredNode, "recovered");
    const before = report.uploads.length;
    const cookies = await owner.cookies();
    await owner.close();
    owner = await context(cookies);
    report.step = "cookies-only fresh context loads original permanent media";
    await open();
    await preview(recoveredNode, "fresh");
    const fresh = (await graph()).nodes[0];
    assert.equal(fresh.id, recoveredNode.id);
    assert.equal(fresh.metadata.storageKey, recoveredNode.metadata.storageKey);
    assert.equal(fresh.metadata.assetId, recoveredNode.metadata.assetId);
    if (config.kind === "video") assert.deepEqual(fresh.metadata.videoPreview, recoveredNode.metadata.videoPreview);
    assert.equal(report.uploads.length, before);
    report.resource = { id: identity, node_id: recoveredNode.id, asset_id: recoveredNode.metadata.assetId };
    report.cookies_only_recovery_without_reupload = true;
    assert.equal(report.task_posts, 0);
    assert.deepEqual(report.page_errors, []);
    assert.ok(report.http_errors.every(error => error.path === resourcePath && error.status === 503 || error.path === config.path && error.status === 422), JSON.stringify(report.http_errors));
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
