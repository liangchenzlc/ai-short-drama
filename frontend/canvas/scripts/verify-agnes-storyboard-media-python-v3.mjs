import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
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
const output = resolve(config.outputDirectory);
const report = {
    scope: "original storyboard Table with real Agnes image and direct video through formal stack",
    input_revision: "C geometry blocks; distinct from failed A/B commercial-object inputs",
    previous_attempt_verdicts: "A/B failures preserved; B content failed",
    width: config.width, step: "start", page_errors: [], http_errors: [], trace_errors: [],
    submissions: [], bindings: [], resource_posts: 0, stages: {}, viewport_checks: [], private_access: [],
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
const unacceptedRateLimit = value => value.status === "failed"
    && value.stage === "failed" && value.errorCode === "provider_rate_limit"
    && value.canRetry === true && value.canResume === false
    && !value.providerRequestId && !value.resultJson && (value.outputs || []).length === 0;

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
        if (request.method() === "POST" && (route === tasksPath || route === bindPath)) {
            const entry = { stage: activeStage, page: pageNumber, time: Date.now() };
            try {
                const body = request.postDataJSON();
                assertNoCredentials(body);
                entry.request = body;
            } catch (error) {
                report.trace_errors.push(error.stack ?? String(error));
                return;
            }
            if (route === tasksPath) {
                entry.idempotency_key = request.headers()["idempotency-key"];
                report.submissions.push(entry);
            } else report.bindings.push(entry);
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
            const value = await response.json();
            if (route === tasksPath) entry.response = taskSnapshot(value);
            else {
                const receipt = value.result || {};
                entry.response = {
                    op: value.op, op_id: value.opId, replayed: value.replayed, revision: value.revision,
                    canvas_id: receipt.canvasId, node_id: receipt.nodeId, task_id: receipt.taskId,
                    applied: receipt.applied, already_bound: receipt.alreadyBound,
                    binding_status: receipt.bindingStatus, output_index: receipt.outputIndex,
                    storage_key: receipt.storageKey, resource_id: receipt.resourceId,
                };
            }
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
async function retryOriginalRateLimit(stage, originalTerminal, postCount) {
    const failed = originalTerminal.filter(value => value.status === "failed");
    if (!failed.length) return originalTerminal;
    assert.equal(stage.kind, "video", "本专项仅允许一个未受理的视频限流失败项显式重试");
    assert.equal(failed.length, 1);
    assert.equal(stage.retry_lineage.length, 0);
    const original = failed[0];
    assert.ok(unacceptedRateLimit(original), "只允许明确未受理且 canRetry=true 的 provider_rate_limit");
    assert.ok(originalTerminal.filter(value => value.id !== original.id).every(value => value.status === "succeeded"));
    const ordinal = stage.original_task_ids.indexOf(original.id);
    assert.ok(ordinal >= 0);
    const before = await graph(owner, document => {
        const batch = batchFor(document, stage.batch_id);
        return batch?.status === "partial_failed" && batch.items.every(item => item.taskId === original.id
            ? item.status === "failed" : item.status === "succeeded"
            && document.nodes.find(node => node.id === item.nodeId)?.metadata.status === "success");
    }, 180000);
    const batch = batchFor(before, stage.batch_id);
    const failedItem = batch.items[ordinal];
    assert.equal(failedItem.taskId, original.id);
    assert.equal(failedItem.retryCount, 0);
    assert.equal(failedItem.submissionUncertain || false, false);
    assert.ok(unacceptedRateLimit(await task(original.id)), "点击前再次核对原任务的服务端重试许可");
    const observation = await eventually(async () => {
        const file = resolve(config.providerDirectory, "provider", "video", original.id, "report.json");
        try { return JSON.parse(await readFile(file, "utf8")); }
        catch (error) { if (error.code === "ENOENT") return undefined; throw error; }
    }, value => Boolean(value?.errors?.length));
    const wire = observation.requests;
    assert.equal(observation.submissions, 1);
    assert.equal(observation.task_id, original.id);
    assert.equal(observation.role, "video");
    assert.equal(observation.model, config.videoModel);
    assert.equal(wire.length, 1);
    assert.equal(wire[0].method, "POST");
    assert.equal(wire[0].host, "api.agnes-ai.cn");
    assert.equal(wire[0].path, "/v1/videos");
    assert.equal(wire[0].http_status, 429);
    assert.ok(!wire[0].video_id);
    assert.deepEqual(observation.errors, [{ code: "provider_rate_limit", http_status: 429, accepted_unknown: false }]);
    for (const field of ["results", "downloads", "inline_outputs"]) assert.deepEqual(observation[field], []);
    const admission = stage.submissions.find(entry => entry.response.id === original.id);
    assert.ok(admission);
    const lineage = {
        original_task_id: original.id, row_id: failedItem.rowId, node_id: failedItem.nodeId,
        item_id: failedItem.id, original_idempotency_key: admission.idempotency_key,
        original_terminal: taskSnapshot(original), original_batch_snapshot: snapshot(before, batch),
        original_provider_observation: observation,
        accepted_original_tasks_succeeded_before_retry: true,
        ui: "更多操作 → 查看批次详情 → 重试镜头 N；固定源无额外确认弹窗",
    };
    stage.retry_lineage.push(lineage);
    report.step = "原batch单项限流失败只点击一次原重试镜头控件";
    await fit();
    await script().getByRole("button", { name: "更多操作", exact: true }).click();
    await page.getByRole("menuitem", { name: "查看批次详情", exact: true }).click();
    const details = page.getByRole("dialog", { name: "批次详情", exact: true });
    await details.waitFor({ state: "visible" });
    const shotNumber = rows(before).find(row => row.id === failedItem.rowId).shotNumber;
    const retry = details.getByRole("button", { name: "重试镜头 " + shotNumber, exact: true });
    assert.equal(await details.getByRole("button", { name: /^重试镜头 / }).count(), 1);
    assert.equal(await retry.isEnabled(), true);
    await page.screenshot({ path: resolve(output, "video-original-retry-item.png") });
    lineage.clicked_at = new Date().toISOString();
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
        assert.equal(currentBatch.items[index].retryCount, index === ordinal ? 1 : 0);
        if (index !== ordinal) {
            assert.equal(currentBatch.items[index].taskId, stage.original_task_ids[index]);
            const prior = before.nodes.find(node => node.id === stage.node_ids[index]);
            const current = retried.nodes.find(node => node.id === stage.node_ids[index]);
            for (const field of ["storageKey", "content", "assetId", "taskId"]) assert.equal(current.metadata[field], prior.metadata[field]);
        }
    }
    await finishTraces();
    stage.submissions = report.submissions.slice(postCount);
    assert.equal(stage.submissions.length, 3);
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
    assert.equal(retryTask.retryOf, original.id);
    assert.equal(retryTask.attemptGroupId, original.attemptGroupId || original.id);
    assert.equal(retryTask.clientContext.nodeId, lineage.node_id);
    lineage.retry_admission = taskSnapshot(retryTask);
    checkUnselected(retried);
    await page.screenshot({ path: resolve(output, "video-retry-started.png") });
    const ready = await eventually(async () => Promise.all(stage.task_ids.map(id => task(id))), values => {
        stage.last_tasks = values.map(taskSnapshot);
        const invalid = values.find(value => ["failed", "cancelled"].includes(value.status));
        assert.ok(!invalid, "唯一显式重试仍失败，不继续重试：" + JSON.stringify(invalid && taskSnapshot(invalid)));
        return values.every(value => value.status === "succeeded");
    }, 900000);
    assert.ok(unacceptedRateLimit(await task(original.id)), "失败原任务必须保留，不能被重试改写");
    lineage.retry_terminal = taskSnapshot(ready[ordinal]);
    stage.explicit_rate_limit_retry_only_once = true;
    return ready;
}

async function runStage(kind) {
    activeStage = kind;
    const mode = kind === "image" ? "storyboard_image" : "storyboard_video";
    const label = kind === "image" ? "生成分镜图" : "生成视频";
    const stage = report.stages[kind] = { kind, row_ids: report.selected_row_ids, submissions: [], bindings: [], outputs: [], retry_lineage: [] };
    report.step = kind + "原Table确认取消无任务、目标或批次";
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
    report.step = kind + "自然queued/running期间仅cookies关窗恢复";
    const viewport = await save();
    await finishTraces();
    const cookies = await owner.cookies();
    const beforeClose = await graph();
    const beforeTasks = await Promise.all(stage.task_ids.map(id => task(id)));
    stage.pre_close_observed_at = new Date().toISOString();
    stage.running_snapshot = snapshot(beforeClose, batchFor(beforeClose, batch.id));
    stage.running_tasks = beforeTasks.map(taskSnapshot);
    const activeTasks = beforeTasks.filter(value => ["queued", "running"].includes(value.status) && !value.resultJson);
    const rateLimited = beforeTasks.filter(unacceptedRateLimit);
    assert.ok(activeTasks.length >= 1, "关闭前至少一个原任务必须自然活跃且尚无成品");
    assert.equal(activeTasks.length + rateLimited.length, 2, "其他原任务只能是明确未受理的限流失败，不能包含 unknown 或已完成任务");
    assert.ok(rateLimited.length <= 1 && (!rateLimited.length || kind === "video"));
    stage.pre_close_rate_limit_failures = rateLimited.map(taskSnapshot);
    assert.ok(stage.node_ids.every(id => !beforeClose.nodes.find(node => node.id === id).metadata.content));
    for (const item of batchFor(beforeClose, batch.id).items) {
        assert.ok(activeTasks.some(value => value.id === item.taskId)
            ? ["queued", "running"].includes(item.status)
            : item.status === "failed" && rateLimited.some(value => value.id === item.taskId));
    }
    stage.context_close_requested_at = new Date().toISOString();
    await reopen(viewport, cookies);
    const restored = await graph(owner, document => Boolean(batchFor(document, batch.id)));
    assert.deepEqual(batchFor(restored, batch.id).items.map(item => item.taskId), stage.task_ids);
    assert.deepEqual(rows(restored).map(row => row.id), report.row_ids);
    checkUnselected(restored);
    assert.equal(report.submissions.length, postCount + 2);
    stage.running_cookie_only_recovery = true;
    stage.running_recovered_snapshot = snapshot(restored, batchFor(restored, batch.id));
    await page.screenshot({ path: resolve(output, kind + "-running-restored.png") });

    report.step = kind + "真实归档和原消费器绑定";
    const originalTerminal = await eventually(async () => Promise.all(stage.original_task_ids.map(id => task(id))), values => {
        stage.last_tasks = values.map(taskSnapshot);
        const failed = values.filter(value => ["failed", "cancelled"].includes(value.status));
        assert.ok(failed.length <= 1 && failed.every(value => kind === "video" && unacceptedRateLimit(value)), "原任务失败不可显式重试：" + JSON.stringify(failed.map(taskSnapshot)));
        return values.every(value => value.status === "succeeded" || unacceptedRateLimit(value));
    }, 900000);
    stage.original_terminal_tasks = originalTerminal.map(taskSnapshot);
    stage.failed_original_tasks = originalTerminal.filter(value => value.status === "failed").map(taskSnapshot);
    const ready = await retryOriginalRateLimit(stage, originalTerminal, postCount);
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
        cacheDir: resolve(".runtime/vite-agnes-storyboard-media-v3"), logLevel: "silent",
        server: { host: "127.0.0.1", port: config.browserPort, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } },
    });
    await server.listen();
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined });
    owner = await context(config.username);
    const foreign = await context(config.foreignUsername);
    const outsider = await context(config.outsideUsername);
    await open(owner);
    await fit();
    await authorRows();
    await runStage("image");
    await runStage("video");
    report.step = "作品仅本人可读，跨账号访问返回404";
    assert.equal((await foreign.request.get(origin + canvasPath + "/my-document")).status(), 404);
    const shared = (await read(await owner.request.get(origin + canvasPath))).source_document;
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
            for (const [actor, current] of [["foreign", foreign], ["outside", outsider]]) {
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
    for (const current of [foreign, outsider]) {
        assert.equal((await current.request.get(origin + canvasPath + "/my-document")).status(), 404);
        for (const stage of Object.values(report.stages)) {
            for (const item of stage.outputs) {
                assert.equal((await current.request.get(origin + resourcesPath + "/" + item.storage_key.slice(9) + "/file")).status(), 404);
            }
        }
    }
    report.foreign_owner_denied = true;
    await finishTraces();
    const allIds = Object.values(report.stages).flatMap(allTaskIds);
    const retries = Object.values(report.stages).flatMap(stage => stage.retry_lineage);
    assert.ok(retries.length <= 1);
    assert.equal(report.submissions.length, retries.length ? 5 : 4);
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
