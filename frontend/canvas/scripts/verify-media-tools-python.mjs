import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = { step: "start", kind: config.kind, width: config.width, page_errors: [], http_errors: [], resource_posts: 0, task_posts: 0 };
const server = await createServer({ cacheDir: resolve(".runtime/vite-media-tools"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
let owner;
const source = () => page.locator('[data-node-id="media-video"]');

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function eventually(readValue, predicate, timeout = 60000) {
    const deadline = Date.now() + timeout;
    let value;
    while (Date.now() < deadline) {
        value = await readValue();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`媒体工具状态未达到预期：${JSON.stringify(value)}`);
}
async function context(username, cookies) {
    const value = await browser.newContext({ viewport: { width: config.width, height: 900 }, ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    value.setDefaultTimeout(20000);
    value.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await value.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await value.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await value.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return value;
}
async function graph(context = owner, predicate = () => true) {
    return eventually(async () => (await read(await context.request.get(`${origin}${path}/my-document`))).source_document, predicate);
}
async function fit() {
    const focus = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focus.isVisible()) await focus.getByRole("button", { name: "退出专注模式", exact: true }).press("Control+0");
    else await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await page.waitForFunction(async () => {
        const viewport = document.querySelector("[data-canvas-viewport]");
        let previous;
        let stable = 0;
        while (stable < 8) {
            await new Promise(requestAnimationFrame);
            const next = ["--canvas-live-x", "--canvas-live-y", "--canvas-live-scale"].map(key => viewport.style.getPropertyValue(key)).join(",");
            stable = next === previous ? stable + 1 : 0;
            previous = next;
        }
        return true;
    });
}
async function open(context) {
    page = await context.newPage();
    page.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    page.on("request", request => {
        const route = new URL(request.url()).pathname;
        if (request.method() === "POST" && route === "/api/v1/canvas-runtime/resources") report.resource_posts++;
        if (request.method() === "POST" && route === "/api/v1/canvas-runtime/tasks") report.task_posts++;
    });
    page.on("response", response => {
        if (response.status() >= 400) report.http_errors.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    await source().waitFor({ state: "visible" });
    await fit();
}
async function selectSource() {
    await fit();
    await source().click({ position: { x: 12, y: 36 } });
    await source().hover();
    await page.getByRole("toolbar", { name: "节点快捷工具", exact: true }).waitFor({ state: "visible" });
}
async function processMenu(name) {
    await selectSource();
    await page.getByRole("button", { name: "视频处理", exact: true }).click();
    await page.getByRole("menuitem", { name, exact: true }).click();
}
function outputs(document) {
    return document.nodes.filter(node => node.id !== "media-video");
}
async function previewsReady(ids) {
    return graph(owner, document => ids.every(id => {
        const metadata = document.nodes.find(node => node.id === id)?.metadata;
        return metadata?.videoPreview?.storageKey?.startsWith("resource:") && metadata.videoPreview.sourceKey === metadata.storageKey && metadata.videoPreview.captureVersion === 2;
    }));
}
async function saved() {
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}

try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined });
    owner = await context(config.username);
    await open(owner);
    await previewsReady(["media-video"]);
    await selectSource();
    report.step = "operate";
    await writeFile(resolve(output, "original-tools.yml"), await page.locator("body").ariaSnapshot());
    if (config.kind === "frames") {
        for (const [index, name] of ["截取首帧", "截取尾帧"].entries()) {
            await selectSource();
            await page.getByRole("button", { name: "关键帧截取", exact: true }).click();
            await page.getByRole("menuitem", { name, exact: true }).click();
            await graph(owner, document => outputs(document).length === index + 1 && outputs(document).every(node => node.metadata?.storageKey?.startsWith("resource:")));
        }
    } else if (config.kind === "trim") {
        await processMenu("视频剪辑");
        const trim = page.getByRole("dialog", { name: "视频片段剪辑", exact: true });
        await trim.waitFor({ state: "visible" });
        await trim.getByRole("button", { name: "取消剪辑", exact: true }).click();
        assert.equal(outputs(await graph()).length, 0);
        await processMenu("视频剪辑");
        const track = await trim.locator("[data-video-trim-filmstrip]").boundingBox();
        const end = await trim.getByRole("button", { name: "调整片段终点", exact: true }).boundingBox();
        assert.ok(track && end);
        await page.mouse.move(end.x + end.width / 4, end.y + end.height / 2);
        await page.mouse.down();
        await page.mouse.move(track.x + track.width / 2, end.y + end.height / 2, { steps: 8 });
        await page.mouse.up();
        await eventually(() => trim.getByLabel("所选片段时长", { exact: true }).textContent(), value => /^1\.0\d\s*s$/.test(value));
        await page.screenshot({ path: resolve(output, "trim-selected.png"), fullPage: true });
        await trim.getByRole("button", { name: "确认剪辑", exact: true }).click();
    } else if (config.kind === "crop") {
        await processMenu("画面裁切");
        await page.locator("[data-video-crop-inline]").waitFor({ state: "visible" });
        await page.getByRole("button", { name: "取消裁切", exact: true }).click();
        assert.equal(outputs(await graph()).length, 0);
        await processMenu("画面裁切");
        await page.getByLabel("视频裁切选区", { exact: true }).getByText("256 × 144", { exact: true }).first().waitFor({ state: "visible" });
        await page.screenshot({ path: resolve(output, "crop-selected.png"), fullPage: true });
        await page.getByRole("button", { name: "确认裁切", exact: true }).click();
    } else {
        assert.equal(config.kind, "audio");
        await page.getByRole("button", { name: "音视频分离", exact: true }).click();
    }
    const produced = await graph(owner, document => outputs(document).length === (config.kind === "frames" || config.kind === "audio" ? 2 : 1) && outputs(document).every(node => node.metadata?.storageKey?.startsWith("resource:") && node.metadata.status === "success"));
    await fit();
    const withPreviews = await previewsReady(produced.nodes.filter(node => node.type === "video").map(node => node.id));
    await saved();
    const ready = await graph(owner, document => outputs(document).every(node => typeof node.metadata.assetId === "string" && node.metadata.assetId.length > 0));
    const derived = outputs(ready);
    report.outputs = [];
    for (const node of derived) {
        assert.ok(produced.connections.some(edge => edge.fromNodeId === "media-video" && edge.toNodeId === node.id));
        const resourceId = node.metadata.storageKey.slice("resource:".length);
        const response = await owner.request.get(`${origin}/api/v1/canvas-runtime/resources/${resourceId}/file`);
        assert.ok(response.ok());
        const bytes = await response.body();
        const filename = `${node.id}.${node.type === "image" ? "png" : node.type === "video" ? "mp4" : "m4a"}`;
        await writeFile(resolve(output, filename), bytes);
        report.outputs.push({ id: node.id, kind: node.type, resource_id: resourceId, checksum: createHash("sha256").update(bytes).digest("hex"), filename });
    }
    report.video_previews = withPreviews.nodes.filter(node => node.type === "video").map(node => ({ id: node.id, ...node.metadata.videoPreview }));
    const uploads = report.resource_posts;
    await page.screenshot({ path: resolve(output, "media-produced.png"), fullPage: true });
    const cookies = await owner.cookies();
    await owner.close();
    owner = await context(undefined, cookies);
    report.step = "fresh";
    await open(owner);
    const restored = await graph();
    for (const node of derived) {
        assert.deepEqual(restored.nodes.find(item => item.id === node.id).metadata, node.metadata);
        const card = page.locator(`[data-node-id="${node.id}"]`);
        await card.waitFor({ state: "visible" });
        if (node.type === "video") {
            await card.getByRole("button", { name: /^播放 / }).first().click();
            await eventually(() => card.locator("video").first().evaluate(element => element.videoWidth > 0 && element.readyState >= 2), Boolean);
        } else if (node.type === "image") {
            await eventually(() => card.getByRole("img").first().evaluate(element => element.complete && element.naturalWidth === 320 && element.naturalHeight === 180), Boolean);
        }
    }
    for (const preview of report.video_previews) assert.equal(restored.nodes.find(node => node.id === preview.id).metadata.videoPreview.storageKey, preview.storageKey);
    report.cookie_only_recovery = true;
    assert.equal(report.resource_posts, uploads, "Fresh context must preserve permanent output and preview identities");
    assert.equal(report.task_posts, 0);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    await page.screenshot({ path: resolve(output, "media-restored.png"), fullPage: true });
    report.step = "complete";
    process.stdout.write(JSON.stringify(report));
} catch (error) {
    report.error = error.stack ?? String(error);
    if (page && !page.isClosed()) {
        await writeFile(resolve(output, "failure.yml"), await page.locator("body").ariaSnapshot()).catch(() => {});
        await page.screenshot({ path: resolve(output, "failure.png"), fullPage: true }).catch(() => {});
    }
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
