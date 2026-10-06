import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4193";
const output = resolve(".runtime/provider-query-python-browser");
const report = { source_button_reachable: false, source_button_query_count: 0, source_bind_applied: false, persisted_after_fresh_browser: false, author_video_played: false, fresh_video_played: false, member_video_played: false, private_task_hidden_from_member: false, task_post_count: 0, playback: [], page_errors: [], failed_routes: [] };
await mkdir(output, { recursive: true });
const server = await createServer({ server: { host: "127.0.0.1", port: 4193, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
const taskPath = `/api/v1/canvas-runtime/tasks/${config.taskId}`;
const canvasPath = `${origin}/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const canvasUrl = `${origin}/canvas-app/canvas/${config.sourceKey}`;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
function observe(context) {
    context.on("page", current => current.on("pageerror", error => report.page_errors.push(error.stack || error.message)));
    context.on("request", request => {
        const path = new URL(request.url()).pathname;
        if (request.method() !== "POST") return;
        if (path === "/api/v1/canvas-runtime/tasks") report.task_post_count++;
        if (path === `${taskPath}/query-provider`) report.source_button_query_count++;
    });
    context.on("response", response => {
        const path = new URL(response.url()).pathname;
        if (response.status() >= 400 && path.startsWith("/api/")) report.failed_routes.push({ path, status: response.status() });
    });
}
async function login(context, username, actorId) {
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value, "X-Canvas-Actor": actorId });
}
async function playSourceVideo(current, label) {
    const node = current.locator(`[data-node-id="${config.nodeId}"]`);
    await node.waitFor({ state: "visible" });
    await node.getByRole("button", { name: "播放 原视频取回验证", exact: true }).click();
    await current.waitForFunction(id => {
        const video = document.querySelector(`[data-node-id="${id}"] video`);
        return video && video.videoWidth === 160 && video.videoHeight === 90 && video.currentTime > 0.15 && !video.error;
    }, config.nodeId);
    const result = await node.locator("video").evaluate((video, label) => ({ label, width: video.videoWidth, height: video.videoHeight, duration: video.duration, time: video.currentTime, readyState: video.readyState, frames: video.getVideoPlaybackQuality().totalVideoFrames }), label);
    assert.equal(result.width, 160);
    assert.equal(result.height, 90);
    assert.ok(result.duration >= 0.9 && result.duration <= 1.5);
    assert.ok(result.time > 0.15 && result.frames > 0);
    report.playback.push(result);
}
function contextOptions(cookies) {
    return { viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference", ...(cookies ? { storageState: { cookies, origins: [] } } : {}) };
}
function configure(context) {
    context.setDefaultTimeout(30000);
    context.setDefaultNavigationTimeout(60000);
    observe(context);
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const context = await browser.newContext(contextOptions());
    configure(context);
    await login(context, config.author, config.actorId);
    page = await context.newPage();
    await page.goto(canvasUrl);
    const node = page.locator(`[data-node-id="${config.nodeId}"]`);
    await node.waitFor({ state: "visible" });
    await node.getByRole("button", { name: /查看详情|查看原任务/ }).click();
    const details = page.getByRole("dialog", { name: "任务详情", exact: true });
    const retrieve = details.getByRole("button", { name: "取回结果", exact: true });
    await retrieve.waitFor({ state: "visible" });
    assert.ok(await retrieve.isEnabled());
    report.source_button_reachable = true;
    const boundResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && response.request().method() === "POST" && response.ok());
    const [queried] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === `${taskPath}/query-provider` && response.request().method() === "POST"),
        retrieve.click(),
    ]);
    const recovered = await read(queried);
    assert.equal(recovered.recovered, true);
    assert.equal(recovered.task.id, config.taskId);
    assert.equal(recovered.task.status, "succeeded");
    assert.equal(recovered.task.resultState, "READY");
    const bound = await read(await boundResponse);
    assert.equal(bound.result.taskId, config.taskId);
    assert.equal(bound.result.node.metadata.storageKey, JSON.parse(recovered.task.resultJson).video.storageKey);
    report.source_bind_applied = true;
    await details.getByRole("button", { name: "Close", exact: true }).click();
    await playSourceVideo(page, "author");
    report.author_video_played = true;
    const saved = await read(await context.request.get(`${canvasPath}/my-document`));
    const persisted = saved.source_document.nodes.find(node => node.id === config.nodeId);
    assert.equal(persisted.metadata.taskId, config.taskId);
    assert.ok(persisted.metadata.storageKey.startsWith("resource:"));
    assert.ok(!persisted.metadata.content.startsWith("blob:"));
    const fresh = await browser.newContext(contextOptions(await context.cookies()));
    configure(fresh);
    page = await fresh.newPage();
    await page.goto(canvasUrl);
    await playSourceVideo(page, "fresh-author");
    report.fresh_video_played = true;
    const refreshed = await read(await fresh.request.get(`${canvasPath}/my-document`));
    assert.equal(refreshed.source_document.nodes.find(node => node.id === config.nodeId).metadata.storageKey, persisted.metadata.storageKey);
    report.persisted_after_fresh_browser = true;
    const shared = await browser.newContext(contextOptions());
    configure(shared);
    await login(shared, config.member, config.memberId);
    const sharedDocument = await read(await shared.request.get(`${canvasPath}/my-document`));
    const sharedNode = sharedDocument.source_document.nodes.find(node => node.id === config.nodeId);
    assert.equal(sharedNode.metadata.storageKey, persisted.metadata.storageKey);
    assert.ok(!Object.hasOwn(sharedNode.metadata, "taskId"));
    const hidden = await shared.request.get(`${origin}${taskPath}`);
    assert.equal(hidden.status(), 404);
    report.private_task_hidden_from_member = true;
    page = await shared.newPage();
    await page.goto(canvasUrl);
    await playSourceVideo(page, "member");
    report.member_video_played = true;
    assert.equal(report.source_button_query_count, 1);
    assert.equal(report.task_post_count, 0);
    assert.deepEqual(report.page_errors, []);
    // M3 assistant endpoints are still absent; keep their actual 404s in the
    // report and reject every failure outside those two known endpoints.
    assert.deepEqual(report.failed_routes.filter(item => item.status !== 404 || !["/api/v1/canvas-runtime/assistant/status", "/api/v1/canvas-runtime/assistant/ui-session"].includes(item.path)), []);
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
