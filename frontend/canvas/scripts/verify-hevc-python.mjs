import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4198";
const resources = "/api/v1/canvas-runtime/resources";
const output = resolve(config.outputDirectory);
const digest = bytes => createHash("sha256").update(bytes).digest("hex");
const report = { scope: "original canvas trusted HEVC upload and playback", width: config.width, step: "start", page_errors: [], http_errors: [], task_posts: 0, uploads: [], playback_variants_requested: [], media_views: [] };
const observations = [];
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(output, "node_modules/.vite"), server: { host: "127.0.0.1", port: 4198, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let owner;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}`);
    return response.json();
}
async function authorize(context) {
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    context.setDefaultTimeout(20000);
    context.setDefaultNavigationTimeout(60000);
}
async function eventually(readValue, accepted, limit = 45000) {
    let value;
    const deadline = Date.now() + limit;
    while (Date.now() < deadline) {
        value = await readValue();
        if (accepted(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`原HEVC画布未达到预期：${JSON.stringify(value)}`);
}
async function graph(accepted) {
    return eventually(async () => (await read(await owner.request.get(origin + config.path + "/my-document"))).source_document, accepted);
}
async function open() {
    page = await owner.newPage();
    page.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    page.on("request", request => {
        const url = new URL(request.url());
        if (url.pathname === "/api/v1/canvas-runtime/tasks" && request.method() === "POST") report.task_posts++;
        if (url.pathname.startsWith(resources + "/") && url.searchParams.get("variant")) report.playback_variants_requested.push(url.searchParams.get("variant"));
    });
    page.on("response", response => {
        const pathname = new URL(response.url()).pathname;
        if (pathname.startsWith("/api/") && response.status() >= 400) report.http_errors.push({ method: response.request().method(), path: pathname, status: response.status() });
        if (pathname === resources && response.request().method() === "POST") {
            observations.push(response.json().then(value => report.uploads.push({ kind: value.resource?.kind, id: value.resource?.id, status: response.status() })));
        }
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
    await page.waitForFunction(async () => {
        const viewport = document.querySelector("[data-canvas-viewport]");
        if (!viewport) return false;
        let previous;
        let stable = 0;
        while (stable < 8) {
            await new Promise(requestAnimationFrame);
            const current = ["--canvas-live-x", "--canvas-live-y", "--canvas-live-scale"].map(key => viewport.style.getPropertyValue(key)).join(",");
            stable = current === previous ? stable + 1 : 0;
            previous = current;
        }
        return true;
    });
}
async function play(node, phase) {
    await fit();
    const card = page.locator(`[data-node-id="${node.id}"]`);
    await card.getByRole("button", { name: /^播放 / }).click();
    await eventually(() => card.locator("video").evaluate(video => ({ width: video.videoWidth, height: video.videoHeight, duration: video.duration, time: video.currentTime, paused: video.paused, ready: video.readyState, error: video.error?.code ?? null })), value => !value.paused && value.time > 0.1 && value.ready >= 2 && value.width === 1280 && value.height === 720 && value.error === null);
    const value = await card.locator("video").evaluate(video => ({ width: video.videoWidth, height: video.videoHeight, duration: video.duration, time: video.currentTime, paused: video.paused, error: video.error?.code ?? null }));
    assert.ok(value.duration >= 4.47 && value.duration <= 4.49);
    const response = await owner.request.get(`${origin}${resources}/${node.metadata.storageKey.slice(9)}/file`);
    assert.ok(response.ok());
    const sha256 = digest(await response.body());
    assert.equal(sha256, config.sha256);
    report.media_views.push({ phase, sha256, ...value });
    const bounds = await card.boundingBox();
    assert.ok(bounds && bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= config.width && bounds.y + bounds.height <= 900);
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    report.browser_version = browser.version();
    owner = await browser.newContext({ viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference" });
    await read(await owner.request.post(origin + "/api/v1/auth/login", { headers: { Origin: origin }, data: { username: config.username, password: config.password } }));
    await authorize(owner);
    await open();
    report.step = "原文件input上传HEVC";
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: "可信HEVC原视频.mp4", mimeType: "video/mp4", buffer: Buffer.from(config.media, "base64") });
    const created = await graph(document => document.nodes.length === 1 && document.nodes[0].metadata?.storageKey?.startsWith("resource:") && document.nodes[0].metadata?.assetId && document.nodes[0].metadata?.status === "success");
    let node = created.nodes[0];
    assert.equal(node.type, "video");
    assert.equal(node.metadata.naturalWidth, 1280);
    assert.equal(node.metadata.naturalHeight, 720);
    assert.equal(node.metadata.durationMs, 4480);
    assert.equal(node.metadata.hasAudio, true);
    assert.equal(created.connections.length, 0);
    report.resource_id = node.metadata.storageKey.slice(9);
    report.step = "原播放按钮与规范永久封面";
    await play(node, "initial");
    const hydrated = await graph(document => document.nodes[0].metadata?.videoPreview?.captureVersion === 2 && document.nodes[0].metadata.videoPreview.sourceKey === node.metadata.storageKey);
    node = hydrated.nodes[0];
    assert.ok(node.metadata.videoPreview.storageKey.startsWith("resource:"));
    const poster = await owner.request.get(`${origin}${resources}/${node.metadata.videoPreview.storageKey.slice(9)}/file`);
    assert.ok(poster.ok());
    report.poster_sha256 = digest(await poster.body());
    report.step = "关闭旧窗口，仅cookies恢复后原按钮播放";
    await Promise.all(observations);
    const uploadCount = report.uploads.length;
    const cookies = await owner.cookies();
    await owner.close();
    owner = await browser.newContext({ storageState: { cookies, origins: [] }, viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference" });
    await authorize(owner);
    await open();
    await page.locator(`[data-node-id="${node.id}"]`).waitFor();
    const fresh = await graph(document => document.nodes.length === 1);
    assert.equal(fresh.nodes[0].id, node.id);
    assert.equal(fresh.nodes[0].metadata.storageKey, node.metadata.storageKey);
    assert.equal(fresh.nodes[0].metadata.assetId, node.metadata.assetId);
    assert.deepEqual(fresh.nodes[0].metadata.videoPreview, node.metadata.videoPreview);
    await play(fresh.nodes[0], "fresh");
    await Promise.all(observations);
    assert.equal(report.uploads.length, uploadCount);
    report.video_uploads = report.uploads.filter(row => row.kind === "video").length;
    assert.equal(report.video_uploads, 1);
    assert.equal(report.task_posts, 0);
    report.node = fresh.nodes[0];
    report.cookies_only_restored_without_reupload = true;
    await page.mouse.move(5, 500);
    await page.screenshot({ path: resolve(output, "fresh-playing.png") });
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    report.step = "complete";
} catch (error) {
    report.failure = error.stack ?? error.message;
    if (page) {
        report.body = await page.locator("body").innerText().catch(() => "");
        await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    }
    process.exitCode = 1;
} finally {
    await Promise.all(observations);
    await browser?.close();
    await server.close();
    report.browser_closed = report.server_closed = true;
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    process.stdout.write(JSON.stringify(report) + "\n");
}
