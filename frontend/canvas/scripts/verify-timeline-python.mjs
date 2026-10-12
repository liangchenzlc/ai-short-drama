import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer, preview } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = { step: "start", width: config.width, serve_mode: config.serveMode, page_errors: [], http_errors: [], render_posts: 0, resource_posts: 0, compiled_plans: 0 };
const connection = { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } };
const server = config.serveMode === "preview"
    ? await preview({ root: resolve(".."), configFile: resolve("../vite.config.ts"), preview: connection, logLevel: "silent" })
    : await createServer({ cacheDir: resolve(".runtime/vite-timeline"), server: connection, logLevel: "silent" });
if (config.serveMode !== "preview") await server.listen();
let browser;
let page;
let owner;
const source = () => page.locator('[data-node-id="timeline-video"]');
const dialog = () => page.getByRole("dialog").filter({ has: page.getByText("多轨时间线", { exact: true }) });

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function eventually(readValue, predicate, timeout = 50000) {
    const deadline = Date.now() + timeout;
    let value;
    while (Date.now() < deadline) {
        value = await readValue();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`时间线状态未达到预期：${JSON.stringify(value)}`);
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
}
async function open(context) {
    page = await context.newPage();
    page.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    page.on("request", request => {
        const route = new URL(request.url()).pathname;
        if (request.method() === "POST" && route.endsWith("/timeline/renders")) report.render_posts++;
        if (request.method() === "POST" && route.endsWith("/timeline/render-plan")) report.compiled_plans++;
        if (request.method() === "POST" && route === "/api/v1/canvas-runtime/resources") report.resource_posts++;
    });
    page.on("response", response => {
        if (response.status() >= 400) report.http_errors.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    await source().waitFor({ state: "visible" });
    if (report.step === "fresh") {
        const focus = page.getByRole("button", { name: "退出专注模式", exact: true });
        if (await focus.isVisible()) await focus.click();
        await page.getByRole("button", { name: "自动整理节点", exact: true }).click();
    }
    await fit();
    await source().click({ position: { x: 12, y: 36 } });
    await source().hover();
    await writeFile(resolve(output, `canvas-${report.step}.yml`), await page.locator("body").ariaSnapshot());
}
async function timeline() {
    await source().hover();
    await page.getByRole("button", { name: /^(?:时间线编辑|进入剪辑)$/ }).first().click();
    await dialog().waitFor({ state: "visible" });
    await writeFile(resolve(output, `timeline-${report.step}.yml`), await dialog().ariaSnapshot());
}
async function saved() {
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}

try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined });
    owner = await context(config.username);
    report.step = "timeline";
    await open(owner);
    await timeline();
    await dialog().getByText("原界面中文字幕", { exact: true }).first().waitFor({ state: "visible" });
    await dialog().getByRole("button", { name: /^保\s*存$/ }).click();
    await dialog().waitFor({ state: "hidden" });
    await graph(owner, document => document.timeline?.clips?.length >= 2 && document.timeline.durationMs === 1000);
    report.timeline_saved = true;
    await timeline();
    report.step = "download";
    const downloadPromise = page.waitForEvent("download", { timeout: 120000 });
    await dialog().getByRole("button", { name: "导出成片", exact: true }).click();
    const download = await downloadPromise;
    assert.equal(download.suggestedFilename(), "时间线来源.mp4");
    await download.saveAs(resolve(output, "timeline-download.mp4"));
    assert.equal(await download.failure(), null);
    await dialog().getByRole("button", { name: "取消导出", exact: true }).waitFor({ state: "hidden" });
    report.actual_wasm_download = true;
    assert.ok(report.compiled_plans >= 1);
    await page.screenshot({ path: resolve(output, "timeline-downloaded.png"), fullPage: true });
    report.step = "assemble";
    await dialog().getByRole("button", { name: "生成新片段", exact: true }).click();
    const assembledGraph = await graph(owner, document => document.nodes.some(node => node.id !== "timeline-video" && node.type === "video" && node.metadata?.storageKey?.startsWith("resource:")));
    const assembled = assembledGraph.nodes.find(node => node.id !== "timeline-video" && node.type === "video");
    report.assembled_node_id = assembled.id;
    const resourceId = assembled.metadata.storageKey.slice("resource:".length);
    const bytes = await owner.request.get(`${origin}/api/v1/canvas-runtime/resources/${resourceId}/file`);
    assert.ok(bytes.ok());
    report.assembled_checksum = createHash("sha256").update(await bytes.body()).digest("hex");
    await dialog().getByRole("button", { name: "取消导出", exact: true }).waitFor({ state: "hidden", timeout: 60000 });
    await dialog().getByRole("button", { name: /^取\s*消$/ }).click();
    await saved();
    const withPreviews = await graph(owner, document => document.nodes.every(node => {
        const metadata = node.metadata;
        return metadata?.videoPreview?.storageKey?.startsWith("resource:")
            && metadata.videoPreview.sourceKey === metadata.storageKey
            && metadata.videoPreview.captureVersion === 2;
    }));
    report.video_previews = withPreviews.nodes.map(node => ({ id: node.id, ...node.metadata.videoPreview }));
    const posts = report.resource_posts;
    const cookies = await owner.cookies();
    await owner.close();
    owner = await context(undefined, cookies);
    report.step = "fresh";
    await open(owner);
    assert.deepEqual((await graph()).timeline, assembledGraph.timeline);
    const restoredPreviews = await graph();
    for (const preview of report.video_previews) {
        assert.equal(restoredPreviews.nodes.find(node => node.id === preview.id).metadata.videoPreview.storageKey, preview.storageKey);
    }
    await timeline();
    await dialog().getByText("原界面中文字幕", { exact: true }).first().waitFor({ state: "visible" });
    report.timeline_restored = true;
    await dialog().getByRole("button", { name: /^取\s*消$/ }).click();
    const restored = page.locator(`[data-node-id="${assembled.id}"]`);
    await restored.getByRole("button", { name: /^播放 / }).first().click();
    const media = restored.locator("video").first();
    await eventually(() => media.evaluate(element => element.readyState >= 1 && element.videoWidth > 0), Boolean);
    const measured = await media.evaluate(element => ({ width: element.videoWidth, height: element.videoHeight, duration: element.duration }));
    assert.equal(measured.width, 1920);
    assert.equal(measured.height, 1080);
    assert.ok(measured.duration >= 1 && measured.duration < 1.1);
    report.cookie_only_recovery = true;
    await page.screenshot({ path: resolve(output, "timeline-assembled-restored.png"), fullPage: true });
    const member = await context(config.memberUsername);
    const memberGraph = await graph(member);
    assert.ok(memberGraph.nodes.some(node => node.id === assembled.id && node.metadata.storageKey === assembled.metadata.storageKey));
    const shared = await member.request.get(`${origin}/api/v1/canvas-runtime/resources/${resourceId}/file`);
    assert.equal(createHash("sha256").update(await shared.body()).digest("hex"), report.assembled_checksum);
    report.assembled_shared = true;
    assert.equal(report.resource_posts, posts, "Fresh context must not upload the existing output again");
    assert.equal(report.render_posts, 0);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
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
    if (config.serveMode === "preview") await new Promise((resolve, reject) => server.httpServer.close(error => error ? reject(error) : resolve()));
    else await server.close();
}
