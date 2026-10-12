import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const tasks = "/api/v1/canvas-runtime/tasks";
const output = resolve(config.outputDirectory);
const report = { step: "start", page_errors: [], failed_routes: [], task_posts: 0, text_event_requests: 0 };
report.viewport_requests = [];
const observations = [];
await mkdir(output, { recursive: true });
const server = await createServer({
    server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } },
    logLevel: "silent",
});
await server.listen();
let browser;
let page;
let submitted;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}

async function login(username, cookies) {
    const context = await browser.newContext({
        viewport: { width: 1440, height: 900 },
        ...(cookies ? { storageState: { cookies, origins: [] } } : {}),
    });
    context.setDefaultTimeout(20000);
    context.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return context;
}

function observe(target) {
    target.on("pageerror", error => report.page_errors.push(error.message));
    target.on("request", request => {
        const route = new URL(request.url()).pathname;
        if (route === tasks && request.method() === "POST") {
            report.task_posts++;
            submitted = request.postDataJSON();
        }
        if (route.startsWith(`${tasks}/`) && route.endsWith("/text-events")) report.text_event_requests++;
        if (route === `${path}/viewport`) report.viewport_requests.push({ step: report.step, request: request.postDataJSON() });
    });
    target.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (response.status() >= 400 && route.startsWith("/api/")) report.failed_routes.push({ path: route, status: response.status() });
        if (route === `${path}/viewport`) observations.push(response.json().then(body => {
            report.viewport_requests.push({ step: report.step, status: response.status(), response: body });
        }));
    });
}

async function graph(context, predicate) {
    let document;
    for (let attempt = 0; attempt < 120; attempt++) {
        document = (await read(await context.request.get(`${origin}${path}/my-document`))).source_document;
        if (predicate(document)) return document;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`Gemini原界面画布未达到预期：${JSON.stringify(document)}`);
}

