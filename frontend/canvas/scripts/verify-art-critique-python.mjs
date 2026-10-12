import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { preview } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const runs = "/api/v1/canvas-runtime/creation-runs";
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = { step: "start", protocol: config.protocol, page_errors: [], failed_routes: [], execute_receipts: [], direct_task_posts: 0 };
const responseReads = [];
const server = await preview({
    root: resolve(".."), configFile: resolve("../vite.config.ts"), logLevel: "silent",
    preview: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } },
});
let browser;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
function observe(target) {
    target.on("pageerror", error => report.page_errors.push(error.message));
    target.on("request", request => {
        if (request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/canvas-runtime/tasks") report.direct_task_posts++;
    });
    target.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (route.startsWith("/api/") && response.status() >= 400) report.failed_routes.push({ route, status: response.status() });
        if (response.request().method() === "POST" && route.startsWith(runs) && route.endsWith("/execute")) {
            responseReads.push((async () => {
                const task = await read(response);
                report.execute_receipts.push({ id: task.id, status: response.status() });
            })());
        }
    });
}
async function login(username, cookies) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    context.setDefaultTimeout(25000);
    context.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return context;
}
async function poll(context, route, predicate) {
    let value;
    for (let i = 0; i < 400; i++) {
        value = await read(await context.request.get(`${origin}${route}`));
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`状态未达到预期 ${route}: ${JSON.stringify(value)}`);
}
async function graph(context, predicate) {
    return (await poll(context, `${path}/my-document`, value => predicate(value.source_document))).source_document;
}
async function openCanvas(target) {
    await target.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await target.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await target.evaluate(() => document.fonts.ready);
}
async function save(target) {
    const live = await target.locator("[data-canvas-viewport]").evaluate(viewport => ({
        x: parseFloat(viewport.style.getPropertyValue("--canvas-live-x")),
        y: parseFloat(viewport.style.getPropertyValue("--canvas-live-y")),
        k: parseFloat(viewport.style.getPropertyValue("--canvas-live-scale")),
    }));
    await graph(target.context(), document => document.viewport.x === live.x && document.viewport.y === live.y && document.viewport.k === live.k);
    await target.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await target.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
    await target.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}
