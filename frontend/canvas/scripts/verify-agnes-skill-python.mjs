import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = `http://127.0.0.1:${config.browserPort}`;
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const tasks = "/api/v1/canvas-runtime/tasks";
const output = resolve(config.outputDirectory);
const report = { scope: "original Skill snapshot with real Agnes through formal stack", step: "start", protocol: config.protocol, variant: config.variant, width: config.width, page_errors: [], failed_routes: [], task_post_count: 0, resource_post_count: 0, state_trace: [], provider_prepost_checks: [] };
const pendingTraces = [];
let requestId = 0;
let pageId = 0;
let browser;
let page;
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(".runtime/vite-agnes-skill-browser"), server: { host: "127.0.0.1", port: config.browserPort, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();

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
        if (["POST", "PUT"].includes(request.method()) && route.startsWith("/api/v1/canvas-runtime/resources")) report.resource_post_count++;
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
    next.setDefaultTimeout(30000);
    next.setDefaultNavigationTimeout(60000);
    if (cookies) assert.deepEqual((await next.storageState()).origins, []);
    else await read(await next.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await next.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await next.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return next;
}
async function eventually(readValue, predicate, timeout = 30000) {
    const deadline = Date.now() + timeout;
    let current;
    while (Date.now() < deadline) {
        current = await readValue();
        if (predicate(current)) return current;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`Agnes Skill 状态未达到预期：${JSON.stringify(current)}`);
}
async function graph(owner, predicate = () => true) {
    return eventually(async () => (await read(await owner.request.get(`${origin}${path}/my-document`))).source_document, predicate);
}
async function liveViewport() {
    return page.locator("[data-canvas-viewport]").evaluate(viewport => ({ x: parseFloat(viewport.style.getPropertyValue("--canvas-live-x")), y: parseFloat(viewport.style.getPropertyValue("--canvas-live-y")), k: parseFloat(viewport.style.getPropertyValue("--canvas-live-scale")) }));
}
const viewportKeys = ["x", "y", "k"];
function float64Ordinal(value) {
    if (!Number.isFinite(value)) return null;
    const view = new DataView(new ArrayBuffer(8));
    view.setFloat64(0, value);
    const bits = view.getBigUint64(0);
    const sign = 1n << 63n;
    return bits >= sign ? -(bits - sign) : bits;
}
function ulpDistance(actual, expected) {
    const left = float64Ordinal(actual);
    const right = float64Ordinal(expected);
    if (left === null || right === null) return null;
    const distance = left - right;
    return distance < 0n ? -distance : distance;
}
function viewportEquivalent(actual, expected) {
    return actual && viewportKeys.every(key => {
        const distance = ulpDistance(actual[key], expected[key]);
        return distance !== null && distance <= 1n;
    });
}
function viewportEvidence(actual, expected) {
    return {
        difference: Object.fromEntries(viewportKeys.map(key => [key, actual[key] - expected[key]])),
        ulp_distance: Object.fromEntries(viewportKeys.map(key => [key, ulpDistance(actual[key], expected[key])?.toString() ?? null])),
    };
}
async function open(owner, expectedViewport) {
    page = await owner.newPage();
    observe(page);
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    if (expectedViewport) {
        const actual = await eventually(liveViewport, viewport => viewportKeys.every(key => Number.isFinite(expectedViewport[key]) && viewport[key] === expectedViewport[key]));
        assert.ok(viewportKeys.every(key => actual[key] === expectedViewport[key]), "新窗口必须在fit前精确恢复持久化视口");
        report.state_trace.push({ phase: "fresh_initial_live_viewport", step: report.step, viewport: actual, expected_viewport: expectedViewport, ...viewportEvidence(actual, expectedViewport) });
    }
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
    const live = await liveViewport();
    assert.ok(viewportKeys.every(key => Number.isFinite(live[key])));
    await graph(owner, document => viewportEquivalent(document.viewport, live));
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await page.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
    const acknowledged = await graph(owner, document => viewportEquivalent(document.viewport, live));
    const stored = acknowledged.viewport;
    report.state_trace.push({ phase: "pre_close_viewport_ack", step: report.step, live, stored, ...viewportEvidence(stored, live) });
    return stored;
}
async function providerHeld(label) {
    const provider = await eventually(async () => {
        try { return JSON.parse(await readFile(config.providerReportPath, "utf8")); }
        catch (error) { if (error.code === "ENOENT" || error instanceof SyntaxError) return undefined; throw error; }
    }, value => value?.worker_pid && value?.submissions === 0 && Array.isArray(value?.requests));
    assert.equal(provider.submissions, 0);
    assert.deepEqual(provider.requests, []);
    assert.deepEqual(provider.results, []);
    report.provider_prepost_checks.push({ label, submissions: provider.submissions, request_count: provider.requests.length, result_count: provider.results.length, worker_pid: provider.worker_pid });
}
async function heldTask(owner, taskId, textId) {
    const held = await read(await owner.request.get(`${origin}${tasks}/${taskId}`));
    assert.equal(held.id, taskId);
    assert.equal(held.status, "running");
    assert.equal(held.prompt, config.expectedPrompt);
    const declared = JSON.parse(held.inputJson);
    assert.equal(declared.prompt, config.expectedPrompt);
    assert.equal(declared.metadata.nodeId, textId);
    assert.equal(declared.metadata.sourceNodeId, textId);
    const document = await graph(owner);
    const loading = document.nodes.find(node => node.id === textId);
    assert.equal(loading.type, "text");
    assert.equal(loading.metadata.status, "loading");
    assert.equal(loading.metadata.taskId, taskId);
    assert.ok(!loading.metadata.content);
    return document;
}

try {
    assert.equal(config.width, 1440);
    assert.equal(config.variant, "snapshot");
    assert.equal(config.model, "agnes-3.0-flash");
    assert.equal(config.protocol, "chat-completion");
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    let owner = await context(config.username);
    const member = await context(config.memberUsername);
    const outsider = await context(config.outsideUsername);
    await open(owner);
    report.step = "原 Skill 连接点创建文字";
    const skill = page.locator('[data-node-id="skill-input"]');
    await skill.getByText(config.privateMetadata.skillSnapshot.template, { exact: true }).waitFor({ state: "visible" });
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
    report.step = "原生成按钮受理已编译私人模板";
    const admission = page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST");
    await page.getByRole("button", { name: "生成", exact: true }).click();
    const admitted = await admission;
    const task = await read(admitted);
    report.task_id = task.id;
    assert.equal(admitted.request().postDataJSON().logicalModelId, config.modelId);
    assert.equal(task.model, config.model);
    assert.equal(task.prompt, config.expectedPrompt);
    const declared = JSON.parse(task.inputJson);
    assert.equal(declared.prompt, config.expectedPrompt);
    assert.equal(declared.mode, "text");
    assert.equal(declared.metadata.nodeId, text.id);
    assert.equal(declared.metadata.sourceNodeId, text.id);
    for (const field of ["referenceImages", "referenceVideos", "referenceAudios"]) assert.deepEqual(declared[field], []);
    report.skill_prompt_compiled = true;
    await graph(owner, document => document.nodes.find(node => node.id === text.id)?.metadata.taskId === task.id);
    await eventually(async () => read(await owner.request.get(`${origin}${tasks}/${task.id}`)), value => value.status === "running");
    await heldTask(owner, task.id, text.id);
    await providerHeld("old-window-running");
    for (const other of [member, outsider]) {
        assert.equal((await other.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
        assert.equal((await other.request.get(`${origin}${tasks}/${task.id}/text-deltas`)).status(), 404);
    }
    await page.screenshot({ path: resolve(output, "skill-held-before-provider.png") });

    report.step = "供应商 POST 前关闭旧窗口并更新本人模板";
    const pendingViewport = await save(owner);
    report.pending_source_saved_before_close = true;
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(undefined, cookies);
    const before = await graph(owner);
    const changed = structuredClone(before);
    changed.nodes.find(node => node.id === "skill-input").metadata = config.updatedPrivateMetadata;
    await read(await owner.request.post(`${origin}${path}/commits`, { headers: { "Idempotency-Key": `agnes-skill-pending-change-${task.id}` }, data: { expected_row_version: before.revision, source_document: changed } }));
    assert.deepEqual((await graph(owner)).nodes.find(node => node.id === "skill-input").metadata, config.updatedPrivateMetadata);
    await heldTask(owner, task.id, text.id);
    await providerHeld("closed-window-template-changed");
    report.closed_window_before_provider_post = true;
    report.private_template_changed_after_admission = true;

    report.step = "仅 cookies 新页恢复同一 running 任务后放行真实供应商";
    await open(owner, pendingViewport);
    await page.locator('[data-node-id="skill-input"]').getByText(config.updatedPrivateMetadata.skillSnapshot.template, { exact: true }).waitFor({ state: "visible" });
    await heldTask(owner, task.id, text.id);
    await providerHeld("fresh-window-same-task");
    assert.equal(report.task_post_count, 1);
    report.same_task_cookie_only_running_recovery = true;
    await page.screenshot({ path: resolve(output, "skill-fresh-running.png") });
    const binding = owner.waitForEvent("response", { timeout: 180000, predicate: response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && response.request().method() === "POST" && response.ok() });
    const bindingOutcome = binding.then(response => ({ response }), error => ({ error }));
    await writeFile(config.releasePath, "Original window closed; private template changed; same frozen task restored with cookies only.\n", { flag: "wx" });

    report.step = "真实动态终文与原消费者绑定";
    const ready = await eventually(async () => read(await owner.request.get(`${origin}${tasks}/${task.id}`)), value => ["succeeded", "failed", "cancelled"].includes(value.status), 180000);
    report.terminal_status = ready.status;
    if (ready.status !== "succeeded") {
        report.provider_error = { code: ready.errorCode, message: ready.error, can_retry: ready.canRetry, can_resume: ready.canResume };
        assert.fail(`真实 Agnes Skill 未成功：${ready.errorCode || ready.status}`);
    }
    const result = JSON.parse(ready.resultJson);
    assert.equal(result.mode, "text");
    const actual = result.text;
    assert.equal(typeof actual, "string");
    assert.ok(actual.trim());
    report.text = actual;
    report.canonical_text = actual;
    assert.equal(ready.textDraft, actual);
    const provider = await eventually(async () => {
        try { return JSON.parse(await readFile(config.providerReportPath, "utf8")); }
        catch (error) { if (error.code === "ENOENT" || error instanceof SyntaxError) return undefined; throw error; }
    }, value => value?.results?.length === 1 && value.results[0].status === "succeeded");
    const raw = provider.results[0].text;
    assert.equal(typeof raw, "string");
    report.raw_supplier_text = raw;
    report.content_automated_checks = { old_markers_present: ["清晨车站", "红色礼盒"].every(marker => actual.includes(marker)), within_requested_length: [...actual.trim()].length >= 30 && [...actual.trim()].length <= 80, changed_template_markers_absent: !actual.includes("深夜机场") && !actual.includes("蓝色行李箱") };
    assert.ok(Object.values(report.content_automated_checks).every(Boolean));
    await writeFile(resolve(output, "generated-text.txt"), actual + "\n");
    const outcome = await bindingOutcome;
    if (outcome.error) throw outcome.error;
    const bound = await read(outcome.response);
    assert.equal(bound.result.node.id, text.id);
    assert.equal(bound.result.node.metadata.content, actual);
    const final = await graph(owner, document => document.nodes.find(node => node.id === text.id)?.metadata.content === actual);
    assert.equal(final.nodes.find(node => node.id === text.id).metadata.taskId, task.id);
    assert.deepEqual(final.nodes.find(node => node.id === "skill-input").metadata, config.updatedPrivateMetadata);
    assert.equal(final.nodes.length, 2);
    assert.equal(final.connections.length, 1);
    report.original_consumer_bound_text = true;
    const replay = await read(await owner.request.get(`${origin}${tasks}/${task.id}/text-deltas`));
    assert.equal(replay.status, "succeeded");
    assert.equal(replay.textDraft, actual);
    assert.equal(replay.finalText, actual);
    assert.ok(replay.deltas.length > 0);
    assert.deepEqual(replay.deltas.map(delta => delta.sequence), Array.from({ length: replay.deltas.length }, (_, index) => index + 1));
    assert.equal(replay.deltas.map(delta => delta.content).join(""), raw);
    report.delta_count = replay.deltas.length;
    report.real_delta_replay_matches_supplier_and_canonical_terminal = true;
    await fit();
    await page.locator(`[data-node-id="${text.id}"]`).getByText(actual, { exact: true }).waitFor({ state: "visible" });
    const finalViewport = await save(owner);
    await page.screenshot({ path: resolve(output, "skill-generated.png") });

    report.step = "仅 cookies 终态恢复不增加 POST";
    const finalCookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(undefined, finalCookies);
    await open(owner, finalViewport);
    await page.locator(`[data-node-id="${text.id}"]`).getByText(actual, { exact: true }).waitFor({ state: "visible" });
    const restored = await graph(owner);
    assert.equal(restored.nodes.find(node => node.id === text.id).metadata.content, actual);
    assert.equal(restored.nodes.find(node => node.id === text.id).metadata.taskId, task.id);
    const listed = await read(await owner.request.get(`${origin}${tasks}?projectId=${config.sourceKey}`));
    assert.deepEqual(listed.map(task => task.id), [task.id]);
    report.final_cookie_only_recovery = true;
    await page.screenshot({ path: resolve(output, "skill-final-restored.png") });
    await owner.close();
    page = undefined;

    report.step = "成员仅见共享成品，模板与任务过程保持私人";
    await open(member);
    await fit();
    await page.locator('[data-node-id="skill-input"]').getByText("未配置技能模板", { exact: true }).waitFor({ state: "visible" });
    await page.locator(`[data-node-id="${text.id}"]`).getByText(actual, { exact: true }).waitFor({ state: "visible" });
    const visible = await page.locator("body").innerText();
    for (const metadata of [config.privateMetadata, config.updatedPrivateMetadata]) {
        for (const value of [metadata.content, metadata.skillSnapshot.name, metadata.skillSnapshot.template]) assert.ok(!visible.includes(value), value);
    }
    const shared = await graph(member);
    assert.deepEqual(shared.nodes.find(node => node.id === "skill-input").metadata, {});
    assert.deepEqual(shared.nodes.find(node => node.id === text.id).metadata, { content: actual, fontSize: 14, generatedFromNodeId: text.id });
    for (const other of [member, outsider]) {
        assert.equal((await other.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
        assert.equal((await other.request.get(`${origin}${tasks}/${task.id}/text-deltas`)).status(), 404);
        assert.equal((await other.request.get(`${origin}/api/v1/ai-model-configs/${config.modelId}`)).status(), 404);
    }
    assert.equal((await outsider.request.get(`${origin}${path}/my-document`)).status(), 404);
    await page.screenshot({ path: resolve(output, "skill-member.png") });
    report.member_private_input_hidden = true;
    assert.equal(report.task_post_count, 1);
    assert.equal(report.resource_post_count, 0);
    assert.equal(report.provider_prepost_checks.length, 3);
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
