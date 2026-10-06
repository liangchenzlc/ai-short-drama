import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4188";
const output = resolve(".runtime/generation-python-browser");
const report = { source_saved_before_admission: false, same_task_recovered_after_refresh: false, source_bind_applied: false, persisted_after_fresh_browser: false, task_post_count: 0, page_errors: [], failed_routes: [] };
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
    const model = await read(await context.request.post(`${origin}/api/v1/ai-model-configs`, { data: { service_type: "text", name: "浏览器测试文本模型", provider: "openai", model_key: "gpt-4o-mini", base_url: "https://api.openai.com/v1", apikey: "canvas-integration-placeholder-not-a-real-key" } }));
    const preferences = await read(await context.request.get(`${origin}/api/v1/canvas-runtime/workspace/model-config`));
    const modelKey = `host-${model.id}::gpt-4o-mini`;
    await read(await context.request.put(`${origin}/api/v1/canvas-runtime/workspace/model-config`, { data: { expected_row_version: preferences.row_version, preferences: { ...preferences.preferences, model: modelKey, textModel: modelKey, systemPrompt: "浏览器源生成上下文" } } }));
    const project = await read(await context.request.post(`${origin}/api/v1/projects`, { headers: { "Idempotency-Key": "browser-generation-project" }, data: { name: "真实生成任务刷新", aspect: "16:9", workspace_mode: "infinite_canvas" } }));
    const path = `${origin}/api/v1/projects/${project.id}/canvases/${project.primary_canvas_id}`;
    const initial = await read(await context.request.get(`${path}/my-document`));
    const document = initial.source_document;
    document.nodes = [{ id: "browser-runtime-node", type: "text", title: "浏览器真实生成", position: { x: 420, y: 180 }, width: 320, height: 220, metadata: { content: "", prompt: "浏览器源提示词" } }];
    await read(await context.request.post(`${path}/commits`, { headers: { "Idempotency-Key": "browser-generation-seed" }, data: { expected_row_version: initial.row_version, source_document: document } }));
    const url = `${origin}/canvas-app/canvas/${initial.source_key}`;
    page = await context.newPage();
    await page.goto(url);
    const source = page.locator('[data-node-id="browser-runtime-node"]');
    await source.waitFor({ state: "visible" });
    await source.click({ position: { x: 20, y: 12 } });
    const prompt = page.getByRole("textbox", { name: "文本提示词", exact: true });
    await prompt.waitFor({ state: "visible" });
    await prompt.fill("浏览器在生成前刚修改的提示词");
    const [admitted] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/tasks" && response.request().method() === "POST"),
        page.getByRole("button", { name: "生成", exact: true }).click(),
    ]);
    const task = await read(admitted);
    assert.ok(task.id && task.id.match(/^\d+$/));
    const savedBefore = await read(await context.request.get(`${path}/my-document`));
    assert.ok(savedBefore.source_document.nodes.find(node => node.id === "browser-runtime-node" && node.metadata.prompt === "浏览器在生成前刚修改的提示词"));
    assert.equal(JSON.parse(task.inputJson).prompt, "浏览器在生成前刚修改的提示词");
    assert.ok(savedBefore.source_document.nodes.find(node => node.id === task.clientContext.nodeId && node.metadata.taskId === task.id));
    report.source_saved_before_admission = true;
    const recovered = page.waitForResponse(async response => {
        const target = new URL(response.url());
        if (response.request().method() !== "GET" || !target.pathname.startsWith("/api/v1/canvas-runtime/tasks")) return false;
        if (!response.ok() || target.pathname.endsWith("/logs")) return false;
        const data = await response.json().catch(() => undefined);
        return Array.isArray(data) ? data.some(item => item.id === task.id) : data?.id === task.id;
    });
    await page.reload();
    await page.locator(`[data-node-id="${task.clientContext.nodeId}"]`).waitFor({ state: "visible" });
    await recovered;
    report.same_task_recovered_after_refresh = true;
    const boundResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && response.request().method() === "POST" && response.ok());
    await writeFile(config.releasePath, "Browser observed the original task after refresh.\n");
    const bound = await read(await boundResponse);
    assert.equal(bound.result.taskId, task.id);
    assert.equal(bound.result.node.metadata.content, "浏览器刷新后恢复的真实任务正文");
    report.source_bind_applied = true;
    const savedAfter = await read(await context.request.get(`${path}/my-document`));
    assert.equal(savedAfter.source_document.nodes.find(node => node.id === task.clientContext.nodeId).metadata.content, "浏览器刷新后恢复的真实任务正文");
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference", storageState: { cookies: await context.cookies(), origins: [] } });
    fresh.setDefaultTimeout(30000);
    fresh.setDefaultNavigationTimeout(60000);
    observe(fresh);
    page = await fresh.newPage();
    await page.goto(url);
    await page.locator(`[data-node-id="${task.clientContext.nodeId}"]`).getByText("浏览器刷新后恢复的真实任务正文", { exact: true }).waitFor({ state: "visible" });
    const listed = await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/tasks?projectId=${initial.source_key}&pageSize=30`));
    assert.deepEqual(listed.map(item => item.id), [task.id]);
    report.persisted_after_fresh_browser = true;
    assert.equal(report.task_post_count, 1);
    assert.deepEqual(report.page_errors, []);
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
