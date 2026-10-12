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
const report = { step: "start", protocol: config.protocol, variant: config.variant, width: config.width, page_errors: [], failed_routes: [], task_post_count: 0, resource_post_count: 0, state_trace: [] };
const pendingTraces = [];
let requestId = 0;
let pageId = 0;
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(".runtime/vite-skill-generation-browser"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
function observe(target) {
    const observedPage = ++pageId;
    const requests = new WeakMap();
    target.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    target.on("request", request => {
        const route = new URL(request.url()).pathname;
        if (request.method() === "POST" && route === tasks) report.task_post_count++;
        if (request.method() === "POST" && route === "/api/v1/canvas-runtime/resources") report.resource_post_count++;
        if (route.endsWith("/viewport") || route.endsWith("/my-document") || route.endsWith("/commits")) {
            const id = ++requestId;
            requests.set(request, id);
            const data = request.postDataJSON();
            report.state_trace.push({ id, phase: "request", page: observedPage, time: performance.now(), step: report.step, path: route, method: request.method(), expected_viewport: data?.expected_viewport, viewport: data?.viewport ?? data?.source_document?.viewport, revision: data?.expected_row_version });
        }
    });
    target.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (response.status() >= 400 && route.startsWith("/api/")) report.failed_routes.push({ path: route, status: response.status() });
        const id = requests.get(response.request());
        if (id) pendingTraces.push((async () => {
            const value = await response.json().catch(() => ({}));
            report.state_trace.push({ id, phase: "response", page: observedPage, time: performance.now(), step: report.step, path: route, status: response.status(), viewport: value.viewport ?? value.source_document?.viewport, revision: value.row_version, reason: value.error?.code });
        })());
    });
}
async function context(username, cookies) {
    const next = await browser.newContext({ viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference", ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    next.setDefaultTimeout(20000);
    next.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await next.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await next.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await next.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return next;
}
async function eventually(readValue, predicate) {
    let current;
    for (let attempt = 0; attempt < 150; attempt++) {
        current = await readValue();
        if (predicate(current)) return current;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`Skill 引用状态未达到预期：${JSON.stringify(current)}`);
}
async function graph(owner, predicate = () => true) {
    return eventually(async () => (await read(await owner.request.get(`${origin}${path}/my-document`))).source_document, predicate);
}
async function open(owner) {
    page = await owner.newPage();
    observe(page);
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
}
async function fit() {
    const focusBar = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focusBar.isVisible()) await focusBar.getByTitle("适应画布", { exact: true }).click();
    else await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await page.waitForFunction(async () => {
        const viewport = document.querySelector("[data-canvas-viewport]");
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
async function save(owner) {
    const live = await page.locator("[data-canvas-viewport]").evaluate(viewport => ({ x: parseFloat(viewport.style.getPropertyValue("--canvas-live-x")), y: parseFloat(viewport.style.getPropertyValue("--canvas-live-y")), k: parseFloat(viewport.style.getPropertyValue("--canvas-live-scale")) }));
    await graph(owner, document => Object.entries(live).every(([key, value]) => document.viewport[key] === value));
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await page.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    let owner = await context(config.username);
    const member = await context(config.memberUsername);
    const outsider = await context(config.outsideUsername);
    await open(owner);
    report.step = "原 Skill 连接点创建文字";
    const skill = page.locator('[data-node-id="skill-input"]');
    const template = config.privateMetadata.skillSnapshot?.template || config.privateMetadata.content || "未配置技能模板";
    await skill.getByText(template, { exact: true }).waitFor({ state: "visible" });
    await skill.hover();
    await skill.locator('[data-canvas-connection-rail="right"]').click();
    await page.locator("[data-connection-create-menu]").getByRole("button", { name: "文本", exact: true }).click();
    const connected = await graph(owner, document => document.nodes.length === 2 && document.connections.length === 1);
    const text = connected.nodes.find(node => node.type === "text");
    assert.ok(text);
    assert.equal(connected.connections[0].fromNodeId, "skill-input");
    assert.equal(connected.connections[0].toNodeId, text.id);
    assert.equal(text.position.x, connected.nodes[0].position.x + connected.nodes[0].width + 96);
    report.text_node_id = text.id;
    report.original_connection_created_text = true;
    await fit();
    await page.locator(`[data-node-id="${text.id}"]`).click({ position: { x: 20, y: 12 } });
    await page.getByRole("textbox", { name: "文本提示词", exact: true }).fill(config.prompt);
    report.step = "原生成按钮冻结 Skill 输入";
    const admission = page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST");
    await page.getByRole("button", { name: "生成", exact: true }).click();
    const task = await read(await admission);
    assert.equal(task.prompt, config.expectedPrompt);
    const declared = JSON.parse(task.inputJson);
    assert.equal(declared.prompt, config.expectedPrompt);
    assert.equal(declared.mode, "text");
    assert.deepEqual(declared.referenceImages, []);
    report.task_id = task.id;
    report.skill_prompt_compiled = true;
    await graph(owner, document => document.nodes.find(node => node.id === text.id)?.metadata.taskId === task.id);

    report.step = "私人增量保持原 loading";
    const draft = await eventually(async () => read(await owner.request.get(`${origin}${tasks}/${task.id}/text-deltas`)), value => value.textDraft === config.first);
    assert.equal(draft.status, "running");
    assert.deepEqual(draft.deltas.map(delta => [delta.sequence, delta.content]), [[1, config.first]]);
    const pending = await graph(owner);
    const loading = pending.nodes.find(node => node.id === text.id);
    assert.equal(loading.metadata.status, "loading");
    assert.ok(!loading.metadata.content);
    assert.ok(!(await page.locator(`[data-node-id="${text.id}"]`).innerText()).includes(config.first));
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}/text-deltas`)).status(), 404);
    await page.screenshot({ path: resolve(output, "skill-generation-loading.png") });
    report.private_delta_kept_loading = true;

    report.step = "终态前关闭旧窗口并更新本人模板";
    await save(owner);
    report.pending_source_saved_before_close = true;
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(undefined, cookies);
    const before = await graph(owner);
    const changed = structuredClone(before);
    changed.nodes.find(node => node.id === "skill-input").metadata = config.updatedPrivateMetadata;
    await read(await owner.request.post(`${origin}${path}/commits`, { headers: { "Idempotency-Key": `skill-pending-change-${task.id}` }, data: { expected_row_version: before.revision, source_document: changed } }));
    assert.deepEqual((await graph(owner)).nodes.find(node => node.id === "skill-input").metadata, config.updatedPrivateMetadata);
    const held = await read(await owner.request.get(`${origin}${tasks}/${task.id}`));
    assert.equal(held.status, "running");
    assert.equal(held.prompt, config.expectedPrompt);
    assert.equal(JSON.parse(held.inputJson).prompt, config.expectedPrompt);
    report.closed_window_before_terminal = true;
    report.private_template_changed_after_admission = true;
    await writeFile(config.releasePath, "Old Skill window closed; frozen prompt observed after private template change.\n");

    report.step = "仅 cookies 新窗口恢复同一任务并回填正文";
    const binding = owner.waitForEvent("response", { predicate: response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && response.request().method() === "POST" && response.ok() });
    await open(owner);
    const bound = await read(await binding);
    assert.equal(bound.result.node.id, text.id);
    assert.equal(bound.result.node.metadata.content, config.first + config.second);
    await fit();
    await page.locator(`[data-node-id="${text.id}"]`).getByText(config.first + config.second, { exact: true }).waitFor({ state: "visible" });
    const final = await graph(owner, document => document.nodes.find(node => node.id === text.id)?.metadata.content === config.first + config.second);
    assert.equal(final.nodes.find(node => node.id === text.id).metadata.taskId, task.id);
    assert.deepEqual(final.nodes.find(node => node.id === "skill-input").metadata, config.updatedPrivateMetadata);
    assert.equal(final.nodes.length, 2);
    assert.equal(final.connections.length, 1);
    report.same_task_cookie_only_recovery = true;
    report.original_consumer_bound_text = true;
    await save(owner);
    await page.screenshot({ path: resolve(output, "skill-generation-recovered.png") });
    const finalCookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(undefined, finalCookies);
    await open(owner);
    await fit();
    await page.locator(`[data-node-id="${text.id}"]`).getByText(config.first + config.second, { exact: true }).waitFor({ state: "visible" });
    assert.equal((await graph(owner)).nodes.find(node => node.id === text.id).metadata.taskId, task.id);
    await page.screenshot({ path: resolve(output, "skill-generation-final-restored.png") });
    report.final_cookie_only_recovery = true;
    await owner.close();
    page = undefined;

    report.step = "成员仅见共享成品、模板和过程仍私人";
    await open(member);
    await fit();
    await page.locator('[data-node-id="skill-input"]').getByText("未配置技能模板", { exact: true }).waitFor({ state: "visible" });
    await page.locator(`[data-node-id="${text.id}"]`).getByText(config.first + config.second, { exact: true }).waitFor({ state: "visible" });
    const visible = await page.locator("body").innerText();
    for (const metadata of [config.privateMetadata, config.updatedPrivateMetadata]) {
        for (const value of [metadata.content, metadata.skillSnapshot?.name, metadata.skillSnapshot?.template].filter(Boolean)) assert.ok(!visible.includes(value), value);
    }
    const shared = await graph(member);
    assert.deepEqual(shared.nodes.find(node => node.id === "skill-input").metadata, {});
    assert.deepEqual(shared.nodes.find(node => node.id === text.id).metadata, { content: config.first + config.second, fontSize: 14, generatedFromNodeId: text.id });
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
    assert.equal((await outsider.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
    await page.screenshot({ path: resolve(output, "skill-generation-member.png") });
    report.member_private_input_hidden = true;
    assert.equal(report.task_post_count, 1);
    assert.equal(report.resource_post_count, 0);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.failed_routes, []);
    report.step = "complete";
} catch (error) {
    report.failure_stack = error.stack ?? String(error);
    if (page && !page.isClosed()) {
        await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
        await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    }
    process.exitCode = 1;
} finally {
    await Promise.allSettled(pendingTraces);
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report) + "\n");
