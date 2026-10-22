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
const report = { scope: "original storyboard with real Agnes through formal stack", step: "start", protocol: config.protocol, width: config.width, page_errors: [], http_errors: [], run_posts: 0, prepare_posts: 0, approve_posts: 0, execute_posts: 0, task_posts: 0, resource_posts: 0, creation_lifecycle: [], trace_errors: [], state_trace: [], provider_prepost_checks: [] };
const pendingTraces = [];
let requestId = 0;
let pageId = 0;
let browser;
let page;
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(".runtime/vite-agnes-storyboard-browser"), server: { host: "127.0.0.1", port: config.browserPort, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
let tracedCount = 0;
function queueTrace(promise) {
    pendingTraces.push(promise.then(() => ({ ok: true }), error => ({ ok: false, error: error.stack ?? String(error) })));
}
async function finishTraces() {
    while (tracedCount < pendingTraces.length) {
        const batch = pendingTraces.slice(tracedCount);
        tracedCount += batch.length;
        const outcomes = await Promise.allSettled(batch);
        for (const outcome of outcomes) {
            if (outcome.status === "rejected") report.trace_errors.push(outcome.reason?.stack ?? String(outcome.reason));
            else if (!outcome.value.ok) report.trace_errors.push(outcome.value.error);
        }
    }
}
function checkLifecycle() {
    for (const [stage, httpStatus] of [["create", 201], ["claim", 200], ["prepare", 200], ["approve", 200], ["execute", 202]]) {
        const entries = report.creation_lifecycle.filter(row => row.stage === stage);
        assert.equal(entries.length, 1, "one original lifecycle " + stage);
        assert.equal(entries[0].http_status, httpStatus, stage);
        assert.equal(entries[0].run_id, report.run_id, stage);
        if (stage === "prepare") assert.equal(entries[0].submission_id, report.submission_id);
        if (stage === "approve") {
            assert.deepEqual(entries[0].approved_submission_ids, [report.submission_id]);
            assert.ok(entries[0].approved_at.every(Boolean));
        }
        if (stage === "execute") assert.equal(entries[0].task_id, report.task_id);
    }
    report.creation_lifecycle_checked = true;
}
function observe(target) {
    const observedPage = ++pageId;
    const requests = new WeakMap();
    target.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    target.on("request", request => {
        const route = new URL(request.url()).pathname;
        if (route.endsWith("/text-events")) report.text_event_requests = (report.text_event_requests || 0) + 1;
        if (request.method() === "POST") {
            if (route === tasks) report.task_posts++;
            if (route === "/api/v1/canvas-runtime/creation-runs") report.run_posts++;
            if (/\/creation-runs\/\d+\/submissions\/prepare$/.test(route)) report.prepare_posts++;
            if (/\/creation-runs\/\d+\/submissions\/approve$/.test(route)) report.approve_posts++;
            if (/\/creation-runs\/\d+\/execute$/.test(route)) report.execute_posts++;
        }
        if (["POST", "PUT"].includes(request.method()) && route.startsWith("/api/v1/canvas-runtime/resources")) report.resource_posts++;
        if (route.endsWith("/viewport") || route.endsWith("/my-document") || route.endsWith("/commits")) {
            const id = ++requestId;
            requests.set(request, id);
            const data = request.postDataJSON();
            report.state_trace.push({ id, phase: "request", page: observedPage, time: performance.now(), step: report.step, path: route, method: request.method(), expected_viewport: data?.expected_viewport, viewport: data?.viewport ?? data?.source_document?.viewport, revision: data?.expected_row_version });
        }
    });
    target.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (response.status() >= 400 && route.startsWith("/api/")) report.http_errors.push({ path: route, status: response.status() });
        if (response.request().method() === "POST" && (route === "/api/v1/canvas-runtime/creation-runs" || /\/creation-runs\/\d+\//.test(route))) {
            queueTrace((async () => {
                const value = await response.json().catch(() => ({}));
                const stage = route === "/api/v1/canvas-runtime/creation-runs" ? "create" : route.split("/").at(-1);
                report.creation_lifecycle.push({
                    stage, path: route, http_status: response.status(),
                    run_id: value?.run?.id ?? value?.runId ?? route.match(/\/creation-runs\/(\d+)\//)?.[1],
                    submission_id: stage === "prepare" ? value?.id : undefined,
                    approved_submission_ids: stage === "approve" ? value?.submissions?.map(item => item.id) : undefined,
                    approved_at: stage === "approve" ? value?.submissions?.map(item => item.approvedAt) : undefined,
                    task_id: stage === "execute" ? value?.id : undefined,
                    execution_epoch: stage === "claim" ? value?.executionEpoch : undefined,
                });
            })());
        }
        const id = requests.get(response.request());
        if (id) queueTrace((async () => {
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
    assert.fail(`Agnes 分镜状态未达到预期：${JSON.stringify(current)}`);
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
async function open(owner, expectedViewport, preparePage) {
    page = await owner.newPage();
    observe(page);
    preparePage?.();
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

const script = () => page.locator('[data-node-id="' + config.nodeId + '"]');
const api = route => origin + route;
const taskRoute = id => tasks + "/" + id;
const runRoute = id => "/api/v1/canvas-runtime/creation-runs/" + id;
async function heldTask(owner, taskId) {
    const held = await read(await owner.request.get(api(taskRoute(taskId))));
    assert.equal(held.id, taskId);
    assert.equal(held.status, "running");
    assert.equal(held.prompt, config.prompt);
    assert.equal(held.operation, "storyboard");
    assert.ok(!held.textDraft);
    const declared = JSON.parse(held.inputJson);
    assert.equal(declared.prompt, config.prompt);
    assert.equal(declared.metadata.nodeId, config.nodeId);
    assert.equal(declared.metadata.sourceNodeId, config.nodeId);
    assert.equal(declared.shotCount, 2);
    assert.equal(declared.shotDurationSeconds, 5);
    assert.equal(declared.requirements, config.requirements);
    assert.deepEqual(declared.projectStyle, config.projectStyle);
    assert.deepEqual(declared.characters, []);
    assert.deepEqual(declared.canvasAssets, []);
    for (const field of ["referenceImages", "referenceVideos", "referenceAudios"]) assert.deepEqual(declared[field], []);
    const document = await graph(owner);
    const loading = document.nodes.find(node => node.id === config.nodeId);
    assert.equal(loading.type, "script");
    assert.equal(loading.metadata.status, "loading");
    assert.equal(loading.metadata.taskId, taskId);
    assert.equal(loading.metadata.composerContent, config.prompt);
    assert.equal(loading.metadata.storyboardShotCount, "2");
    assert.equal(loading.metadata.storyboardShotDuration, "5");
    assert.deepEqual(loading.metadata.storyboard.rows, []);
    assert.ok(!loading.metadata.content);
    assert.equal(document.nodes.length, 2);
    assert.deepEqual(document.connections, []);
    return document;
}
function lifecycle(suffix) {
    return page.waitForResponse(response => {
        const route = new URL(response.url()).pathname;
        return response.request().method() === "POST" && (suffix === "create"
            ? route === "/api/v1/canvas-runtime/creation-runs"
            : new RegExp("/creation-runs/\\d+/" + (["prepare", "approve"].includes(suffix) ? "submissions/" : "") + suffix + "$").test(route));
    }).then(response => ({ response }), error => ({ error }));
}
async function admitted(outcome) {
    const value = await outcome;
    if (value.error) throw value.error;
    return { response: value.response, value: await read(value.response) };
}
async function providerResult() {
    return eventually(async () => {
        try { return JSON.parse(await readFile(config.providerReportPath, "utf8")); }
        catch (error) { if (error.code === "ENOENT" || error instanceof SyntaxError) return undefined; throw error; }
    }, value => value?.results?.length === 1 || value?.error, 180000);
}
function consumerBinding() {
    return page.waitForEvent("response", { timeout: 180000, predicate: response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && response.request().method() === "POST" && response.ok() })
        .then(response => ({ response }), error => ({ error }));
}
async function interruptNaturalStream(owner, taskId, viewport) {
    report.step = "首段真实增量持久化期间关闭窗口";
    const partial = await eventually(async () => read(await owner.request.get(api(taskRoute(taskId)))), value => value.status === "running" && value.textDraftSequence > 0 && Boolean(value.textDraft), 180000);
    const replay = await read(await owner.request.get(api(taskRoute(taskId) + "/text-deltas")));
    assert.equal(replay.status, "running", "首次增量检查必须发生在真实供应商终态之前");
    assert.ok(replay.deltas.length > 0);
    const persisted = replay.deltas.map(delta => delta.content).join("");
    assert.ok(persisted.startsWith(partial.textDraft));
    const document = await graph(owner);
    const node = document.nodes.find(item => item.id === config.nodeId);
    assert.equal(node.metadata.status, "loading");
    assert.ok(!node.metadata.content, "部分JSON不能提前发布为分镜正文");
    assert.deepEqual(node.metadata.storyboard.rows, []);
    const loadingButton = script().locator("button.ant-btn-loading");
    assert.equal(await loadingButton.count(), 1, "首段增量时原生成按钮必须仍有loading反馈");
    assert.ok(await loadingButton.isVisible());
    assert.ok(await loadingButton.isDisabled());
    await page.screenshot({ path: resolve(output, "storyboard-natural-first-delta.png"), fullPage: true });
    const beforeClose = await read(await owner.request.get(api(taskRoute(taskId))));
    assert.equal(beforeClose.status, "running", "关闭窗口前必须仍是同一个running任务");
    const providerProgress = JSON.parse(await readFile(config.providerReportPath, "utf8"));
    assert.equal(providerProgress.submissions, 1);
    assert.equal(providerProgress.requests.length, 1);
    assert.equal(providerProgress.results.length, 0, "自然中断前不能已获得供应商终态结果");
    assert.equal(providerProgress.requests[0].response_bytes, undefined, "自然中断前真实响应尚未读取完毕");
    report.natural_midstream = { task_id: taskId, status_at_first_observation: partial.status, status_before_close: beforeClose.status, sequence_at_first_observation: partial.textDraftSequence, sequence_before_close: beforeClose.textDraftSequence, first_text: partial.textDraft, replay_text: persisted, replay_sequence: replay.deltas.at(-1).sequence, source_status: node.metadata.status, source_content_empty: !node.metadata.content, source_rows: node.metadata.storyboard.rows, text_event_requests_before_close: report.text_event_requests || 0, provider_submissions_before_close: providerProgress.submissions, provider_results_before_close: providerProgress.results.length, provider_response_complete_before_close: false };
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    const fresh = await context(undefined, cookies);
    const same = await read(await fresh.request.get(api(taskRoute(taskId))));
    assert.equal(same.id, taskId);
    assert.ok(same.textDraft.startsWith(persisted));
    report.natural_midstream.status_at_cookie_only_read = same.status;
    report.natural_midstream.sequence_at_cookie_only_read = same.textDraftSequence;
    let bindingOutcome;
    await open(fresh, viewport, () => { bindingOutcome = consumerBinding(); });
    await script().waitFor({ state: "visible" });
    report.natural_midstream.cookie_only_fresh_page = true;
    report.natural_midstream_loading = "passed; real positive delta while running, no stream hold after provider POST";
    return { owner: fresh, bindingOutcome };
}
async function editorScreenshot(name, rows, title) {
    await script().getByRole("button", { name: "全屏编辑", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: title, exact: true });
    await dialog.waitFor({ state: "visible" });
    const descriptions = dialog.getByRole("checkbox", { name: "画面描述", exact: true });
    if (!await descriptions.isChecked()) await descriptions.press("Space");
    assert.ok(await descriptions.isChecked());
    await eventually(() => dialog.locator("tbody tr[data-row-key]").evaluateAll(elements => elements.map(element => element.getAttribute("data-row-key"))), ids => JSON.stringify(ids) === JSON.stringify(rows.map(row => row.id)));
    for (const row of rows) assert.equal(await dialog.locator(`tr[data-row-key="${row.id}"]`).getByPlaceholder("填写画面描述", { exact: true }).inputValue(), row.plotDescription);
    await page.waitForFunction(async () => {
        const editor = document.querySelector('.ant-modal[role="dialog"]');
        if (!editor) return false;
        let previous;
        let stable = 0;
        while (stable < 15) {
            await new Promise(requestAnimationFrame);
            const rect = editor.getBoundingClientRect();
            const style = getComputedStyle(editor);
            const current = JSON.stringify([rect.x, rect.y, rect.width, rect.height, style.opacity, style.transform]);
            const moving = editor.getAnimations({ subtree: true }).some(animation => animation.effect?.getTiming().iterations !== Infinity && ["running", "pending"].includes(animation.playState));
            stable = !moving && current === previous ? stable + 1 : 0;
            previous = current;
        }
        return true;
    });
    await page.screenshot({ path: resolve(output, name), fullPage: true });
    if (config.width === 390) {
        const scroller = dialog.locator(".ant-table-body");
        const metrics = () => scroller.evaluate(element => ({ scroll_left: element.scrollLeft, scroll_width: element.scrollWidth, client_width: element.clientWidth }));
        const before = await metrics();
        assert.ok(before.scroll_width > before.client_width, "窄屏分镜表必须允许横向滚动");
        await scroller.hover();
        await page.mouse.wheel(400, 0);
        const after = await eventually(metrics, value => value.scroll_left > before.scroll_left);
        report.narrow_editor_scroll ??= [];
        report.narrow_editor_scroll.push({ screenshot: name, before, after });
        await page.screenshot({ path: resolve(output, name.replace(/\.png$/, "-scrolled.png")), fullPage: true });
    }
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
}

let owner;
try {
    assert.ok([1440, 390].includes(config.width));
    assert.equal(config.model, "agnes-3.0-flash");
    assert.equal(config.protocol, "chat-completion");
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    owner = await context(config.username);
    const foreign = await context(config.foreignUsername);
    const outsider = await context(config.outsideUsername);
    await open(owner);
    await script().waitFor({ state: "visible" });
    await fit();

    report.step = "原分镜剧情、镜数、时长与Ctrl+Enter";
    const selects = script().locator(".ant-select");
    await selects.nth(0).click();
    await page.locator(".ant-select-dropdown:visible").getByText("2 镜", { exact: true }).click();
    await selects.nth(1).click();
    await page.locator(".ant-select-dropdown:visible").getByText("每镜 5 秒", { exact: true }).click();
    const composer = script().getByRole("textbox", { name: "分镜剧情与项目设定", exact: true });
    await composer.fill(config.prompt);
    const createdRun = lifecycle("create");
    const preparedSubmission = lifecycle("prepare");
    const approvedSubmission = lifecycle("approve");
    const executedTask = lifecycle("execute");
    await composer.press("Control+Enter");
    const created = await admitted(createdRun);
    report.run_id = created.value.run.id;
    assert.equal(created.response.request().postDataJSON().state.kind, "storyboard");
    assert.equal(created.value.run.state.nodeId, config.nodeId);
    assert.equal(created.value.run.state.sourceNodeId, config.nodeId);
    assert.equal(created.value.run.state.logicalModelId, config.modelId);
    assert.deepEqual(created.value.submissions, []);
    const prepared = await admitted(preparedSubmission);
    report.submission_id = prepared.value.id;
    assert.equal(prepared.value.runId, report.run_id);
    assert.equal(prepared.value.itemKey, "storyboard");
    const preparedRequest = prepared.response.request().postDataJSON().request;
    assert.equal(preparedRequest.logicalModelId, config.modelId);
    assert.equal(preparedRequest.operation, "storyboard");
    assert.equal(preparedRequest.prompt, config.prompt);
    const approved = await admitted(approvedSubmission);
    assert.deepEqual(approved.response.request().postDataJSON().submissionIds, [report.submission_id]);
    assert.equal(approved.value.submissions.length, 1);
    assert.equal(approved.value.submissions[0].id, report.submission_id);
    assert.ok(approved.value.submissions[0].approvedAt);
    const executed = await admitted(executedTask);
    const task = executed.value;
    report.task_id = task.id;
    assert.equal(executed.response.request().postDataJSON().submissionId, report.submission_id);
    assert.equal(task.model, config.model);
    assert.equal(task.prompt, config.prompt);
    report.original_composer_created_approved_run = true;
    await graph(owner, document => document.nodes.find(node => node.id === config.nodeId)?.metadata.taskId === task.id);
    await eventually(async () => read(await owner.request.get(api(taskRoute(task.id)))), value => value.status === "running");
    const held = await heldTask(owner, task.id);
    report.style_before_provider = held.nodes.find(node => node.id === "style");
    assert.ok(report.style_before_provider);
    await providerHeld("old-window-running");
    report.source_saved_and_private_loading = true;
    await page.screenshot({ path: resolve(output, "storyboard-held-before-provider.png"), fullPage: true });

    report.step = "供应商POST前保存回执并关闭旧窗口";
    const pendingViewport = await save(owner);
    report.pending_source_saved_before_close = true;
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(undefined, cookies);
    await heldTask(owner, task.id);
    await providerHeld("closed-window-cookie-only-api-read");
    report.closed_before_provider_post = true;

    report.step = "仅cookies新页恢复同一running任务";
    await open(owner, pendingViewport);
    await script().waitFor({ state: "visible" });
    await heldTask(owner, task.id);
    await providerHeld("fresh-window-same-task");
    assert.equal(report.run_posts, 1);
    assert.equal(report.prepare_posts, 1);
    assert.equal(report.approve_posts, 1);
    assert.equal(report.execute_posts, 1);
    assert.equal(report.task_posts, 0);
    report.running_cookie_only_recovery = true;
    for (const other of [foreign, outsider]) {
        assert.equal((await other.request.get(api(taskRoute(task.id)))).status(), 404);
        assert.equal((await other.request.get(api(taskRoute(task.id) + "/text-deltas"))).status(), 404);
        assert.equal((await other.request.get(api(runRoute(report.run_id)))).status(), 404);
    }
    await page.screenshot({ path: resolve(output, "storyboard-fresh-running.png"), fullPage: true });
    let bindingOutcome = consumerBinding();
    await writeFile(config.releasePath, "Original storyboard source saved; old window closed; same task restored with cookies only.\n", { flag: "wx" });
    if (config.naturalMidstream) {
        const recovered = await interruptNaturalStream(owner, task.id, pendingViewport);
        owner = recovered.owner;
        bindingOutcome = recovered.bindingOutcome;
    }

    report.step = "真实动态JSON终文与原消费者回填";
    const ready = await eventually(async () => read(await owner.request.get(api(taskRoute(task.id)))), value => ["succeeded", "failed", "cancelled"].includes(value.status), 180000);
    report.terminal_status = ready.status;
    report.terminal_task = ready;
    const provider = await providerResult();
    report.raw_supplier_text = provider.results?.[0]?.text;
    report.provider_observation_error = provider.error;
    await writeFile(resolve(output, "supplier-text.txt"), report.raw_supplier_text ?? "", "utf8");
    if (ready.status !== "succeeded") {
        report.provider_error = { code: ready.errorCode, message: ready.error, can_retry: ready.canRetry, can_resume: ready.canResume };
        report.private_terminal_text = ready.textDraft;
        const unchanged = await graph(owner);
        report.failed_source_rows = unchanged.nodes.find(node => node.id === config.nodeId).metadata.storyboard.rows;
        assert.deepEqual(report.failed_source_rows, []);
        assert.fail("真实Agnes分镜未成功：" + (ready.errorCode || ready.status));
    }
    assert.equal(provider.results.length, 1);
    assert.equal(provider.results[0].status, "succeeded");
    const result = JSON.parse(ready.resultJson);
    assert.equal(result.mode, "text");
    assert.equal(typeof result.text, "string");
    assert.ok(result.text.trim());
    report.canonical_text = result.text;
    report.title = result.title;
    report.final_rows = result.rows;
    assert.equal(ready.textDraft, report.canonical_text);
    assert.equal(typeof report.title, "string");
    assert.ok(report.title.trim());
    assert.equal(report.final_rows.length, 2);
    assert.deepEqual(report.final_rows.map(row => row.id), [1, 2].map(index => "shot:" + task.id + ":" + index));
    assert.deepEqual(report.final_rows.map(row => row.shotNumber), [1, 2]);
    for (const row of report.final_rows) {
        assert.equal(row.durationSeconds, 5);
        assert.ok(Number.isInteger(row.durationSeconds));
        assert.ok(row.plotDescription.trim());
        assert.equal(row.status, "idle");
        assert.deepEqual(row.characters, []);
        assert.deepEqual(row.assetBindings, []);
    }
    const supplied = JSON.parse(report.raw_supplier_text);
    assert.equal(supplied.title, report.title);
    assert.equal(supplied.rows.length, 2);
    for (const [index, row] of supplied.rows.entries()) {
        assert.equal(row.shotNumber, index + 1);
        assert.equal(row.durationSeconds, 5);
        assert.equal(row.plotDescription, report.final_rows[index].plotDescription);
        assert.deepEqual(row.characters ?? [], []);
        assert.deepEqual(row.assetBindings ?? [], []);
    }
    await writeFile(resolve(output, "generated-storyboard.json"), JSON.stringify({ title: report.title, rows: report.final_rows }, null, 2), "utf8");
    const outcome = await bindingOutcome;
    if (outcome.error) throw outcome.error;
    const bound = await read(outcome.response);
    assert.equal(bound.result.node.id, config.nodeId);
    assert.equal(bound.result.node.title, report.title);
    assert.equal(bound.result.node.metadata.content, report.canonical_text);
    assert.deepEqual(bound.result.node.metadata.storyboard.rows, report.final_rows);
    const final = await graph(owner, document => {
        const node = document.nodes.find(node => node.id === config.nodeId);
        return node?.metadata.content === report.canonical_text && node?.metadata.status === "success";
    });
    const finalScript = final.nodes.find(node => node.id === config.nodeId);
    assert.equal(finalScript.type, "script");
    assert.equal(finalScript.title, report.title);
    assert.equal(finalScript.metadata.taskId, task.id);
    assert.deepEqual(finalScript.metadata.storyboard.rows, report.final_rows);
    assert.equal(final.nodes.length, 2);
    assert.deepEqual(final.connections, []);
    report.original_consumer_bound_storyboard = true;
    const replay = await read(await owner.request.get(api(taskRoute(task.id) + "/text-deltas")));
    assert.equal(replay.status, "succeeded");
    assert.equal(replay.textDraft, report.canonical_text);
    assert.equal(replay.finalText, report.canonical_text);
    assert.ok(replay.deltas.length > 0);
    assert.deepEqual(replay.deltas.map(delta => delta.sequence), Array.from({ length: replay.deltas.length }, (_, index) => index + 1));
    assert.equal(replay.deltas.map(delta => delta.content).join(""), report.raw_supplier_text);
    report.delta_count = replay.deltas.length;
    report.real_delta_replay_matches_supplier_and_canonical_terminal = true;
    await fit();
    await editorScreenshot("storyboard-generated.png", report.final_rows, report.title);
    const finalViewport = await save(owner);

    report.step = "终态保存回执与仅cookies新页恢复";
    const finalCookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(undefined, finalCookies);
    await open(owner, finalViewport);
    await script().waitFor({ state: "visible" });
    await fit();
    const fresh = await graph(owner);
    const restored = fresh.nodes.find(node => node.id === config.nodeId);
    assert.equal(restored.title, report.title);
    assert.equal(restored.metadata.content, report.canonical_text);
    assert.equal(restored.metadata.taskId, task.id);
    assert.deepEqual(restored.metadata.storyboard.rows, report.final_rows);
    const listed = await read(await owner.request.get(api(tasks + "?projectId=" + config.sourceKey)));
    assert.deepEqual(listed.map(row => row.id), [task.id]);
    const runDetail = await read(await owner.request.get(api(runRoute(report.run_id))));
    assert.equal(runDetail.submissions.length, 1);
    assert.equal(runDetail.submissions[0].id, report.submission_id);
    assert.equal(runDetail.submissions[0].taskId, task.id);
    await editorScreenshot("storyboard-generated-restored.png", report.final_rows, report.title);
    report.stable_rows_restored = true;
    await owner.close();
    page = undefined;

    report.step = "分镜与私人任务均拒绝跨账号访问";
    for (const other of [foreign, outsider]) {
        assert.equal((await other.request.get(api(taskRoute(task.id)))).status(), 404);
        assert.equal((await other.request.get(api(taskRoute(task.id) + "/text-deltas"))).status(), 404);
        assert.equal((await other.request.get(api(runRoute(report.run_id)))).status(), 404);
        assert.equal((await other.request.get(api("/api/v1/ai-model-configs/" + config.modelId))).status(), 404);
    }
    assert.equal((await outsider.request.get(api(path + "/my-document"))).status(), 404);
    assert.equal((await foreign.request.get(api(path + "/my-document"))).status(), 404);
    report.foreign_owner_denied = true;
    assert.equal(report.run_posts, 1);
    assert.equal(report.prepare_posts, 1);
    assert.equal(report.approve_posts, 1);
    assert.equal(report.execute_posts, 1);
    assert.equal(report.task_posts, 0);
    assert.equal(report.resource_posts, 0);
    assert.equal(report.provider_prepost_checks.length, 3);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    if (config.width === 390) assert.equal(report.narrow_editor_scroll.length, 3);
    report.content_verdict = "requires manual inspection of actual provider JSON and generated/restored owner screenshots";
    report.wire_messages_mime_raw_sse = "logical request body and decoded supplier SSE collected by dedicated observer; not a network packet capture";
    if (!config.naturalMidstream) report.natural_midstream_loading = "not claimed; provider is held before POST";
    report.character_support = "zero-character case only; project character binding not exercised";
    await finishTraces();
    assert.deepEqual(report.trace_errors, []);
    checkLifecycle();
    report.step = "complete";
} catch (error) {
    report.failure_stack = error.stack ?? String(error);
    if (page && !page.isClosed()) {
        await page.screenshot({ path: resolve(output, "failure.png"), fullPage: true }).catch(() => {});
        await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    }
    process.exitCode = 1;
} finally {
    await finishTraces();
    if (report.trace_errors.length) {
        process.exitCode = 1;
        if (report.step === "complete") report.step = "observation-failed";
    }
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2), "utf8");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
