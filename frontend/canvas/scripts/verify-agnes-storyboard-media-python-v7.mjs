import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { isDeepStrictEqual } from "node:util";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
assert.ok([1440, 390].includes(config.width));
assert.deepEqual(config.selectedRowIndexes, [0, 2]);
assert.equal(config.rows.length, 3);
assert.deepEqual(config.selectedRowIndexes.map(index => config.rows[index].durationSeconds), [4, 5]);
for (const row of config.rows) {
    for (const field of ["plotDescription", "imageGenerationPrompt", "videoMotionPrompt"]) assert.ok(row[field]?.trim(), field);
}
const origin = "http://127.0.0.1:" + config.browserPort;
const canvasPath = "/api/v1/projects/" + config.projectId + "/canvases/" + config.canvasId;
const tasksPath = "/api/v1/canvas-runtime/tasks";
const resourcesPath = "/api/v1/canvas-runtime/resources";
const assetsPath = "/api/v1/canvas-runtime/assets";
const bindPath = "/api/v1/canvas-runtime/ops/canvas.task.bind";
const commitPath = canvasPath + "/commits";
const output = resolve(config.outputDirectory);
const report = {
    scope: "original storyboard Table with real Agnes image and direct video through formal stack",
    input_revision: "J/v7 reuses C geometry inputs; distinct execution from failed A-I; J/v6 not executed",
    previous_attempt_verdicts: "A-I failures preserved; B content failed",
    width: config.width, canvas_id: config.canvasId, project_id: config.projectId, source_key: config.sourceKey,
    step: "start", page_errors: [], http_errors: [], trace_errors: [],
    submissions: [], bindings: [], document_commits: [], resource_posts: 0, stages: {}, viewport_checks: [], private_access: [],
};
await mkdir(output, { recursive: true });
let browser;
let server;
let page;
let owner;
let activeStage;
let observedPage = 0;
let traceCount = 0;
const pendingTraces = [];
const script = () => page.locator('[data-node-id="' + config.nodeId + '"]');
const source = document => document.nodes.find(node => node.id === config.nodeId);
const rows = document => source(document).metadata.storyboard.rows;
const batches = document => source(document).metadata.generationBatches || [];
const batchFor = (document, id) => batches(document).find(batch => batch.id === id);
const editor = () => page.getByRole("dialog", { name: config.title, exact: true });
const sha256 = bytes => createHash("sha256").update(bytes).digest("hex");
const sorted = values => [...values].sort();
const allTaskIds = stage => [...new Set([...(stage.original_task_ids || []), ...(stage.all_task_ids || []), ...(stage.task_ids || [])])];
const knownUnacceptedVideo = value => value.status === "failed"
    && value.stage === "failed" && ["provider_rate_limit", "provider_queue_full"].includes(value.errorCode)
    && value.canRetry === true && value.canResume === false
    && !value.providerRequestId && !value.resultJson && (value.outputs || []).length === 0;
function assertSubmissionKnown(value) {
    assert.ok(!["submission_unknown", "unknown"].includes(value.stage)
        && !["provider_acceptance_unknown", "provider_submission_unknown"].includes(value.errorCode), "受理状态未知，停止且不得重提：" + JSON.stringify(taskSnapshot(value)));
}

