import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4194";
const output = resolve(".runtime/video-protocols-python-browser", config.mode);
const report = { source_saved_before_admission: false, original_https_dialog: false, same_operation_after_replacement: false, same_task_after_refresh: false, source_bind_applied: false, source_play_clicked: false, real_video_decoded: false, fresh_browser_preserved: false, unknown_retry_unavailable: false, task_posts: [], admission_errors: [], page_errors: [], failed_routes: [] };
await mkdir(output, { recursive: true });
const server = await createServer({ server: { host: "127.0.0.1", port: 4194, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
async function read(response) {
    if (!response.ok() && new URL(response.url()).pathname === "/api/v1/canvas-runtime/tasks") await admissionError(response);
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}`);
    return response.json();
}
async function admissionError(response) {
    const data = await response.json();
    report.admission_errors.push({ code: data.error?.code, message: data.error?.message, fields: data.error?.fields });
    return data;
}
function observe(context) {
    context.on("page", current => current.on("pageerror", error => report.page_errors.push(error.message)));
    context.on("request", request => {
        if (new URL(request.url()).pathname !== "/api/v1/canvas-runtime/tasks" || request.method() !== "POST") return;
        const body = request.postDataJSON();
        report.task_posts.push({ operation_key: body.input.metadata.clientOperationId, source_node: body.input.metadata.sourceNodeId, target_node: body.input.metadata.nodeId, operation: body.operation, config: body.input.config, capability_options: body.input.capabilityOptions, saved_references: (body.input.referenceImages || []).map(item => ({ storage_key: item.storageKey || "", url: item.url || "" })) });
    });
    context.on("response", response => {
        if (response.status() >= 400 && new URL(response.url()).pathname.startsWith("/api/")) report.failed_routes.push({ path: new URL(response.url()).pathname, status: response.status() });
    });
}
async function currentTask(context, taskId, predicate) {
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
        const task = await read(await context.request.get(`${origin}/api/v1/canvas-runtime/tasks/${taskId}`));
        if (predicate(task)) return task;
        await new Promise(resolve => setTimeout(resolve, 200));
    }
    assert.fail("The same admitted video task did not reach the expected state");
}
function admissionResponse(response) {
    return new URL(response.url()).pathname === "/api/v1/canvas-runtime/tasks" && response.request().method() === "POST";
}
async function inspectVideo(page, node) {
    const closeAssistant = page.getByRole("button", { name: "关闭助手", exact: true });
    if (await closeAssistant.isVisible()) await closeAssistant.click();
    await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await node.getByRole("button", { name: /^播放 / }).first().click();
    report.source_play_clicked = true;
    const video = node.locator("video").first();
    await video.waitFor({ state: "visible" });
    await video.evaluate(async media => {
        if (media.readyState >= 1) return;
        await new Promise((resolve, reject) => {
            media.addEventListener("loadedmetadata", resolve, { once: true });
            media.addEventListener("error", () => reject(new Error("The actual archived video could not be decoded")), { once: true });
        });
    });
    const measured = await video.evaluate(media => ({ width: media.videoWidth, height: media.videoHeight, duration: media.duration }));
    assert.equal(measured.width, 160);
    assert.equal(measured.height, 90);
    assert.ok(measured.duration >= 0.9 && measured.duration <= 1.5);
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference" });
    context.setDefaultTimeout(30000);
    context.setDefaultNavigationTimeout(60000);
    observe(context);
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username: config.username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    const path = `${origin}/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
    const url = `${origin}/canvas-app/canvas/${config.sourceKey}`;
    page = await context.newPage();
    await page.goto(url);
    const source = page.locator('[data-node-id="runtime-node"]');
    await source.waitFor({ state: "visible" });
    await source.click({ position: { x: 20, y: 12 } });
    const prompt = page.getByRole("textbox", { name: "视频提示词", exact: true });
    await prompt.waitFor({ state: "visible" });
    const typed = "浏览器在视频生成前保存的新提示词";
    await prompt.fill(typed);
    const [initialResponse] = await Promise.all([
        page.waitForResponse(admissionResponse),
        page.getByRole("button", { name: "生成", exact: true }).click(),
    ]);
    let admittedResponse = initialResponse;
    if (config.mode === "https-replacement") {
        assert.equal(initialResponse.status(), 422);
        assert.equal((await admissionError(initialResponse)).error.code, "reference_media_requires_url");
        const dialog = page.getByRole("dialog").filter({ hasText: "填写参考素材链接" });
        await dialog.waitFor({ state: "visible" });
        await dialog.getByText("当前渠道只能读取在线素材。请填写对应素材的 HTTPS 链接，用于本次生成。", { exact: true }).waitFor({ state: "visible" });
        assert.equal((await read(await context.request.get(`${origin}/api/v1/canvas-runtime/tasks?projectId=${config.sourceKey}`))).length, 0);
        const useLink = dialog.getByRole("button", { name: "使用链接并生成", exact: true });
        assert.equal(await useLink.isEnabled(), false);
        await dialog.getByRole("textbox", { name: "参考图片 1", exact: true }).fill("http://public-reference.example/a.png");
        assert.equal(await useLink.isEnabled(), false);
        await dialog.getByRole("textbox", { name: "参考图片 1", exact: true }).fill("https://public-reference.example/a.png");
        await page.waitForFunction(() => [...document.querySelectorAll("button")].some(button => button.textContent.trim() === "使用链接并生成" && !button.disabled));
        [admittedResponse] = await Promise.all([page.waitForResponse(response => admissionResponse(response) && response.ok()), useLink.click()]);
        report.original_https_dialog = true;
        assert.equal(report.task_posts.length, 2);
        assert.equal(report.task_posts[0].operation_key, report.task_posts[1].operation_key);
        assert.ok(report.task_posts[0].saved_references[0].storage_key.startsWith("resource:"));
        assert.equal(report.task_posts[1].saved_references[0].storage_key, "");
        assert.equal(report.task_posts[1].saved_references[0].url, "https://public-reference.example/a.png");
        report.same_operation_after_replacement = true;
    }
    const task = await read(admittedResponse);
    assert.match(task.id, /^\d+$/);
    assert.equal(task.clientContext.sourceNodeId, "runtime-node");
    const savedBefore = await read(await context.request.get(`${path}/my-document`));
    assert.equal(savedBefore.source_document.nodes.find(node => node.id === "runtime-node").metadata.prompt, typed);
    assert.equal(JSON.parse(task.inputJson).prompt, typed);
    report.source_saved_before_admission = true;
    const nodeId = task.clientContext.nodeId;
    if (config.mode === "unknown") {
        const failed = await currentTask(context, task.id, task => task.status === "failed");
        assert.equal(failed.canRetry, false);
        assert.equal(failed.canResume, false);
        const node = page.locator(`[data-node-id="${nodeId}"]`);
        await node.locator(".canvas-node-error-content").waitFor({ state: "visible" });
        assert.equal(await node.getByRole("button", { name: /^重新生成/ }).count(), 0);
        report.unknown_retry_unavailable = true;
        await page.reload();
        await page.locator(`[data-node-id="${nodeId}"] .canvas-node-error-content`).waitFor({ state: "visible" });
        const listed = await read(await context.request.get(`${origin}/api/v1/canvas-runtime/tasks?projectId=${config.sourceKey}`));
        assert.deepEqual(listed.map(item => item.id), [task.id]);
        assert.equal(report.task_posts.length, 1);
        report.same_task_after_refresh = true;
    } else {
        const recovered = page.waitForResponse(async response => {
            const target = new URL(response.url());
            if (response.request().method() !== "GET" || !target.pathname.startsWith("/api/v1/canvas-runtime/tasks") || target.pathname.endsWith("/logs") || !response.ok()) return false;
            const data = await response.json().catch(() => undefined);
            return Array.isArray(data) ? data.some(item => item.id === task.id) : data?.id === task.id;
        });
        await page.reload();
        await page.locator(`[data-node-id="${nodeId}"]`).waitFor({ state: "visible" });
        await recovered;
        report.same_task_after_refresh = true;
        const boundResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && response.request().method() === "POST" && response.ok());
        await writeFile(config.releasePath, "Original task observed after browser refresh.\n");
        const bound = await read(await boundResponse);
        assert.equal(bound.result.taskId, task.id);
        assert.equal(bound.result.node.id, nodeId);
        assert.ok(bound.result.node.metadata.storageKey.startsWith("resource:"));
        report.source_bind_applied = true;
        const ready = await currentTask(context, task.id, task => task.status === "succeeded");
        const media = JSON.parse(ready.resultJson).video;
        const downloaded = await context.request.get(`${origin}${media.url}`);
        assert.ok(downloaded.ok());
        assert.equal(createHash("sha256").update(await downloaded.body()).digest("hex"), config.mediaHash);
        await inspectVideo(page, page.locator(`[data-node-id="${nodeId}"]`));
        report.real_video_decoded = true;
        const savedAfter = await read(await context.request.get(`${path}/my-document`));
        const persisted = savedAfter.source_document.nodes.find(node => node.id === nodeId);
        assert.equal(persisted.metadata.storageKey, media.storageKey);
        const originalImage = savedAfter.source_document.nodes.find(node => node.id === "browser-saved-reference");
        assert.equal(originalImage.metadata.storageKey, config.referenceKey);
    }
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference", storageState: { cookies: await context.cookies(), origins: [] } });
    fresh.setDefaultTimeout(30000);
    fresh.setDefaultNavigationTimeout(60000);
    observe(fresh);
    page = await fresh.newPage();
    await page.goto(url);
    const freshNode = page.locator(`[data-node-id="${nodeId}"]`);
    await freshNode.waitFor({ state: "visible" });
    if (config.mode === "unknown") {
        await freshNode.locator(".canvas-node-error-content").waitFor({ state: "visible" });
        assert.equal(await freshNode.getByRole("button", { name: /^重新生成/ }).count(), 0);
    } else await inspectVideo(page, freshNode);
    const finalTasks = await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/tasks?projectId=${config.sourceKey}`));
    assert.deepEqual(finalTasks.map(item => item.id), [task.id]);
    assert.equal(report.task_posts.length, config.mode === "unknown" ? 1 : 2);
    report.fresh_browser_preserved = true;
    assert.deepEqual(report.page_errors, []);
    assert.ok(report.failed_routes.every(item => item.path.startsWith("/api/v1/canvas-runtime/assistant/") || (item.path === "/api/v1/canvas-runtime/tasks" && item.status === 422)));
} catch (error) {
    report.failure_stack = (error.stack || error.message || String(error)).split("\n").filter((line, index) => index === 0 || /^\s*at\s/.test(line)).slice(0, 12).join("\n");
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