async function stableGeometry(target, ids) {
    await target.waitForFunction(ids => {
        const values = ids.map(id => {
            const element = document.querySelector(`[data-node-id="${id}"]`);
            if (!element) return "missing";
            const box = element.getBoundingClientRect();
            return [box.x, box.y, box.width, box.height].map(value => Math.round(value * 100)).join(":");
        }).join("|");
        const previous = window.__artGeometry;
        window.__artGeometry = { values, frames: previous?.values === values ? previous.frames + 1 : 0 };
        return !values.includes("missing") && window.__artGeometry.frames >= 4;
    }, ids);
}
const artState = document => document.nodes.find(node => node.id === report.target_node_id)?.metadata.artCritique;

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await login(config.username);
    const member = await login(config.memberUsername);
    const outsider = await login(config.outsiderUsername);
    page = await owner.newPage();
    observe(page);
    await openCanvas(page);
    report.step = "原上传、添加菜单和拖动连线创建审美输入";
    const original = Buffer.from(config.image, "base64");
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: "art-source.png", mimeType: "image/png", buffer: original });
    const uploaded = await graph(owner, document => document.nodes.length === 1 && document.nodes[0].metadata.storageKey?.startsWith("resource:"));
    const source = uploaded.nodes[0];
    report.source_node_id = source.id;
    report.resource_id = source.metadata.storageKey.slice("resource:".length);
    report.image_sha256 = createHash("sha256").update(original).digest("hex");
    await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    const sourceNode = page.locator(`[data-node-id="${source.id}"]`);
    await page.getByRole("button", { name: "添加节点", exact: true }).click();
    await page.getByRole("button", { name: "AI 审美批改", exact: true }).click();
    const created = await graph(owner, document => document.nodes.length === 2);
    const target = created.nodes.find(node => node.id !== source.id);
    assert.equal(target.type, "ai-art-critique");
    report.target_node_id = target.id;
    const targetNode = page.locator(`[data-node-id="${target.id}"]`);
    const handle = targetNode.locator(".canvas-node-shell").getByText("AI 审美批改", { exact: true });
    await stableGeometry(page, [source.id, target.id]);
    await handle.hover();
    const box = await handle.boundingBox();
    assert.ok(box && box.width > 0 && box.height > 0);
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 560, box.y + box.height / 2 + 100, { steps: 12 });
    await page.mouse.up();
    await graph(owner, document => document.nodes.find(node => node.id === target.id).position.x > target.position.x + 300);
    await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await stableGeometry(page, [source.id, target.id]);
    await sourceNode.click();
    await targetNode.hover();
    const out = await sourceNode.locator('[data-canvas-connection-rail="right"]').boundingBox();
    const into = await targetNode.locator('[data-canvas-connection-rail="left"]').boundingBox();
    assert.ok(out && into);
    await page.mouse.move(out.x + out.width / 2, out.y + out.height / 2);
    await page.mouse.down();
    await page.mouse.move(into.x + into.width / 2, into.y + into.height / 2, { steps: 12 });
    await page.mouse.up();
    const connected = await graph(owner, document => document.connections.length === 1);
    assert.deepEqual(connected.connections.map(edge => [edge.fromNodeId, edge.toNodeId]), [[source.id, target.id]]);
    await save(page);
    const saved = await graph(owner, document => document.nodes.find(node => node.id === source.id).metadata.assetId);
    report.original_source = saved.nodes.find(node => node.id === source.id);
    await page.locator(`[data-node-id="${target.id}"]`).getByRole("button", { name: "开始 AI 批改", exact: true }).click();
    const modal = page.locator(".art-critique-modal");
    await modal.getByRole("heading", { name: "AI 审美批改", exact: true }).waitFor();
    await modal.getByRole("button", { name: "开始批改", exact: true }).click();
    const accepted = await graph(owner, document => artState(document)?.executionRunId && Object.keys(artState(document).stageTaskIds || {}).length === 5);
    report.run_id = artState(accepted).executionRunId;
    report.initial_task_ids = artState(accepted).stageTaskIds;
    assert.equal(artState(accepted).status, "running");
    assert.ok(!artState(accepted).report);
    const first = await read(await owner.request.get(`${origin}${runs}/${report.run_id}`));
    assert.equal(first.run.executionEpoch, "1");
    assert.equal(first.submissions.length, 5);
    assert.ok(first.submissions.every(item => item.approvedAt && item.taskId));
    await page.screenshot({ path: resolve(output, "accepted.png") });

    report.step = "原取消观察后关闭窗口并仅cookies恢复";
    await modal.getByRole("button", { name: "取消分析", exact: true }).click();
    await modal.getByRole("button", { name: "继续上次分析", exact: true }).waitFor();
    await modal.getByRole("button", { name: "关闭批改报告", exact: true }).click();
    await save(page);
    await graph(owner, document => artState(document)?.status === "failed");
    await poll(owner, `${runs}/${report.run_id}`, value => value.run.executionOwner === "");
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    const fresh = await login(undefined, cookies);
    page = await fresh.newPage();
    observe(page);
    await openCanvas(page);
    const restored = await graph(fresh, document => artState(document)?.executionRunId === report.run_id);
    assert.deepEqual(artState(restored).stageTaskIds, report.initial_task_ids);
    assert.deepEqual(restored.nodes.find(node => node.id === source.id), report.original_source);
    await page.locator(`[data-node-id="${target.id}"]`).getByRole("button", { name: "开始 AI 批改", exact: true }).click();
    await page.locator(".art-critique-modal").getByRole("button", { name: "继续上次分析", exact: true }).click();
    await poll(fresh, `${runs}/${report.run_id}`, value => value.run.executionEpoch === "2");
    await graph(fresh, document => artState(document)?.status === "running" && Object.keys(artState(document).stageTaskIds || {}).length === 5);
    await poll(fresh, `${runs}/${report.run_id}`, value => value.submissions.length === 5 && value.submissions.every(item => Object.values(report.initial_task_ids).includes(item.taskId)));
    for (let i = 0; i < 250 && report.execute_receipts.length < 10; i++) await new Promise(resolve => setTimeout(resolve, 100));
    await Promise.all(responseReads);
    assert.equal(report.execute_receipts.length, 10);
    for (const id of Object.values(report.initial_task_ids)) assert.deepEqual(report.execute_receipts.filter(item => item.id === id).map(item => item.status).sort(), [200, 202]);
    await writeFile(resolve(config.releasePath), "execute accepted art stages\n");

    report.step = "九阶段终态报告和原图片保持";
    const completed = await graph(fresh, document => artState(document)?.status === "completed");
    const state = artState(completed);
    report.all_task_ids = state.stageTaskIds;
    assert.equal(Object.keys(state.stageTaskIds).length, 9);
    assert.equal(state.executionRunId, report.run_id);
    assert.equal(state.report.issues.length, 1);
    assert.equal(state.report.issues[0].id, "issue-one");
    assert.equal(state.report.issues[0].editPrompt, "降低右侧区域对比，保留中心主体。");
    assert.equal(state.report.issues[0].target.type, "box");
    assert.equal(state.report.issues[0].verification.verdict, "confirmed");
    assert.ok(!state.report.pipelineWarnings?.length);
    assert.deepEqual(completed.nodes.find(node => node.id === source.id), report.original_source);
    await page.locator(".art-critique-modal").getByText("分析完成", { exact: true }).waitFor();
    await page.screenshot({ path: resolve(output, "completed.png") });
    await page.locator(".art-critique-modal").getByRole("button", { name: "关闭批改报告", exact: true }).click();
    await save(page);
    const completedCookies = await fresh.cookies();
    await fresh.close();
    page = undefined;

    report.step = "终态fresh恢复和本人回执隔离";
    const final = await login(undefined, completedCookies);
    page = await final.newPage();
    observe(page);
    await openCanvas(page);
    await page.locator(`[data-node-id="${target.id}"]`).getByRole("button", { name: "查看批改报告", exact: true }).click();
    await page.locator(".art-critique-modal").getByText("建议调整右侧视觉重量，保留原图主体。", { exact: true }).waitFor();
    const restoredModal = page.locator(".art-critique-modal");
    await restoredModal.getByText("右侧视觉重量偏高", { exact: true }).click();
    await restoredModal.getByRole("heading", { name: "右侧视觉重量偏高", exact: true }).waitFor();
    const annotation = restoredModal.getByRole("img", { name: "AI 批改标注", exact: true });
    await annotation.waitFor({ state: "visible" });
    const bounds = await annotation.locator("g > rect").first().evaluate(rect => ["x", "y", "width", "height"].map(key => Number(rect.getAttribute(key))));
    assert.ok(bounds.every((value, index) => Math.abs(value - [60, 20, 30, 60][index]) < 1e-8), JSON.stringify(bounds));
    await final.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
    await restoredModal.getByRole("button", { name: "复制 AI 生成的修改提示词", exact: true }).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), state.report.issues[0].editPrompt);
    await page.screenshot({ path: resolve(output, "restored-detail.png") });
    report.restored_annotation = bounds;
    report.copied_edit_prompt = state.report.issues[0].editPrompt;
    await restoredModal.getByRole("button", { name: "问题列表", exact: true }).click();
    await restoredModal.getByText("建议调整右侧视觉重量，保留原图主体。", { exact: true }).waitFor();
    await page.screenshot({ path: resolve(output, "restored-report.png") });
    const privateGraph = await graph(final, document => artState(document)?.status === "completed");
    assert.deepEqual(artState(privateGraph), state);
    assert.deepEqual(privateGraph.nodes.find(node => node.id === source.id), report.original_source);
    const shared = await read(await member.request.get(`${origin}${path}/my-document`));
    assert.ok(!shared.source_document.nodes.find(node => node.id === target.id).metadata.artCritique);
    assert.deepEqual((await read(await member.request.get(`${origin}${runs}`))).runs, []);
    for (const context of [member, outsider]) {
        assert.equal((await context.request.get(`${origin}${runs}/${report.run_id}`)).status(), 404);
        for (const id of Object.values(state.stageTaskIds)) assert.equal((await context.request.get(`${origin}/api/v1/canvas-runtime/tasks/${id}`)).status(), 404);
    }
    assert.deepEqual(await (await final.request.get(`${origin}/api/v1/canvas-runtime/resources/${report.resource_id}/file`)).body(), original);
    assert.equal((await outsider.request.get(`${origin}/api/v1/canvas-runtime/resources/${report.resource_id}/file`)).status(), 404);
    await Promise.all(responseReads);
    assert.equal(report.execute_receipts.length, 14);
    assert.equal(report.direct_task_posts, 0);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.failed_routes, []);
    report.step = "complete";
} catch (error) {
    report.failure_stack = error.stack;
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    if (page) await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await new Promise(resolve => server.httpServer.close(resolve));
}
process.stdout.write(JSON.stringify(report));
