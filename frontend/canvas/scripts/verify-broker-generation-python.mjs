import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4188";
const output = resolve(".runtime/broker-generation-python-browser");
const text = "浏览器刷新后恢复的真实任务正文";
const report = { source_saved_before_admission: false, same_task_recovered_after_refresh: false, source_bind_applied: false, persisted_after_fresh_browser: false, image_bound_and_loaded: false, image_loaded_after_fresh_browser: false, task_post_count: 0, page_errors: [], failed_routes: [] };
await mkdir(output, { recursive: true });
const server = await createServer({ server: { host: "127.0.0.1", port: 4188, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
function observe(context) {
    context.on("page", current => current.on("pageerror", error => report.page_errors.push(error.stack || error.message)));
    context.on("request", request => {
        if (new URL(request.url()).pathname === "/api/v1/canvas-runtime/tasks" && request.method() === "POST") report.task_post_count++;
    });
    context.on("response", response => {
        if (response.status() >= 400 && new URL(response.url()).pathname.startsWith("/api/")) report.failed_routes.push({ path: new URL(response.url()).pathname, status: response.status() });
    });
}
async function freshContext(context) {
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference", storageState: { cookies: await context.cookies(), origins: [] } });
    fresh.setDefaultTimeout(30000);
    fresh.setDefaultNavigationTimeout(60000);
    observe(fresh);
    return fresh;
}
async function seed(context, kind) {
    const project = await read(await context.request.post(`${origin}/api/v1/projects`, { headers: { "Idempotency-Key": `browser-broker-${kind}-project` }, data: { name: `真实消息队列 ${kind}`, aspect: "16:9", workspace_mode: "infinite_canvas" } }));
    const path = `${origin}/api/v1/projects/${project.id}/canvases/${project.primary_canvas_id}`;
    const initial = await read(await context.request.get(`${path}/my-document`));
    const document = initial.source_document;
    document.nodes = [{ id: `browser-broker-${kind}`, type: kind, title: `浏览器真实 ${kind}`, position: { x: 420, y: 180 }, width: 320, height: 220, metadata: { content: "", prompt: "浏览器源提示词" } }];
    await read(await context.request.post(`${path}/commits`, { headers: { "Idempotency-Key": `browser-broker-${kind}-seed` }, data: { expected_row_version: initial.row_version, source_document: document } }));
    return { path, initial, url: `${origin}/canvas-app/canvas/${initial.source_key}` };
}
function nextTask() {
    return page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/tasks" && response.request().method() === "POST");
}
function nextBind(taskId) {
    return page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && response.request().method() === "POST" && response.ok() && response.request().postDataJSON()?.params?.taskId === taskId);
}
async function loadedImage(nodeId) {
    await page.locator(`[data-node-id="${nodeId}"] img`).filter({ visible: true }).first().waitFor({ state: "visible" });
    await page.waitForFunction(id => [...document.querySelectorAll(`[data-node-id="${id}"] img`)].some(image => image.complete && image.naturalWidth === 37 && image.naturalHeight === 19), nodeId);
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
    const models = {};
    for (const kind of ["text", "image"]) {
        const modelKey = kind === "text" ? "gpt-4o-mini" : "gpt-image-1";
        const model = await read(await context.request.post(`${origin}/api/v1/ai-model-configs`, { data: { service_type: kind, name: `浏览器测试 ${kind}`, provider: "openai", model_key: modelKey, base_url: config.supplierUrl, apikey: "canvas-broker-placeholder-not-a-real-key" } }));
        models[kind] = `host-${model.id}::${modelKey}`;
    }
    const preferences = await read(await context.request.get(`${origin}/api/v1/canvas-runtime/workspace/model-config`));
    await read(await context.request.put(`${origin}/api/v1/canvas-runtime/workspace/model-config`, { data: { expected_row_version: preferences.row_version, preferences: { ...preferences.preferences, model: models.text, textModel: models.text, imageModel: models.image, systemPrompt: "浏览器源生成上下文", count: "1", size: "1:1" } } }));
    const canvas = await seed(context, "text");
    page = await context.newPage();
    await page.goto(canvas.url);
    const source = page.locator('[data-node-id="browser-broker-text"]');
    await source.waitFor({ state: "visible" });
    await source.click({ position: { x: 20, y: 12 } });
    const prompt = page.getByRole("textbox", { name: "文本提示词", exact: true });
    await prompt.waitFor({ state: "visible" });
    await prompt.fill("浏览器在生成前刚修改的提示词");
    const [admitted] = await Promise.all([nextTask(), page.getByRole("button", { name: "生成", exact: true }).click()]);
    const task = await read(admitted);
    assert.match(task.id, /^\d+$/);
    const savedBefore = await read(await context.request.get(`${canvas.path}/my-document`));
    assert.ok(savedBefore.source_document.nodes.find(node => node.id === "browser-broker-text" && node.metadata.prompt === "浏览器在生成前刚修改的提示词"));
    assert.equal(JSON.parse(task.inputJson).prompt, "浏览器在生成前刚修改的提示词");
    assert.ok(savedBefore.source_document.nodes.find(node => node.id === task.clientContext.nodeId && node.metadata.taskId === task.id));
    report.source_saved_before_admission = true;
    const recovered = page.waitForResponse(async response => {
        const target = new URL(response.url());
        if (response.request().method() !== "GET" || !target.pathname.startsWith("/api/v1/canvas-runtime/tasks") || !response.ok() || target.pathname.endsWith("/logs")) return false;
        const data = await response.json().catch(() => undefined);
        return Array.isArray(data) ? data.some(item => item.id === task.id) : data?.id === task.id;
    });
    await page.reload();
    await page.locator(`[data-node-id="${task.clientContext.nodeId}"]`).waitFor({ state: "visible" });
    await recovered;
    report.same_task_recovered_after_refresh = true;
    const boundResponse = nextBind(task.id);
    await writeFile(config.releasePath, "Source browser recovered the original AMQP task.\n");
    const bound = await read(await boundResponse);
    assert.equal(bound.result.taskId, task.id);
    assert.equal(bound.result.node.metadata.content, text);
    report.source_bind_applied = true;
    const savedAfter = await read(await context.request.get(`${canvas.path}/my-document`));
    assert.equal(savedAfter.source_document.nodes.find(node => node.id === task.clientContext.nodeId).metadata.content, text);
    const fresh = await freshContext(context);
    page = await fresh.newPage();
    await page.goto(canvas.url);
    await page.locator(`[data-node-id="${task.clientContext.nodeId}"]`).getByText(text, { exact: true }).waitFor({ state: "visible" });
    const listed = await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/tasks?projectId=${canvas.initial.source_key}&pageSize=30`));
    assert.deepEqual(listed.map(item => item.id), [task.id]);
    report.persisted_after_fresh_browser = true;
    await fresh.close();
    const imageCanvas = await seed(context, "image");
    page = await context.newPage();
    await page.goto(imageCanvas.url);
    const imageSource = page.locator('[data-node-id="browser-broker-image"]');
    await imageSource.waitFor({ state: "visible" });
    await imageSource.click({ position: { x: 20, y: 12 } });
    const imagePrompt = page.getByRole("textbox", { name: "图片提示词", exact: true });
    await imagePrompt.waitFor({ state: "visible" });
    await imagePrompt.fill("真实 AMQP 生成一张紫色图片");
    const imageBoundResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && response.request().method() === "POST" && response.ok());
    const [imageAdmitted] = await Promise.all([nextTask(), page.getByRole("button", { name: "生成", exact: true }).click()]);
    const imageTask = await read(imageAdmitted);
    const imageBound = await read(await imageBoundResponse);
    assert.equal(imageBound.result.taskId, imageTask.id);
    const imageNodeId = imageTask.clientContext.nodeId;
    assert.equal(imageBound.result.node.metadata.naturalWidth, 37);
    await loadedImage(imageNodeId);
    report.image_bound_and_loaded = true;
    const savedImage = await read(await context.request.get(`${imageCanvas.path}/my-document`));
    const imageNode = savedImage.source_document.nodes.find(node => node.id === imageNodeId);
    assert.ok(imageNode.metadata.storageKey.startsWith("resource:"));
    const imageFresh = await freshContext(context);
    page = await imageFresh.newPage();
    await page.goto(imageCanvas.url);
    await loadedImage(imageNodeId);
    report.image_loaded_after_fresh_browser = true;
    assert.equal(report.task_post_count, 2);
    assert.deepEqual(report.page_errors, []);
    const deferredM3 = new Set(["/api/v1/canvas-runtime/assistant/status", "/api/v1/canvas-runtime/assistant/ui-session"]);
    assert.deepEqual(report.failed_routes.filter(item => item.status !== 404 || !deferredM3.has(item.path)), []);
} catch (error) {
    // Keep the failure and stack frames, without HTTP call logs or response bodies.
    const lines = String(error?.stack || error?.message || error).split(/\r?\n/);
    let stack = [lines[0], ...lines.slice(1).filter(line => /^\s+at\s/.test(line))].join("\n");
    for (const secret of [config.password, "canvas-broker-placeholder-not-a-real-key"]) {
        if (typeof secret === "string" && secret) stack = stack.replaceAll(secret, "[redacted]");
    }
    report.failure_stack = stack
        .replace(/(HTTP \d{3} [^\s:]+):[^\n]*/g, "$1: [response omitted]")
        .replace(/\bBearer\s+[^\s"'<>]+/gi, "Bearer [redacted]")
        .replace(/\b(authorization|cookie|set-cookie|x-csrf-token|api[-_]?key|password)\s*[:=][^\n]*/gi, "$1: [redacted]");
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