async function open(target) {
    await target.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await target.locator(`[data-node-id="${config.nodeId}"]`).waitFor({ state: "visible" });
    await target.evaluate(() => document.fonts.ready);
    await target.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await target.evaluate(() => new Promise((done, fail) => {
        let previous;
        let stable = 0;
        const started = performance.now();
        const sample = () => {
            const boxes = JSON.stringify([...document.querySelectorAll("[data-node-id]")].map(node => {
                const { x, y, width, height } = node.getBoundingClientRect();
                return [x, y, width, height];
            }));
            stable = boxes === previous ? stable + 1 : 0;
            previous = boxes;
            if (stable >= 4) done();
            else if (performance.now() - started > 5000) fail(new Error("适合屏幕后几何未稳定"));
            else requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
    }));
}

async function save(target) {
    const live = await liveViewport(target);
    const current = await graph(target.context(), document => document.viewport.x === live.x && document.viewport.y === live.y && document.viewport.k === live.k);
    report.before_save = { viewport: current.viewport, live };
    await target.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    // 现有样式隐藏全局 Toast；它在实际保存结束后挂载，顶栏状态仍须可见。
    await target.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
    await target.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}

async function liveViewport(target) {
    return target.locator("[data-canvas-viewport]").evaluate(viewport => ({
        x: parseFloat(viewport.style.getPropertyValue("--canvas-live-x")),
        y: parseFloat(viewport.style.getPropertyValue("--canvas-live-y")),
        k: parseFloat(viewport.style.getPropertyValue("--canvas-live-scale")),
    }));
}

try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined });
    const owner = await login(config.username);
    const member = await login(config.memberUsername);
    const outsider = await login(config.outsiderUsername);
    page = await owner.newPage();
    observe(page);
    await open(page);
    report.opened = { live: await liveViewport(page), viewport: (await read(await owner.request.get(`${origin}${path}/my-document`))).source_document.viewport };
    report.step = "原文字节点输入和生成";
    const node = page.locator(`[data-node-id="${config.nodeId}"]`);
    await node.click({ position: { x: 20, y: 12 } });
    const prompt = page.getByRole("textbox", { name: "文本提示词", exact: true });
    await prompt.waitFor({ state: "visible" });
    await prompt.fill(config.prompt);
    const [response] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST"),
        page.getByRole("button", { name: "生成", exact: true }).click(),
    ]);
    const task = await read(response);
    report.task_id = task.id;
    const declared = JSON.parse(task.inputJson);
    assert.equal(task.clientContext.nodeId, config.nodeId);
    assert.equal(declared.prompt, config.prompt);
    assert.equal(declared.config.systemPrompt, "");
    assert.deepEqual(declared.referenceImages, []);
    assert.deepEqual(declared.textOptions, { stream: true, thinking: false });
    await graph(owner, document => document.nodes.some(node => node.id === config.nodeId && node.metadata.prompt === config.prompt && node.metadata.taskId === task.id));
    report.source_saved_before_admission = true;

    report.step = "真实非流调用保持原节点loading";
    let first;
    for (let attempt = 0; attempt < 100; attempt++) {
        first = await read(await owner.request.get(`${origin}${tasks}/${task.id}/text-deltas`));
        if (first.status === "running") break;
        await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(first.textDraft, "");
    assert.deepEqual(first.deltas, []);
    assert.equal(first.status, "running");
    assert.equal(first.complete, false);
    assert.ok(!first.finalText);
    assert.ok(!JSON.stringify(first).includes(config.reasoning));
    const loading = await graph(owner, document => document.nodes.find(node => node.id === config.nodeId)?.metadata.status === "loading");
    assert.ok(!loading.nodes[0].metadata.content);
    assert.ok(!(await node.innerText()).includes(config.first));
    assert.equal(report.text_event_requests, 0);
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}/text-deltas`)).status(), 404);
    assert.equal((await outsider.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
    report.private_loading_verified = true;
    await page.screenshot({ path: resolve(output, "loading.png") });
    const binding = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && response.request().method() === "POST" && response.ok());
    await writeFile(config.releasePath, "原界面已核对Gemini非流空草稿和loading节点。\n");

    report.step = "原消费者终态直接回填";
    const bound = await read(await binding);
    assert.equal(bound.result.node.id, config.nodeId);
    assert.equal(bound.result.node.metadata.content, config.first + config.second);
    await node.getByText(config.first + config.second, { exact: true }).waitFor({ state: "visible" });
    const final = await graph(owner, document => document.nodes[0].metadata.content === config.first + config.second);
    assert.equal(final.nodes.length, 1);
    assert.equal(final.connections.length, 0);
    assert.ok(!JSON.stringify(final).includes(config.reasoning));
    const privateTask = await read(await owner.request.get(`${origin}${tasks}/${task.id}`));
    assert.equal(JSON.parse(privateTask.resultJson).reasoning, config.reasoning);
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
    const shared = await read(await member.request.get(`${origin}${path}`));
    assert.equal(shared.source_document.nodes[0].metadata.content, config.first + config.second);
    assert.ok(!JSON.stringify(shared).includes(config.reasoning));
    assert.equal((await outsider.request.get(`${origin}${path}`)).status(), 404);
    report.final_bound_and_private_reasoning = true;

    report.step = "全新cookies context恢复正文";
    await save(page);
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    report.saved_and_owner_closed = true;
    const fresh = await login(undefined, cookies);
    const restored = await fresh.newPage();
    page = restored;
    observe(restored);
    await open(restored);
    await restored.locator(`[data-node-id="${config.nodeId}"]`).getByText(config.first + config.second, { exact: true }).waitFor({ state: "visible" });
    assert.ok(!(await restored.locator("body").innerText()).includes(config.reasoning));
    const replay = await read(await fresh.request.post(`${origin}${tasks}`, { data: submitted }));
    assert.equal(replay.id, task.id);
    await save(restored);
    assert.equal(report.task_posts, 1);
    assert.equal(report.text_event_requests, 0);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.failed_routes, []);
    report.fresh_context_restored = true;
    await restored.screenshot({ path: resolve(output, "restored.png") });
    report.step = "complete";
} catch (error) {
    report.failure_stack = error.stack;
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    if (page) await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    throw error;
} finally {
    await Promise.all(observations);
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