async function read(response) {
    assert.ok(response.ok(), "HTTP " + response.status() + " " + new URL(response.url()).pathname + ": " + await response.text());
    return response.json();
}
async function eventually(readValue, predicate, timeout = 45000) {
    const deadline = Date.now() + timeout;
    let last;
    while (Date.now() < deadline) {
        last = await readValue();
        if (predicate(last)) return last;
        await new Promise(resolve => setTimeout(resolve, 150));
    }
    assert.fail("真实分镜媒体状态未达到预期：" + JSON.stringify(last));
}
async function graph(current = owner, predicate = () => true, timeout) {
    return eventually(async () => (await read(await current.request.get(origin + canvasPath + "/my-document"))).source_document, predicate, timeout);
}
async function task(id, current = owner) {
    return read(await current.request.get(origin + tasksPath + "/" + id));
}
async function listed(current = owner) {
    return read(await current.request.get(origin + tasksPath + "?projectId=" + encodeURIComponent(config.sourceKey) + "&pageSize=100"));
}
function taskSnapshot(value) {
    return {
        id: value.id, status: value.status, type: value.type, model: value.model,
        client_context: value.clientContext, stage: value.stage, result_state: value.resultState,
        result_present: Boolean(value.resultJson), output_count: value.outputs?.length || 0,
        error_code: value.errorCode, error: value.error, can_retry: value.canRetry,
        can_resume: value.canResume, failure_diagnostics: value.failureDiagnostics,
        provider_request_id: value.providerRequestId, retry_of: value.retryOf,
        client_operation_id: value.clientOperationId, attempt_group_id: value.attemptGroupId,
    };
}
function snapshot(document, batch) {
    return {
        viewport: document.viewport, batch,
        source_rows: rows(document), connections: document.connections,
        nodes: batch.items.map(item => {
            const node = document.nodes.find(node => node.id === item.nodeId);
            return { id: node.id, type: node.type, metadata: node.metadata };
        }),
    };
}
function documentIdentity(document) {
    return {
        source_key: document.id, source_node_id: config.nodeId,
        row_ids: rows(document).map(row => row.id), connections: document.connections,
        batches: batches(document).map(batch => ({
            id: batch.id, mode: batch.mode, sourceNodeId: batch.sourceNodeId,
            items: batch.items.map(item => ({ id: item.id, rowId: item.rowId, nodeId: item.nodeId, taskId: item.taskId })),
        })),
        node_tasks: document.nodes.filter(node => node.metadata?.taskId).map(node => ({ id: node.id, task_id: node.metadata.taskId })),
    };
}
function documentTaskStates(document) {
    return {
        viewport: document.viewport,
        batches: batches(document).map(batch => ({
            id: batch.id, mode: batch.mode, sourceNodeId: batch.sourceNodeId,
            items: batch.items.map(item => ({
                id: item.id, rowId: item.rowId, nodeId: item.nodeId, taskId: item.taskId,
                status: item.status, retryCount: item.retryCount,
            })),
        })),
        node_tasks: document.nodes.filter(node => node.metadata?.taskId).map(node => ({
            id: node.id, task_id: node.metadata.taskId, task_status: node.metadata.taskStatus,
            content_present: Boolean(node.metadata.content),
        })),
    };
}
function queueTrace(promise) {
    pendingTraces.push(promise.then(() => ({ ok: true }), error => ({ ok: false, error: error.stack ?? String(error) })));
}
async function finishTraces() {
    while (traceCount < pendingTraces.length) {
        const pending = pendingTraces.slice(traceCount);
        traceCount += pending.length;
        for (const result of await Promise.allSettled(pending)) {
            if (result.status === "rejected") report.trace_errors.push(result.reason?.stack ?? String(result.reason));
            else if (!result.value.ok) report.trace_errors.push(result.value.error);
        }
    }
    assert.deepEqual(report.trace_errors, []);
}
function assertNoCredentials(value) {
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
        if (/^(?:apiKey|secretKey|authorization|password|cookie|accessToken|refreshToken)$/i.test(key)) {
            assert.ok(child === undefined || child === null || child === "", "证据请求不得包含凭据字段：" + key);
        } else assertNoCredentials(child);
    }
}
function observe(target) {
    const pageNumber = ++observedPage;
    const requests = new WeakMap();
    target.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    target.on("request", request => {
        const route = new URL(request.url()).pathname;
        if (request.method() === "POST" && [tasksPath, bindPath, commitPath].includes(route)) {
            const entry = { stage: activeStage, page: pageNumber, time: Date.now(), started_at: new Date().toISOString(), method: request.method(), path: route };
            try {
                const body = request.postDataJSON();
                assertNoCredentials(body);
                entry.request = route === commitPath
                    ? {
                        expected_row_version: body.expected_row_version,
                        document_identity: documentIdentity(body.source_document),
                        document_task_states: documentTaskStates(body.source_document),
                    }
                    : body;
            } catch (error) {
                report.trace_errors.push(error.stack ?? String(error));
                return;
            }
            if (route === tasksPath) {
                entry.idempotency_key = request.headers()["idempotency-key"];
                report.submissions.push(entry);
            } else if (route === bindPath) report.bindings.push(entry);
            else {
                entry.idempotency_key = request.headers()["idempotency-key"];
                report.document_commits.push(entry);
            }
            requests.set(request, entry);
        }
        if (["POST", "PUT"].includes(request.method()) && route.startsWith(resourcesPath)) report.resource_posts++;
    });
    target.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (response.status() >= 400 && route.startsWith("/api/")) report.http_errors.push({ path: route, status: response.status() });
        const entry = requests.get(response.request());
        if (entry) queueTrace((async () => {
            entry.http_status = response.status();
            entry.response_received_at = new Date().toISOString();
            const value = await response.json();
            if (route === tasksPath) entry.response = taskSnapshot(value);
            else if (route === bindPath) {
                const receipt = value.result || {};
                entry.response = {
                    op: value.op, op_id: value.opId, replayed: value.replayed, revision: value.revision,
                    canvas_id: receipt.canvasId, node_id: receipt.nodeId, task_id: receipt.taskId,
                    applied: receipt.applied, already_bound: receipt.alreadyBound,
                    binding_status: receipt.bindingStatus, output_index: receipt.outputIndex,
                    storage_key: receipt.storageKey, resource_id: receipt.resourceId,
                };
            } else {
                entry.response = {
                    canvas_id: value.id, project_id: value.project_id,
                    source_key: value.source_key, row_version: value.row_version,
                };
            }
            entry.finished_at = new Date().toISOString();
        })());
    });
}
async function context(username, cookies) {
    const next = await browser.newContext({
        viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference",
        ...(cookies ? { storageState: { cookies, origins: [] } } : {}),
    });
    next.setDefaultTimeout(30000);
    next.setDefaultNavigationTimeout(60000);
    if (cookies) assert.deepEqual((await next.storageState()).origins, []);
    else await read(await next.request.post(origin + "/api/v1/auth/login", {
        headers: { Origin: origin }, data: { username, password: config.password },
    }));
    const csrf = (await next.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await next.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return next;
}
async function liveViewport() {
    return page.locator("[data-canvas-viewport]").evaluate(viewport => ({
        x: parseFloat(viewport.style.getPropertyValue("--canvas-live-x")),
        y: parseFloat(viewport.style.getPropertyValue("--canvas-live-y")),
        k: parseFloat(viewport.style.getPropertyValue("--canvas-live-scale")),
    }));
}
const viewportKeys = ["x", "y", "k"];
function ordinal(value) {
    if (!Number.isFinite(value)) return null;
    const view = new DataView(new ArrayBuffer(8));
    view.setFloat64(0, value);
    const bits = view.getBigUint64(0);
    const sign = 1n << 63n;
    return bits >= sign ? -(bits - sign) : bits;
}
function equivalent(actual, expected) {
    return actual && viewportKeys.every(key => {
        const left = ordinal(actual[key]);
        const right = ordinal(expected[key]);
        return left !== null && right !== null && (left > right ? left - right : right - left) <= 1n;
    });
}
async function open(current, expectedViewport) {
    page = await current.newPage();
    observe(page);
    await page.goto(origin + "/canvas-app/canvas/" + config.sourceKey);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    if (expectedViewport) {
        const actual = await eventually(liveViewport, value => viewportKeys.every(key => value[key] === expectedViewport[key]));
        assert.deepEqual(actual, expectedViewport, "恢复视口必须在fit前核对");
        report.viewport_checks.push({ stage: activeStage, step: report.step, phase: "fresh_before_fit", actual, expected: expectedViewport });
    }
    const close = page.getByRole("button", { name: "关闭助手", exact: true });
    if (await close.isVisible()) await close.click();
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
async function save() {
    const live = await liveViewport();
    assert.ok(viewportKeys.every(key => Number.isFinite(live[key])));
    await graph(owner, document => equivalent(document.viewport, live));
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await page.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
    const document = await graph(owner, document => equivalent(document.viewport, live));
    report.viewport_checks.push({ stage: activeStage, step: report.step, phase: "pre_close_ack", live, stored: document.viewport });
    return document.viewport;
}
async function persistedPreClose(stage, originalBatch, preSubmitViewport) {
    const live = await liveViewport();
    assert.ok(equivalent(live, preSubmitViewport), "批准后到关窗前视口必须保持已保存值");
    const identity = batch => batch.items.map(item => [item.id, item.rowId, item.nodeId, item.taskId]);
    const matches = document => {
        const batch = batchFor(document, originalBatch.id);
        return batch && batch.sourceNodeId === originalBatch.sourceNodeId
            && JSON.stringify(identity(batch)) === JSON.stringify(identity(originalBatch))
            && stage.node_ids.every((id, index) => document.nodes.find(node => node.id === id)?.metadata.taskId === stage.original_task_ids[index]);
    };
    const ack = await eventually(async () => {
        await finishTraces();
        return report.document_commits.find(entry => {
            const document = entry.request.document_identity;
            const batch = document.batches.find(value => value.id === originalBatch.id);
            return entry.stage === activeStage && entry.http_status === 200
                && Date.parse(entry.started_at) >= Date.parse(stage.approval_requested_at)
                && batch && batch.sourceNodeId === originalBatch.sourceNodeId
                && JSON.stringify(identity(batch)) === JSON.stringify(identity(originalBatch))
                && stage.node_ids.every((id, index) => document.node_tasks.some(node => node.id === id && node.task_id === stage.original_task_ids[index]));
        });
    }, value => Boolean(value));
    assert.ok(ack.idempotency_key);
    assert.ok(stage.submissions.every(entry => Date.parse(ack.started_at) >= Date.parse(entry.response_received_at)));
    assert.equal(ack.response.canvas_id, config.canvasId);
    assert.equal(ack.response.project_id, config.projectId);
    assert.equal(ack.response.source_key, config.sourceKey);
    assert.match(ack.response.row_version, /^[1-9]\d*$/);
    assert.ok(BigInt(ack.response.row_version) > BigInt(ack.request.expected_row_version));
    assert.ok(Date.parse(ack.finished_at) >= Date.parse(ack.started_at));
    const document = await graph(owner, value => equivalent(value.viewport, live) && matches(value));
    assert.ok(BigInt(document.revision) >= BigInt(ack.response.row_version));
    assert.deepEqual(ack.request.document_identity.row_ids, report.row_ids);
    assert.equal(ack.request.document_identity.source_key, config.sourceKey);
    assert.deepEqual(document.connections, ack.request.document_identity.connections);
    checkUnselected(document);
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
    const observedAt = new Date().toISOString();
    stage.original_persisted_pre_close = {
        observed_at: observedAt, live, stored: document.viewport,
        graph_revision: document.revision, document_identity: documentIdentity(document),
        native_commit_ack: ack, native_saved_status_visible: true,
    };
    report.viewport_checks.push({ stage: activeStage, step: report.step, phase: "pre_close_persisted_observation", observed_at: observedAt, live, stored: document.viewport });
    return { viewport: document.viewport, document };
}
async function persistedKnownUnaccepted(stage, originalBatch, earlyDocument, rejected) {
    assert.equal(stage.kind, "video");
    assert.ok(rejected.length > 0 && rejected.every(knownUnacceptedVideo));
    assert.ok(rejected.every(value => stage.original_task_ids.includes(value.id)));
    const early = stage.original_persisted_pre_close;
    const rejectedIds = rejected.map(value => value.id);
    const matches = (identity, states) => {
        const batch = states.batches.find(value => value.id === originalBatch.id);
        return isDeepStrictEqual(identity, early.document_identity)
            && equivalent(states.viewport, early.stored)
            && batch && batch.sourceNodeId === originalBatch.sourceNodeId
            && rejectedIds.every(id => {
                const original = originalBatch.items.find(item => item.taskId === id);
                const item = batch.items.find(item => item.id === original.id);
                return item && item.rowId === original.rowId && item.nodeId === original.nodeId
                    && item.taskId === id && item.status === "failed"
                    && item.retryCount === original.retryCount
                    && states.node_tasks.some(node => node.id === original.nodeId
                        && node.task_id === id && node.task_status === "failed" && !node.content_present);
            });
    };
    const earlyMatches = matches(early.native_commit_ack.request.document_identity,
        early.native_commit_ack.request.document_task_states)
        && matches(documentIdentity(earlyDocument), documentTaskStates(earlyDocument));
    stage.known_unaccepted_persistence_wait = {
        started_at: new Date().toISOString(), rejected_task_ids: rejectedIds,
        early_ack_revision: early.native_commit_ack.response.row_version,
        early_graph_revision: earlyDocument.revision, early_ack_and_graph_already_failed: earlyMatches,
    };
    const ack = await eventually(async () => {
        await finishTraces();
        return report.document_commits.find(entry => entry.stage === activeStage && entry.http_status === 200
            && Date.parse(entry.started_at) >= Date.parse(stage.approval_requested_at)
            && stage.submissions.every(submission => Date.parse(entry.started_at) >= Date.parse(submission.response_received_at))
            && matches(entry.request.document_identity, entry.request.document_task_states)
            && (earlyMatches
                ? BigInt(entry.response.row_version) >= BigInt(early.native_commit_ack.response.row_version)
                : BigInt(entry.response.row_version) > BigInt(early.native_commit_ack.response.row_version)));
    }, value => Boolean(value));
    assert.ok(ack.idempotency_key);
    assert.ok(stage.submissions.every(entry => Date.parse(ack.started_at) >= Date.parse(entry.response_received_at)));
    assert.equal(ack.response.canvas_id, config.canvasId);
    assert.equal(ack.response.project_id, config.projectId);
    assert.equal(ack.response.source_key, config.sourceKey);
    assert.match(ack.response.row_version, /^[1-9]\d*$/);
    assert.ok(BigInt(ack.response.row_version) > BigInt(ack.request.expected_row_version));
    assert.ok(Date.parse(ack.finished_at) >= Date.parse(ack.started_at));
    assert.ok(earlyMatches || BigInt(ack.response.row_version) > BigInt(early.native_commit_ack.response.row_version));
    const document = await graph(owner, value => matches(documentIdentity(value), documentTaskStates(value))
        && BigInt(value.revision) >= BigInt(ack.response.row_version)
        && BigInt(value.revision) >= BigInt(earlyDocument.revision));
    assert.deepEqual(documentIdentity(document), early.document_identity);
    assert.deepEqual(document.connections, ack.request.document_identity.connections);
    assert.deepEqual(rows(document).map(row => row.id), report.row_ids);
    checkUnselected(document);
    const live = await liveViewport();
    assert.ok(equivalent(live, early.live) && equivalent(document.viewport, early.stored));
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
    const observedAt = new Date().toISOString();
    stage.known_unaccepted_persisted_pre_close = {
        observed_at: observedAt, rejected_task_ids: rejectedIds, live, stored: document.viewport,
        graph_revision: document.revision, document_identity: documentIdentity(document),
        document_task_states: documentTaskStates(document), native_commit_ack: ack,
        reused_matching_early_ack: ack === early.native_commit_ack, native_saved_status_visible: true,
    };
    stage.known_unaccepted_pre_close_snapshot = snapshot(document, batchFor(document, originalBatch.id));
    report.viewport_checks.push({ stage: activeStage, step: report.step, phase: "known_unaccepted_persisted_observation", observed_at: observedAt, live, stored: document.viewport });
    return document;
}
async function reopen(expectedViewport, capturedCookies) {
    if (!capturedCookies) await finishTraces();
    const cookies = capturedCookies || await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(undefined, cookies);
    await open(owner, expectedViewport);
    await fit();
}
async function openEditor(select = true) {
    await script().getByRole("button", { name: "全屏编辑", exact: true }).click();
    await editor().waitFor({ state: "visible" });
    if (select) {
        const displayed = editor().locator("tbody tr[data-row-key]");
        assert.equal(await displayed.count(), 3);
        for (let index = 0; index < 3; index++) await displayed.nth(index).getByRole("checkbox").setChecked(config.selectedRowIndexes.includes(index));
        assert.equal(await displayed.nth(1).getByRole("checkbox").isChecked(), false);
    }
    return editor();
}
async function closeEditor() {
    await editor().getByRole("button", { name: "Close", exact: true }).click();
    await editor().waitFor({ state: "hidden" });
}
function checkUnselected(document) {
    assert.deepEqual(rows(document).map(row => row.id), report.row_ids);
    for (let index = 0; index < config.rows.length; index++) {
        for (const field of ["plotDescription", "imageGenerationPrompt", "videoMotionPrompt", "durationSeconds"]) {
            assert.equal(rows(document)[index][field], config.rows[index][field], "源Table输入保持不变");
        }
    }
    const row = rows(document).find(row => row.id === report.unselected_row_id);
    assert.ok(row);
    assert.equal(row.imageNodeId, undefined);
    assert.equal(row.videoNodeId, undefined);
    for (const field of ["plotDescription", "imageGenerationPrompt", "videoMotionPrompt", "durationSeconds"]) {
        assert.equal(row[field], config.rows[1][field], "未选择对照镜保持原输入");
    }
    for (const batch of batches(document)) assert.ok(batch.items.every(item => item.rowId !== row.id));
}
async function authorRows() {
    report.step = "原全屏Table新增三镜并填写原字段";
    const original = await graph();
    assert.equal(source(original).type, "script");
    assert.deepEqual(rows(original), []);
    assert.deepEqual(batches(original), []);
    assert.equal((await listed()).length, 0);
    await openEditor(false);
    for (const label of ["时长", "画面描述", "图片提示词", "视频提示词"]) {
        const checkbox = editor().locator(".script-column-picker").getByRole("checkbox", { name: label, exact: true });
        if (!await checkbox.isChecked()) await checkbox.press("Space");
        assert.equal(await checkbox.isChecked(), true);
    }
    for (let index = 0; index < 3; index++) {
        await editor().getByRole("button", { name: "新增镜头", exact: true }).click();
        const row = editor().locator("tbody tr[data-row-key]").nth(index);
        await row.waitFor({ state: "visible" });
        await row.getByRole("spinbutton").fill(String(config.rows[index].durationSeconds));
        await row.getByRole("spinbutton").press("Tab");
        for (const [field, label] of [["plotDescription", "画面描述"], ["imageGenerationPrompt", "图片提示词"], ["videoMotionPrompt", "视频提示词"]]) {
            await row.getByPlaceholder("填写" + label, { exact: true }).fill(config.rows[index][field]);
        }
    }
    const authored = await graph(owner, document => rows(document).length === 3 && rows(document).every((row, index) => {
        return ["plotDescription", "imageGenerationPrompt", "videoMotionPrompt", "durationSeconds"].every(field => row[field] === config.rows[index][field]);
    }));
    report.row_ids = rows(authored).map(row => row.id);
    assert.equal(new Set(report.row_ids).size, 3);
    report.selected_row_ids = config.selectedRowIndexes.map(index => report.row_ids[index]);
    report.unselected_row_id = report.row_ids[1];
    report.source_rows = rows(authored);
    report.source_rows_authored_via_original_table = true;
    await page.screenshot({ path: resolve(output, "source-table-authored.png") });
    assert.deepEqual(sorted(authored.nodes.map(node => node.id)), sorted(original.nodes.map(node => node.id)));
    assert.deepEqual(authored.connections, original.connections);
    await closeEditor();
    await save();
    await page.screenshot({ path: resolve(output, "source-authored.png") });
}
function confirmation(kind) {
    return page.getByRole("dialog").filter({ has: page.getByText("确认提交 2 个" + (kind === "image" ? "图片生成" : "视频生成") + "任务", { exact: true }) });
}
async function verifyMedia(current, stage, phase) {
    stage.media_views ||= [];
    for (const item of stage.outputs) {
        const node = page.locator('[data-node-id="' + item.node_id + '"]');
        if (stage.kind === "image") {
            const image = node.locator("img").first();
            await image.waitFor({ state: "visible" });
            const measured = await image.evaluate(async image => {
                await image.decode();
                const response = await fetch(image.currentSrc);
                if (!response.ok) throw new Error("原图片读取失败：HTTP " + response.status);
                const digest = await crypto.subtle.digest("SHA-256", await response.arrayBuffer());
                return { width: image.naturalWidth, height: image.naturalHeight, sha256: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("") };
            });
            assert.ok(measured.width > 0 && measured.height > 0);
            assert.equal(measured.sha256, item.sha256);
            if (item.width !== undefined) assert.equal(measured.width, item.width);
            if (item.height !== undefined) assert.equal(measured.height, item.height);
            Object.assign(item, { width: measured.width, height: measured.height });
            stage.media_views.push({ phase, node_id: item.node_id, ...measured, decoded: true });
        } else {
            await node.getByRole("button", { name: /^播放 / }).first().click();
            const video = node.locator("video").first();
            await video.waitFor({ state: "visible" });
            await eventually(() => video.evaluate(media => ({ ready: media.readyState >= 2 && media.videoWidth > 0, error: media.error?.message })), value => {
                assert.ok(!value.error, value.error);
                return value.ready;
            });
            const initial = await video.evaluate(media => ({ width: media.videoWidth, height: media.videoHeight, duration: media.duration, current_time: media.currentTime }));
            assert.ok(initial.width > 0 && initial.height > 0 && Number.isFinite(initial.duration));
            const expectedSeconds = config.rows[report.row_ids.indexOf(item.row_id)].durationSeconds;
            assert.ok(Math.abs(initial.duration - expectedSeconds) <= 0.75, "真实视频时长应对应原行秒数");
            const advanced = await eventually(() => video.evaluate(media => ({ current_time: media.currentTime, paused: media.paused, ended: media.ended, error: media.error?.message })), value => {
                assert.ok(!value.error, value.error);
                return value.current_time > initial.current_time + 0.1;
            });
            if (item.width !== undefined) assert.equal(initial.width, item.width);
            if (item.height !== undefined) assert.equal(initial.height, item.height);
            Object.assign(item, { width: initial.width, height: initial.height, duration_ms: Math.round(initial.duration * 1000) });
            stage.media_views.push({ phase, node_id: item.node_id, ...initial, advanced, real_playback: true });
            await video.evaluate(media => media.pause());
        }
        const resource = await current.request.get(origin + resourcesPath + "/" + item.storage_key.slice(9) + "/file");
        assert.ok(resource.ok());
        assert.equal(sha256(await resource.body()), item.sha256);
    }
}
function checkStagesBindings(stage) {
    const observed = report.bindings.filter(entry => stage.task_ids.includes(entry.request.params.taskId));
    stage.bindings = observed;
    for (let index = 0; index < stage.task_ids.length; index++) {
        const entries = observed.filter(entry => entry.request.params.taskId === stage.task_ids[index]);
        assert.ok(entries.length >= 1, "必须捕获原消费器bind请求和回执");
        for (const entry of entries) {
            assert.equal(entry.http_status, 200);
            assert.equal(entry.request.params.canvasId, config.sourceKey);
            assert.equal(entry.request.params.nodeId, stage.node_ids[index]);
            assert.equal(entry.response.task_id, stage.task_ids[index]);
            assert.equal(entry.response.node_id, stage.node_ids[index]);
            assert.equal(entry.response.binding_status, "bound");
            assert.equal(entry.response.storage_key, stage.outputs[index].storage_key);
        }
    }
}
async function providerObservation(taskId, needsError = false) {
    return eventually(async () => {
        const file = resolve(config.providerDirectory, "provider", "video", taskId, "report.json");
        try { return JSON.parse(await readFile(file, "utf8")); }
        catch (error) { if (error.code === "ENOENT") return undefined; throw error; }
    }, value => Boolean(value && (!needsError || value.errors?.length)));
}
async function knownRejectionEvidence(original) {
    assertSubmissionKnown(original);
    assert.ok(knownUnacceptedVideo(original), "只允许服务端明确未受理且许可重试的原视频失败");
    const observation = await providerObservation(original.id, true);
    const wire = observation.requests;
    const expectedStatus = original.errorCode === "provider_rate_limit" ? 429 : 503;
    assert.equal(observation.submissions, 1);
    assert.equal(observation.task_id, original.id);
    assert.equal(observation.role, "video");
    assert.equal(observation.model, config.videoModel);
    assert.equal(wire.length, 1);
    assert.equal(wire[0].method, "POST");
    assert.equal(wire[0].host, "api.agnes-ai.cn");
    assert.equal(wire[0].path, "/v1/videos");
    assert.equal(wire[0].http_status, expectedStatus);
    assert.ok(!wire[0].video_id);
    assert.ok(Number.isFinite(Date.parse(wire[0].finished_at)));
    if (expectedStatus === 503) assert.equal(wire[0].queue_full, true);
    assert.deepEqual(observation.errors, [{
        code: original.errorCode, http_status: expectedStatus, accepted_unknown: false,
    }]);
    for (const field of ["results", "downloads", "inline_outputs"]) assert.deepEqual(observation[field], []);
    return observation;
}
async function cooldownBeforeRetry(stage, lineage) {
    const observed = await Promise.all(allTaskIds(stage).map(async id => ({ id, value: await providerObservation(id) })));
    const posts = observed.flatMap(({ id, value }) => value.requests
        .filter(item => item.method === "POST").map(item => ({ task_id: id, ...item })));
    assert.equal(posts.length, allTaskIds(stage).length, "每个逻辑视频任务必须只产生一个供应商 POST");
    assert.ok(posts.every(item => item.host === "api.agnes-ai.cn" && item.path === "/v1/videos"
        && Number.isFinite(Date.parse(item.finished_at)) && item.finished_at.endsWith("+00:00")));
    const latest = posts.reduce((left, right) => left.finished_at >= right.finished_at ? left : right);
    const started = Date.now();
    const referenceTime = Date.parse(latest.finished_at);
    while (Date.now() - referenceTime < 65001) {
        await new Promise(resolve => setTimeout(resolve, Math.min(1000, 65001 - (Date.now() - referenceTime))));
    }
    const finished = Date.now();
    lineage.cooldown = {
        reference_task_id: latest.task_id, reference_post_finished_at: latest.finished_at,
        wait_started_at: new Date(started).toISOString(), wait_finished_at: new Date(finished).toISOString(),
        actual_wait_ms: finished - started, elapsed_since_reference_ms: finished - referenceTime,
        minimum_elapsed_ms: 65000, observed_post_task_ids: posts.map(item => item.task_id),
    };
    assert.ok(lineage.cooldown.elapsed_since_reference_ms >= 65000);
}
async function recoverFirstAcceptedRetry(stage, lineage, postCount) {
    if (stage.running_cookie_only_recovery) return;
    assert.equal(stage.original_no_active_snapshot_preserved, true);
    assert.equal(stage.retry_lineage.length, 1, "无原 active 时只在首个真实受理 retry 上验关窗恢复");
    const active = await eventually(() => task(lineage.retry_task_id), value => {
        assertSubmissionKnown(value);
        assert.ok(!["failed", "cancelled", "succeeded"].includes(value.status),
            "首个重试未能观察到真实 running，停止：" + JSON.stringify(taskSnapshot(value)));
        return value.status === "running" && Boolean(value.providerRequestId) && !value.resultJson
            && (value.outputs || []).length === 0;
    }, 900000);
    stage.first_accepted_retry_running_task = taskSnapshot(active);
    const viewport = await save();
    await finishTraces();
    const cookies = await owner.cookies();
    const beforeClose = await graph();
    const beforeTasks = await Promise.all(stage.task_ids.map(id => task(id)));
    const confirmed = beforeTasks.find(value => value.id === lineage.retry_task_id);
    assertSubmissionKnown(confirmed);
    assert.ok(confirmed.status === "running" && confirmed.providerRequestId && !confirmed.resultJson
        && (confirmed.outputs || []).length === 0, "关窗前重核 retry 仍真实 running、有供应商 ID 且无成品");
    const batch = batchFor(beforeClose, stage.batch_id);
    const item = batch.items.find(item => item.taskId === confirmed.id);
    assert.ok(item && ["queued", "running"].includes(item.status));
    assert.ok(!beforeClose.nodes.find(node => node.id === lineage.node_id).metadata.content);
    stage.retry_pre_close_observed_at = new Date().toISOString();
    stage.retry_running_snapshot = snapshot(beforeClose, batch);
    stage.retry_running_tasks = beforeTasks.map(taskSnapshot);
    stage.running_recovery_basis = "first_accepted_retry";
    stage.retry_recovery_task_id = confirmed.id;
    stage.retry_context_close_requested_at = new Date().toISOString();
    await reopen(viewport, cookies);
    const restored = await graph(owner, document => Boolean(batchFor(document, stage.batch_id)));
    assert.deepEqual(batchFor(restored, stage.batch_id).items.map(item => item.taskId), stage.task_ids);
    assert.equal(batchFor(restored, stage.batch_id).sourceNodeId, batch.sourceNodeId);
    assert.deepEqual(batchFor(restored, stage.batch_id).items.map(item => [item.id, item.rowId, item.nodeId, item.taskId]),
        batch.items.map(item => [item.id, item.rowId, item.nodeId, item.taskId]));
    assert.deepEqual(rows(restored).map(row => row.id), report.row_ids);
    checkUnselected(restored);
    assert.equal(report.submissions.length, postCount + 2 + stage.retry_lineage.length);
    stage.running_cookie_only_recovery = true;
    stage.retry_running_recovered_snapshot = snapshot(restored, batchFor(restored, stage.batch_id));
    await page.screenshot({ path: resolve(output, "video-retry-running-restored.png") });
}
async function retryOriginalKnownRejections(stage, originalTerminal, postCount) {
    const failed = originalTerminal.filter(value => value.status === "failed");
    if (!failed.length) return originalTerminal;
    assert.equal(stage.kind, "video", "只允许明确未受理的原视频失败项显式重试");
    assert.ok(failed.length <= 2);
    assert.equal(stage.retry_lineage.length, 0);
    assert.ok(failed.every(knownUnacceptedVideo));
    assert.ok(originalTerminal.filter(value => value.status !== "failed").every(value => value.status === "succeeded"));
    for (const original of failed) {
        assert.ok(stage.retry_lineage.every(entry => entry.retry_terminal?.status === "succeeded"),
            "前一唯一 retry 必须 succeeded 并由原 consumer 回填后才点下一镜");
        const ordinal = stage.original_task_ids.indexOf(original.id);
        assert.ok(ordinal >= 0);
        const before = await graph(owner, document => {
            const batch = batchFor(document, stage.batch_id);
            return batch?.status === "partial_failed" && batch.items.every(item => {
                const pendingOriginal = failed.some(value => value.id === item.taskId);
                return pendingOriginal ? item.status === "failed"
                    : item.status === "succeeded" && document.nodes.find(node => node.id === item.nodeId)?.metadata.status === "success";
            });
        }, 180000);
        const batch = batchFor(before, stage.batch_id);
        const failedItem = batch.items[ordinal];
        assert.equal(failedItem.taskId, original.id);
        assert.equal(failedItem.retryCount, 0);
        assert.equal(failedItem.submissionUncertain || false, false);
        const observation = await knownRejectionEvidence(await task(original.id));
        const admission = stage.submissions.find(entry => entry.response.id === original.id);
        assert.ok(admission);
        const lineage = {
            original_task_id: original.id, row_id: failedItem.rowId, node_id: failedItem.nodeId,
            item_id: failedItem.id, original_idempotency_key: admission.idempotency_key,
            original_terminal: taskSnapshot(original), original_batch_snapshot: snapshot(before, batch),
            original_provider_observation: observation, prior_retry_task_ids: stage.retry_lineage.map(entry => entry.retry_task_id),
            prior_retries_succeeded_and_attached_before_retry: true,
            accepted_original_tasks_succeeded_before_retry: true,
            ui: "更多操作 → 查看批次详情 → 重试镜头 N；固定源无额外确认弹窗",
        };
        await cooldownBeforeRetry(stage, lineage);
        stage.retry_lineage.push(lineage);
        report.step = "原batch明确未受理失败镜严格串行且每镜只点击一次原重试控件";
        await fit();
        await script().getByRole("button", { name: "更多操作", exact: true }).click();
        await page.getByRole("menuitem", { name: "查看批次详情", exact: true }).click();
        const details = page.getByRole("dialog", { name: "批次详情", exact: true });
        await details.waitFor({ state: "visible" });
        const shotNumber = rows(before).find(row => row.id === failedItem.rowId).shotNumber;
        const retry = details.getByRole("button", { name: "重试镜头 " + shotNumber, exact: true });
        assert.equal(await details.getByRole("button", { name: /^重试镜头 / }).count(),
            failed.length - stage.retry_lineage.length + 1);
        assert.equal(await retry.isEnabled(), true);
        await knownRejectionEvidence(await task(original.id));
        await page.screenshot({ path: resolve(output, "video-original-retry-item-" + shotNumber + ".png") });
        lineage.clicked_at = new Date().toISOString();
        assert.ok(Date.parse(lineage.clicked_at) - Date.parse(lineage.cooldown.reference_post_finished_at) >= 65000);
        await retry.click();
        lineage.ui_click_count = 1;
        await details.getByRole("button", { name: "Close", exact: true }).click();
        await details.waitFor({ state: "hidden" });
        const retried = await graph(owner, document => {
            const current = batchFor(document, stage.batch_id);
            return current?.items[ordinal]?.taskId && current.items[ordinal].taskId !== original.id;
        }, 180000);
        const currentBatch = batchFor(retried, stage.batch_id);
        stage.task_ids = currentBatch.items.map(item => item.taskId);
        stage.final_task_ids = [...stage.task_ids];
        stage.all_task_ids = [...new Set([...stage.original_task_ids, ...stage.task_ids])];
        lineage.retry_task_id = stage.task_ids[ordinal];
        assert.equal(batches(retried).length, batches(before).length);
        assert.deepEqual(batches(retried).map(value => value.id), batches(before).map(value => value.id));
        assert.equal(currentBatch.sourceNodeId, batch.sourceNodeId);
        assert.deepEqual(currentBatch.items.map(item => [item.id, item.rowId, item.nodeId]), batch.items.map(item => [item.id, item.rowId, item.nodeId]));
        assert.deepEqual(sorted(retried.nodes.map(node => node.id)), sorted(before.nodes.map(node => node.id)));
        assert.deepEqual(retried.connections, before.connections);
        for (let index = 0; index < currentBatch.items.length; index++) {
            assert.equal(currentBatch.items[index].retryCount, stage.retry_lineage.some(entry => entry.row_id === currentBatch.items[index].rowId) ? 1 : 0);
            if (index !== ordinal) {
                assert.equal(currentBatch.items[index].taskId, batch.items[index].taskId);
                const prior = before.nodes.find(node => node.id === stage.node_ids[index]);
                const current = retried.nodes.find(node => node.id === stage.node_ids[index]);
                for (const field of ["storageKey", "content", "assetId", "taskId"]) assert.equal(current.metadata[field], prior.metadata[field]);
            }
        }
        await finishTraces();
        stage.submissions = report.submissions.slice(postCount);
        assert.equal(stage.submissions.length, 2 + stage.retry_lineage.length);
        const retryAdmission = stage.submissions.find(entry => entry.response.id === lineage.retry_task_id);
        assert.ok(retryAdmission);
        assert.equal(retryAdmission.http_status, 202);
        assert.notEqual(retryAdmission.idempotency_key, admission.idempotency_key);
        assert.equal(retryAdmission.request.input.metadata.retryOf, original.id);
        assert.equal(retryAdmission.request.input.metadata.clientOperationId, retryAdmission.idempotency_key);
        const comparable = entry => {
            const value = structuredClone(entry.request);
            for (const field of ["clientOperationId", "retryOf", "attemptGroupId"]) delete value.input.metadata[field];
            return value;
        };
        assert.deepEqual(comparable(retryAdmission), comparable(admission));
        lineage.retry_idempotency_key = retryAdmission.idempotency_key;
        lineage.retry_request = retryAdmission.request;
        const retryTask = await task(lineage.retry_task_id);
        assertSubmissionKnown(retryTask);
        assert.equal(retryTask.retryOf, original.id);
        assert.equal(retryTask.attemptGroupId, original.attemptGroupId || original.id);
        assert.equal(retryTask.clientContext.nodeId, lineage.node_id);
        lineage.retry_admission = taskSnapshot(retryTask);
        checkUnselected(retried);
        await page.screenshot({ path: resolve(output, "video-retry-started-" + shotNumber + ".png") });
        await recoverFirstAcceptedRetry(stage, lineage, postCount);
        const ready = await eventually(() => task(lineage.retry_task_id), value => {
            stage.last_retry_task = taskSnapshot(value);
            assertSubmissionKnown(value);
            assert.ok(!["failed", "cancelled"].includes(value.status),
                "该镜唯一 retry 再次失败，立即停止后续点击：" + JSON.stringify(taskSnapshot(value)));
            return value.status === "succeeded";
        }, 900000);
        const attached = await graph(owner, document => {
            const current = batchFor(document, stage.batch_id).items[ordinal];
            const node = document.nodes.find(node => node.id === lineage.node_id);
            return current.taskId === ready.id && current.status === "succeeded"
                && node?.metadata.status === "success" && node.metadata.taskId === ready.id
                && /^resource:[1-9]\d*$/.test(node.metadata.storageKey || "");
        }, 180000);
        await finishTraces();
        lineage.retry_binding_receipts = report.bindings.filter(entry => entry.request.params.taskId === ready.id
            && entry.http_status === 200 && entry.response.binding_status === "bound");
        assert.ok(lineage.retry_binding_receipts.length >= 1,
            "下一镜点击前必须捕获原 consumer 的成功回填回执");
        assert.ok(knownUnacceptedVideo(await task(original.id)), "原失败任务保持独立且无结果");
        lineage.retry_terminal = taskSnapshot(ready);
        lineage.retry_attached_snapshot = snapshot(attached, batchFor(attached, stage.batch_id));
        lineage.retry_attached_observed_at = new Date().toISOString();
        stage.explicit_unaccepted_retry_once_per_original = true;
    }
    const finalTasks = await Promise.all(stage.task_ids.map(id => task(id)));
    assert.ok(finalTasks.every(value => value.status === "succeeded"));
    return finalTasks;
}

async function runStage(kind) {
    activeStage = kind;
    const mode = kind === "image" ? "storyboard_image" : "storyboard_video";
    const label = kind === "image" ? "生成分镜图" : "生成视频";
    const stage = report.stages[kind] = { kind, row_ids: report.selected_row_ids, submissions: [], bindings: [], outputs: [], retry_lineage: [] };
    report.step = kind + "原Table确认取消无任务、目标或批次";
    const manualStartedAt = new Date().toISOString();
    const preSubmitViewport = await save();
    stage.pre_submit_manual_save = { started_at: manualStartedAt, finished_at: new Date().toISOString(), viewport: preSubmitViewport };
    const baseline = await graph();
    const originalTaskIds = (await listed()).map(value => value.id);
    const postCount = report.submissions.length;
    await openEditor();
    if (kind === "video") {
        await editor().getByText("直接生成", { exact: true }).click();
        assert.equal(source(await graph()).metadata.storyboardVideoInputMode || "direct", "direct");
        stage.video_input_mode = "direct";
    }
    assert.equal(await editor().getByRole("button", { name: label, exact: true }).isEnabled(), true);
    await editor().getByRole("button", { name: label, exact: true }).click();
    await confirmation(kind).waitFor({ state: "visible" });
    assert.equal(report.submissions.length, postCount);
    await confirmation(kind).getByRole("button", { name: /^取\s*消$/, exact: true }).click();
    await confirmation(kind).waitFor({ state: "hidden" });
    const cancelled = await graph();
    assert.deepEqual(sorted(cancelled.nodes.map(node => node.id)), sorted(baseline.nodes.map(node => node.id)));
    assert.deepEqual(cancelled.connections, baseline.connections);
    assert.deepEqual(batches(cancelled), batches(baseline));
    assert.deepEqual(rows(cancelled), rows(baseline));
    assert.deepEqual(sorted((await listed()).map(value => value.id)), sorted(originalTaskIds));
    assert.equal(report.submissions.length, postCount);
    stage.original_table_cancel_no_tasks_targets_batch = true;

    report.step = kind + "原Table批准两镜并自然后台执行";
    await editor().getByRole("button", { name: label, exact: true }).click();
    stage.approval_requested_at = new Date().toISOString();
    await confirmation(kind).getByRole("button", { name: "确认生成", exact: true }).click();
    await confirmation(kind).waitFor({ state: "hidden" });
    await closeEditor();
    const running = await graph(owner, document => {
        const batch = batches(document).find(batch => batch.mode === mode && !batches(baseline).some(previous => previous.id === batch.id));
        return batch?.items.length === 2 && batch.items.every(item => item.taskId);
    }, 180000);
    const batch = batches(running).find(batch => batch.mode === mode && !batches(baseline).some(previous => previous.id === batch.id));
    stage.batch_id = batch.id;
    stage.node_ids = batch.items.map(item => item.nodeId);
    stage.task_ids = batch.items.map(item => item.taskId);
    stage.original_task_ids = [...stage.task_ids];
    stage.all_task_ids = [...stage.task_ids];
    stage.final_task_ids = [...stage.task_ids];
    assert.equal(new Set(stage.node_ids).size, 2);
    assert.equal(new Set(stage.task_ids).size, 2);
    assert.deepEqual(batch.items.map(item => item.rowId), report.selected_row_ids);
    assert.equal(batch.sourceNodeId, config.nodeId);
    checkUnselected(running);
    await finishTraces();
    stage.submissions = report.submissions.slice(postCount);
    assert.equal(stage.submissions.length, 2);
    for (let index = 0; index < stage.task_ids.length; index++) {
        const admission = stage.submissions.find(entry => entry.response.id === stage.task_ids[index]);
        assert.ok(admission);
        assert.equal(admission.http_status, 202);
        assert.equal(admission.request.logicalModelId, kind === "image" ? config.imageModelId : config.videoModelId);
        assert.equal(admission.response.model, kind === "image" ? config.imageModel : config.videoModel);
        assert.equal(admission.request.input.metadata.nodeId, stage.node_ids[index]);
        assert.equal(admission.request.input.metadata.sourceNodeId, stage.node_ids[index]);
        const authored = config.rows[config.selectedRowIndexes[index]];
        assert.equal(admission.request.input.prompt, kind === "image" ? authored.imageGenerationPrompt : authored.videoMotionPrompt);
        for (const field of ["referenceImages", "referenceVideos", "referenceAudios"]) assert.deepEqual(admission.request.input[field] || [], []);
        const parameters = admission.request.input.config;
        assert.equal(parameters.size, kind === "image" ? "1:1" : "16:9");
        if (kind === "image") assert.equal(parameters.count, "1");
        else {
            assert.equal(admission.request.input.metadata.videoEditOperation, "text_to_video");
            assert.equal(parameters.videoSeconds, String(authored.durationSeconds));
            assert.equal(parameters.vquality, "720");
            assert.equal(parameters.videoGenerateAudio, "false");
            assert.equal(parameters.videoWatermark, "false");
        }
    }
    report.step = kind + "记录原任务真实活动状态并按实际状态安排cookies恢复";
    const persisted = await persistedPreClose(stage, batch, preSubmitViewport);
    const viewport = persisted.viewport;
    await finishTraces();
    const cookies = await owner.cookies();
    let beforeClose = persisted.document;
    const taskReads = await Promise.all(stage.task_ids.map(async id => {
        const started_at = new Date().toISOString();
        const value = await task(id);
        return { started_at, finished_at: new Date().toISOString(), value };
    }));
    let beforeTasks = taskReads.map(entry => entry.value);
    stage.original_pre_close_task_reads = taskReads.map(entry => ({ started_at: entry.started_at, finished_at: entry.finished_at, task: taskSnapshot(entry.value) }));
    for (const value of beforeTasks) assertSubmissionKnown(value);
    let observedAt = new Date().toISOString();
    let observedSnapshot = snapshot(beforeClose, batchFor(beforeClose, batch.id));
    stage.original_pre_close_observed_at = observedAt;
    stage.original_pre_close_snapshot = observedSnapshot;
    stage.original_pre_close_tasks = beforeTasks.map(taskSnapshot);
    let activeTasks = beforeTasks.filter(value => ["queued", "running"].includes(value.status)
        && !value.resultJson && (value.outputs || []).length === 0);
    let rejected = beforeTasks.filter(knownUnacceptedVideo);
    assert.equal(activeTasks.length + rejected.length, 2,
        "原任务只能真实 active 或视频明确未受理失败，不能包含 unknown 或已完成项");
    assert.ok(rejected.length <= 2 && (!rejected.length || kind === "video"));
    stage.pre_close_unaccepted_failures = rejected.map(taskSnapshot);
    if (rejected.length) {
        stage.known_unaccepted_persistence_observations = [];
        let observedRejectedIds;
        do {
            observedRejectedIds = rejected.map(value => value.id);
            beforeClose = await persistedKnownUnaccepted(stage, batch, persisted.document, rejected);
            const persistedTaskReads = await Promise.all(stage.original_task_ids.map(async id => {
                const started_at = new Date().toISOString();
                const value = await task(id);
                return { started_at, finished_at: new Date().toISOString(), value };
            }));
            beforeTasks = persistedTaskReads.map(entry => entry.value);
            stage.known_unaccepted_pre_close_task_reads = persistedTaskReads.map(entry => ({ started_at: entry.started_at, finished_at: entry.finished_at, task: taskSnapshot(entry.value) }));
            for (const value of beforeTasks) assertSubmissionKnown(value);
            observedAt = new Date().toISOString();
            observedSnapshot = snapshot(beforeClose, batchFor(beforeClose, batch.id));
            stage.known_unaccepted_pre_close_observed_at = observedAt;
            stage.known_unaccepted_pre_close_tasks = beforeTasks.map(taskSnapshot);
            stage.known_unaccepted_persistence_observations.push({
                wait: stage.known_unaccepted_persistence_wait,
                persisted: stage.known_unaccepted_persisted_pre_close,
                snapshot: observedSnapshot, task_reads: stage.known_unaccepted_pre_close_task_reads,
                tasks_observed_at: observedAt,
            });
            activeTasks = beforeTasks.filter(value => ["queued", "running"].includes(value.status)
                && !value.resultJson && (value.outputs || []).length === 0);
            rejected = beforeTasks.filter(knownUnacceptedVideo);
            assert.equal(activeTasks.length + rejected.length, 2,
                "原任务只能真实 active 或视频明确未受理失败，不能包含 unknown 或已完成项");
            assert.ok(rejected.length <= 2 && (!rejected.length || kind === "video"));
            assert.ok(observedRejectedIds.every(id => rejected.some(value => value.id === id)),
                "已明确拒绝的原任务必须继续保持拒绝状态");
        } while (rejected.some(value => !observedRejectedIds.includes(value.id)));
    }
    assert.ok(stage.node_ids.every(id => !beforeClose.nodes.find(node => node.id === id).metadata.content));
    for (const item of batchFor(beforeClose, batch.id).items) {
        assert.ok(activeTasks.some(value => value.id === item.taskId)
            ? ["queued", "running"].includes(item.status)
            : item.status === "failed" && rejected.some(value => value.id === item.taskId));
    }
    if (activeTasks.length) {
        stage.pre_close_observed_at = observedAt;
        stage.running_snapshot = observedSnapshot;
        stage.running_tasks = beforeTasks.map(taskSnapshot);
        stage.running_recovery_basis = "original";
        stage.running_recovery_task_id = activeTasks[0].id;
        stage.context_close_requested_at = new Date().toISOString();
        await reopen(viewport, cookies);
        const restored = await graph(owner, document => Boolean(batchFor(document, batch.id)));
        assert.deepEqual(batchFor(restored, batch.id).items.map(item => item.taskId), stage.task_ids);
        assert.equal(batchFor(restored, batch.id).sourceNodeId, batch.sourceNodeId);
        assert.deepEqual(batchFor(restored, batch.id).items.map(item => [item.id, item.rowId, item.nodeId, item.taskId]),
            batch.items.map(item => [item.id, item.rowId, item.nodeId, item.taskId]));
        assert.deepEqual(rows(restored).map(row => row.id), report.row_ids);
        checkUnselected(restored);
        assert.equal(report.submissions.length, postCount + 2);
        stage.running_cookie_only_recovery = true;
        stage.running_recovered_snapshot = snapshot(restored, batchFor(restored, batch.id));
        await page.screenshot({ path: resolve(output, kind + "-running-restored.png") });
    } else {
        assert.equal(kind, "video");
        assert.equal(rejected.length, 2);
        stage.pre_close_observed_at = observedAt;
        stage.running_snapshot = observedSnapshot;
        stage.running_tasks = beforeTasks.map(taskSnapshot);
        stage.original_no_active_snapshot_preserved = true;
        stage.original_no_active_observed_at = observedAt;
        stage.original_no_active_snapshot = observedSnapshot;
        stage.original_no_active_tasks = beforeTasks.map(taskSnapshot);
        stage.running_recovery_deferred_until_first_accepted_retry = true;
        await page.screenshot({ path: resolve(output, "video-original-no-active.png") });
    }

    report.step = kind + "真实归档和原消费器绑定";
    const originalTerminal = await eventually(async () => Promise.all(stage.original_task_ids.map(id => task(id))), values => {
        stage.last_tasks = values.map(taskSnapshot);
        const failed = values.filter(value => ["failed", "cancelled"].includes(value.status));
        for (const value of values) assertSubmissionKnown(value);
        assert.ok(failed.length <= 2 && failed.every(value => kind === "video" && knownUnacceptedVideo(value)), "原任务失败不可显式重试：" + JSON.stringify(failed.map(taskSnapshot)));
        return values.every(value => value.status === "succeeded" || knownUnacceptedVideo(value));
    }, 900000);
    stage.original_terminal_tasks = originalTerminal.map(taskSnapshot);
    stage.failed_original_tasks = originalTerminal.filter(value => value.status === "failed").map(taskSnapshot);
    const ready = await retryOriginalKnownRejections(stage, originalTerminal, postCount);
    stage.terminal_tasks = ready.map(taskSnapshot);
    const completed = await graph(owner, document => batchFor(document, batch.id)?.status === "completed" && stage.node_ids.every(id => document.nodes.find(node => node.id === id)?.metadata.status === "success"), 180000);
    stage.final_snapshot = snapshot(completed, batchFor(completed, batch.id));
    assert.deepEqual(batchFor(completed, batch.id).items.map(item => item.taskId), stage.task_ids);
    checkUnselected(completed);
    for (let index = 0; index < stage.node_ids.length; index++) {
        const node = completed.nodes.find(node => node.id === stage.node_ids[index]);
        const storageKey = node.metadata.storageKey;
        assert.match(storageKey, /^resource:[1-9]\d*$/);
        assert.equal(node.metadata.taskId, stage.task_ids[index]);
        const result = JSON.parse(ready[index].resultJson);
        const media = kind === "image" ? result.images[0] : result.video;
        if (kind === "image") assert.equal(result.images.length, 1);
        assert.equal(media.storageKey, storageKey);
        const delivery = ready[index].outputs.find(item => item.outputIndex === 0);
        assert.equal(delivery.mediaType, kind);
        assert.ok(delivery.materializedAssetId);
        const response = await owner.request.get(origin + resourcesPath + "/" + storageKey.slice(9) + "/file");
        assert.ok(response.ok());
        const bytes = await response.body();
        stage.outputs.push({ row_id: stage.row_ids[index], node_id: node.id, task_id: stage.task_ids[index], storage_key: storageKey, asset_id: delivery.materializedAssetId, sha256: sha256(bytes), bytes: bytes.length, mime: response.headers()["content-type"] });
    }
    await finishTraces();
    checkStagesBindings(stage);
    await fit();
    await verifyMedia(owner, stage, "owner_completed");
    await page.screenshot({ path: resolve(output, kind + "-completed.png") });
    report.step = kind + "终态原Table重复点击不新增逻辑任务或批次";
    await openEditor();
    const completedBatches = batches(await graph());
    await editor().getByRole("button", { name: label, exact: true }).click();
    await page.locator(".ant-message").getByText(kind === "image" ? "所选分镜图已生成或正在生成" : "镜头视频已存在", { exact: true }).waitFor({ state: "attached" });
    assert.equal(await confirmation(kind).count(), 0);
    assert.equal(report.submissions.length, postCount + allTaskIds(stage).length);
    assert.deepEqual(batches(await graph()), completedBatches);
    stage.original_table_repeat_no_new_tasks_batch = true;
    await closeEditor();
    report.step = kind + "终态再次仅cookies恢复";
    await reopen(await save());
    const fresh = await graph();
    assert.equal(batchFor(fresh, batch.id).status, "completed");
    assert.deepEqual(batchFor(fresh, batch.id).items.map(item => item.taskId), stage.task_ids);
    assert.equal(report.submissions.length, postCount + allTaskIds(stage).length);
    for (const item of stage.outputs) assert.equal(fresh.nodes.find(node => node.id === item.node_id).metadata.storageKey, item.storage_key);
    checkUnselected(fresh);
    await verifyMedia(owner, stage, "owner_fresh_terminal");
    stage.final_cookie_only_recovery = true;
    await page.screenshot({ path: resolve(output, kind + "-fresh-terminal.png") });
}

try {
    server = await createServer({
        cacheDir: resolve(".runtime/vite-agnes-storyboard-media-v7"), logLevel: "silent",
        server: { host: "127.0.0.1", port: config.browserPort, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } },
    });
    await server.listen();
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined });
    owner = await context(config.username);
    const member = await context(config.memberUsername);
    const outsider = await context(config.outsideUsername);
    await open(owner);
    await fit();
    await authorRows();
    await runStage("image");
    await runStage("video");
    report.step = "成员共享四作品及私人任务隔离";
    const shared = await graph(member);
    assert.equal(source(shared).metadata.generationBatches, undefined);
    assert.deepEqual(rows(shared).map(row => row.id), report.row_ids);
    checkUnselected(shared);
    for (const stage of Object.values(report.stages)) {
        for (const item of stage.outputs) {
            const publicNode = shared.nodes.find(node => node.id === item.node_id);
            assert.equal(publicNode.metadata.storageKey, item.storage_key);
            for (const key of ["prompt", "composerContent", "model", "taskId"]) assert.equal(publicNode.metadata[key], undefined);
            const detailResponse = await owner.request.get(origin + tasksPath + "/" + item.task_id);
            const detail = await read(detailResponse);
            assert.equal(detail.id, item.task_id);
            assert.equal(detail.status, "succeeded");
            assert.equal(detail.resultState, "READY");
            const result = JSON.parse(detail.resultJson);
            const media = stage.kind === "image" ? result.images[0] : result.video;
            assert.equal(media.storageKey, item.storage_key);
            assert.equal(detail.outputs.find(output => output.outputIndex === 0).materializedAssetId, item.asset_id);
            report.private_access.push({ actor: "owner", task_id: item.task_id, status: detailResponse.status(), result_state: detail.resultState, storage_key: media.storageKey, asset_id: item.asset_id });
            const assetResponse = await owner.request.get(origin + assetsPath + "/" + encodeURIComponent(item.asset_id));
            const asset = (await read(assetResponse)).asset;
            assert.equal(asset.id, item.asset_id);
            assert.equal(asset.data.storageKey, item.storage_key);
            report.private_access.push({ actor: "owner", asset_id: item.asset_id, status: assetResponse.status(), storage_key: asset.data.storageKey });
            for (const [actor, current] of [["member", member], ["outside", outsider]]) {
                const response = await current.request.get(origin + tasksPath + "/" + item.task_id);
                report.private_access.push({ actor, task_id: item.task_id, status: response.status() });
                assert.equal(response.status(), 404);
                const deniedAsset = await current.request.get(origin + assetsPath + "/" + encodeURIComponent(item.asset_id));
                report.private_access.push({ actor, asset_id: item.asset_id, status: deniedAsset.status() });
                assert.equal(deniedAsset.status(), 404);
            }
            const denied = await outsider.request.get(origin + resourcesPath + "/" + item.storage_key.slice(9) + "/file");
            report.private_access.push({ actor: "outside", storage_key: item.storage_key, status: denied.status() });
            assert.equal(denied.status(), 404);
        }
    }
    await open(member);
    await fit();
    for (const stage of Object.values(report.stages)) await verifyMedia(member, stage, "member_shared");
    report.member_shared_works_private_batch_hidden = true;
    await page.screenshot({ path: resolve(output, "member-shared.png") });
    await finishTraces();
    const allIds = Object.values(report.stages).flatMap(allTaskIds);
    const retries = Object.values(report.stages).flatMap(stage => stage.retry_lineage);
    assert.ok(retries.length <= 2);
    assert.equal(report.submissions.length, 4 + retries.length);
    assert.equal(new Set(allIds).size, allIds.length);
    assert.equal(allIds.length, report.submissions.length);
    assert.deepEqual(sorted((await listed()).map(value => value.id)), sorted(allIds));
    report.original_task_ids = Object.values(report.stages).flatMap(stage => stage.original_task_ids);
    report.final_task_ids = Object.values(report.stages).flatMap(stage => stage.task_ids);
    report.all_task_ids = allIds;
    report.retry_lineage = retries;
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    report.step = "complete";
} catch (error) {
    report.failure = { type: error.name, message: error.message, stack: error.stack };
    if (owner) {
        await finishTraces().catch(traceError => { report.failure_trace_error = traceError.stack ?? String(traceError); });
        const ids = [...new Set([...Object.values(report.stages).flatMap(allTaskIds), ...report.submissions.map(entry => entry.response?.id).filter(Boolean)])];
        report.failure_all_task_ids = ids;
        const results = await Promise.allSettled(ids.map(id => task(id)));
        report.failure_task_snapshots = results.map((result, index) => result.status === "fulfilled" ? taskSnapshot(result.value) : { id: ids[index], read_error: result.reason?.stack ?? String(result.reason) });
    }
    if (page && !page.isClosed()) {
        try { await page.screenshot({ path: resolve(output, "failure.png") }); }
        catch (captureError) { report.failure_capture_error = captureError.stack ?? String(captureError); }
    }
    process.exitCode = 1;
} finally {
    try { await finishTraces(); }
    catch (traceError) { report.final_trace_error = traceError.stack ?? String(traceError); process.exitCode = 1; }
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server?.close();
    process.stdout.write(JSON.stringify(report) + "\n");
}
