import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const resources = "/api/v1/canvas-runtime/resources";
const tasks = "/api/v1/canvas-runtime/tasks";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const output = resolve(config.outputDirectory);
const prompt = "把选中的局部区域改为紫色";
const count = config.count || 1;
const nodeCount = count > 1 ? count + 2 : 2;
const taskReceipts = [];
const report = { step: "start", scenario: config.scenario, page_errors: [], http_errors: [], uploads: 0, submissions: [] };
await mkdir(output, { recursive: true });
const server = await createServer({
    cacheDir: resolve(".runtime/vite-mask-browser"),
    server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } },
    logLevel: "silent",
});
await server.listen();
let browser;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}

async function authorize(context) {
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    context.setDefaultTimeout(20000);
    context.setDefaultNavigationTimeout(60000);
}

async function login(username) {
    const context = await browser.newContext({ viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference" });
    await read(await context.request.post(`${origin}/api/v1/auth/login`, {
        headers: { Origin: origin }, data: { username, password: config.password },
    }));
    await authorize(context);
    return context;
}

async function eventually(readCurrent, accepted) {
    let current;
    for (let attempt = 0; attempt < 300; attempt++) {
        current = await readCurrent();
        if (accepted(current)) return current;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`局部重绘未达到预期：${JSON.stringify(current)}`);
}

async function graph(context, accepted) {
    return eventually(async () => (await read(await context.request.get(`${origin}${path}/my-document`))).source_document, accepted);
}

function observe(nextPage) {
    nextPage.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    nextPage.on("response", response => {
        if (response.status() >= 400) report.http_errors.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
        if (new URL(response.url()).pathname === tasks && response.request().method() === "POST" && response.ok()) {
            taskReceipts.push(response.json().then(task => ({ task, input: response.request().postDataJSON() })));
        }
    });
    nextPage.on("request", request => {
        const pathname = new URL(request.url()).pathname;
        if (pathname === resources && request.method() === "POST") report.uploads++;
        if (pathname === tasks && request.method() === "POST") {
            const body = request.postDataJSON();
            report.submissions.push({ projectId: body.projectId, prompt: body.prompt, model: body.model, logicalModelId: body.logicalModelId,
                metadata: body.input.metadata, mask: body.input.mask, referenceImages: body.input.referenceImages });
        }
    });
}

async function openEditor(source) {
    const node = page.locator(`[data-node-id="${source.id}"]`);
    await node.click();
    await node.hover();
    await page.getByRole("button", { name: "局部重绘", exact: true }).click();
    return node.locator("[data-image-mask-edit-inline]");
}

async function selection(editor) {
    return editor.locator("canvas.hidden").evaluate(canvas => canvas.toDataURL("image/png"));
}

async function draw(editor, from, to = from) {
    const box = await editor.getByLabel("局部重绘蒙版", { exact: true }).boundingBox();
    assert.ok(box);
    await page.mouse.move(box.x + box.width * from[0], box.y + box.height * from[1]);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * to[0], box.y + box.height * to[1], { steps: 10 });
    await page.mouse.up();
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    let owner = await login(config.username);
    const member = await login(config.memberUsername);
    page = await owner.newPage();
    observe(page);
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    report.step = "原上传、蒙版校验与画笔操作";
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({
        name: "mask-source-pattern.png", mimeType: "image/png", buffer: Buffer.from(config.image, "base64"),
    });
    const original = await graph(owner, document => document.nodes.length === 1 && document.nodes[0].metadata?.assetId);
    const source = original.nodes[0];
    report.source_node = source;
    let editor = await openEditor(source);
    assert.equal(await editor.getByRole("slider", { name: "笔刷大小" }).inputValue(), "100");
    await editor.getByRole("button", { name: "AI 修改", exact: true }).click();
    await editor.getByText("请输入修改要求", { exact: true }).waitFor();
    await editor.getByRole("textbox", { name: "局部重绘要求" }).fill(prompt);
    await editor.getByRole("button", { name: "AI 修改", exact: true }).click();
    await editor.getByText("请先涂抹局部区域", { exact: true }).waitFor();
    assert.equal(report.submissions.length, 0);
    assert.equal(report.uploads, 1);
    report.empty_inputs_rejected = true;
    await editor.getByRole("slider", { name: "笔刷大小" }).press("Home");
    assert.equal(await editor.getByRole("slider", { name: "笔刷大小" }).inputValue(), "8");
    const empty = await selection(editor);
    await draw(editor, [0.25, 0.4], [0.5, 0.4]);
    const painted = await selection(editor);
    assert.notEqual(painted, empty);
    await editor.getByRole("button", { name: "擦除", exact: true }).click();
    await draw(editor, [0.25, 0.4]);
    assert.notEqual(await selection(editor), painted);
    await editor.getByRole("button", { name: "重置蒙版", exact: true }).click();
    assert.equal(await selection(editor), empty);
    await editor.getByRole("button", { name: "关闭局部重绘", exact: true }).click();
    await editor.waitFor({ state: "hidden" });
    assert.deepEqual((await graph(owner, document => document.nodes.length === 1)).nodes, original.nodes);
    assert.equal(report.uploads, 1);
    report.erase_reset_cancel_kept_source = true;
    editor = await openEditor(source);
    assert.equal(await editor.getByRole("textbox", { name: "局部重绘要求" }).inputValue(), "");
    assert.equal(await editor.getByRole("slider", { name: "笔刷大小" }).inputValue(), "100");
    await editor.getByRole("slider", { name: "笔刷大小" }).press("Home");
    await editor.getByRole("textbox", { name: "局部重绘要求" }).fill(prompt);
    await draw(editor, [0.4, 0.4], [0.55, 0.55]);
    const selected = await selection(editor);
    await writeFile(resolve(output, "selection.png"), Buffer.from(selected.slice(selected.indexOf(",") + 1), "base64"));
    await page.screenshot({ path: resolve(output, "mask-editor.png") });
    report.step = "原蒙版提交";
    if (config.scenario === "unsupported") {
        await editor.getByRole("button", { name: "AI 修改", exact: true }).click();
        const feedback = page.getByText("当前图片模型不支持局部重绘蒙版，请选择支持蒙版编辑的模型", { exact: true });
        // 固定源全局CSS隐藏Toast；这里证明能力拒绝与零提交，不能称为可见反馈验收。
        await feedback.waitFor({ state: "attached" });
        report.unsupported_feedback_visible = await feedback.isVisible();
        assert.equal(report.submissions.length, 0);
        assert.equal(report.uploads, 1);
        assert.deepEqual((await graph(owner, document => document.nodes.length === 1)).nodes, original.nodes);
        report.unsupported_model_rejected = true;
        await page.screenshot({ path: resolve(output, "unsupported-model.png") });
    } else {
        const admitted = page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST" && response.ok());
        await editor.getByRole("textbox", { name: "局部重绘要求" }).press("Enter");
        const response = await admitted;
        const task = await read(response);
        const receipts = await eventually(() => Promise.all(taskReceipts), rows => rows.length === count);
        assert.equal(report.submissions.length, count);
        report.admitted_tasks = receipts.map(({ task, input }) => ({ id: task.id, nodeId: input.input.metadata.nodeId, maskResourceId: input.input.mask.storageKey.slice(9) }));
        assert.equal(new Set(report.admitted_tasks.map(item => item.id)).size, count);
        for (const submitted of report.submissions) {
            assert.equal(submitted.referenceImages[0].storageKey, source.metadata.storageKey);
            assert.match(submitted.mask.storageKey, /^resource:[0-9]+$/);
            assert.ok(!submitted.mask.dataUrl);
        }
        const submitted = receipts.find(item => item.task.id === task.id).input.input;
        report.task_id = task.id;
        report.target_node_id = submitted.metadata.nodeId;
        report.mask_resource_id = submitted.mask.storageKey.slice(9);
        report.step = "真实Worker待响应时的私人任务与原图";
        const running = await eventually(async () => read(await owner.request.get(`${origin}${tasks}/${task.id}`)), row => row.status === "running");
        assert.equal(running.resultState === "READY", false);
        const pending = await graph(owner, document => document.nodes.length === nodeCount && report.admitted_tasks.every(item => document.nodes.some(node => node.id === item.nodeId && node.metadata?.taskId === item.id)));
        assert.deepEqual(pending.nodes.find(node => node.id === source.id), source);
        for (const item of report.admitted_tasks) {
            assert.ok(!pending.nodes.find(node => node.id === item.nodeId).metadata.content);
            assert.equal((await member.request.get(`${origin}${tasks}/${item.id}`)).status(), 404);
            assert.equal((await member.request.get(`${origin}${resources}/${item.maskResourceId}/file`)).status(), 404);
        }
        if (count > 1) {
            const root = pending.nodes.find(node => node.metadata?.isBatchRoot);
            assert.ok(root);
            assert.deepEqual(new Set(root.metadata.batchChildIds), new Set(report.admitted_tasks.map(item => item.nodeId)));
            report.batch_root_id = root.id;
        }
        const mask = await owner.request.get(`${origin}${resources}/${report.mask_resource_id}/file`);
        assert.ok(mask.ok());
        const maskBytes = await mask.body();
        await writeFile(resolve(output, "persisted-mask.png"), maskBytes);
        report.mask_sha256 = createHash("sha256").update(maskBytes).digest("hex");
        if (config.scenario === "recovery") {
            report.step = "关闭旧窗口后仅cookies恢复同一待执行任务";
            const cookies = (await owner.storageState()).cookies;
            await owner.close();
            owner = await browser.newContext({ storageState: { cookies, origins: [] }, viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference" });
            await authorize(owner);
            page = await owner.newPage();
            observe(page);
            await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
            await page.locator(`[data-node-id="${report.target_node_id}"]`).waitFor();
            const restored = await graph(owner, document => document.nodes.some(node => node.id === report.target_node_id && node.metadata?.taskId === task.id));
            assert.ok(!restored.nodes.find(node => node.id === report.target_node_id).metadata.content);
            assert.equal((await read(await owner.request.get(`${origin}${tasks}/${task.id}`))).status, "running");
            assert.equal(report.submissions.length, 1);
            report.pending_fresh_context_restored = true;
        }
        await writeFile(config.imageReleaseFile, "browser-ready");
        report.step = "终态直接回填与永久图片";
        const final = await graph(owner, document => document.nodes.length === nodeCount && report.admitted_tasks.every(item => document.nodes.some(node => node.id === item.nodeId && node.metadata?.assetId && node.metadata?.storageKey)) && (count === 1 || document.nodes.some(node => node.id === report.batch_root_id && node.metadata?.primaryImageId && node.metadata?.status === "success")));
        assert.deepEqual(final.nodes.find(node => node.id === source.id), source);
        const target = final.nodes.find(node => node.id === report.target_node_id);
        report.outputs = [];
        for (const item of report.admitted_tasks) {
            const completed = await read(await owner.request.get(`${origin}${tasks}/${item.id}`));
            assert.equal(completed.status, "succeeded");
            assert.equal(completed.resultState, "READY");
            const result = JSON.parse(completed.resultJson).images[0];
            const child = final.nodes.find(node => node.id === item.nodeId);
            assert.equal(child.metadata.storageKey, result.storageKey);
            const shared = await owner.request.get(`${origin}${resources}/${result.storageKey.slice(9)}/file`);
            assert.equal((await member.request.get(`${origin}${resources}/${result.storageKey.slice(9)}/file`)).status(), 404);
            assert.ok(shared.ok());
            report.outputs.push({ taskId: item.id, nodeId: item.nodeId, storageKey: result.storageKey, sha256: createHash("sha256").update(await shared.body()).digest("hex") });
            assert.equal((await member.request.get(`${origin}${resources}/${item.maskResourceId}/file`)).status(), 404);
        }
        const edges = new Set(final.connections.map(edge => `${edge.fromNodeId}->${edge.toNodeId}`));
        assert.equal(final.connections.length, count > 1 ? count + 1 : 1);
        if (count > 1) {
            const root = final.nodes.find(node => node.id === report.batch_root_id);
            const primary = final.nodes.find(node => node.id === root.metadata.primaryImageId);
            assert.ok(primary);
            assert.equal(root.metadata.storageKey, primary.metadata.storageKey);
            assert.equal(root.metadata.assetId, primary.metadata.assetId);
            assert.equal(root.metadata.batchFailedCount, 0);
            assert.deepEqual(edges, new Set([`${source.id}->${root.id}`, ...report.admitted_tasks.map(item => `${root.id}->${item.nodeId}`)]));
            report.batch_representative_matches_child = true;
        } else assert.deepEqual(edges, new Set([`${source.id}->${target.id}`]));
        const firstOutput = report.outputs.find(item => item.taskId === task.id);
        report.output_storage_key = firstOutput.storageKey;
        report.final_nodes = final.nodes;
        report.output_sha256 = firstOutput.sha256;
        const cookies = (await owner.storageState()).cookies;
        await owner.close();
        owner = await browser.newContext({ storageState: { cookies, origins: [] }, viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference" });
        await authorize(owner);
        page = await owner.newPage();
        observe(page);
        await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
        await page.locator(`[data-node-id="${source.id}"]`).waitFor();
        await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
        await page.evaluate(() => document.fonts.ready);
        await page.waitForFunction(async () => {
            const viewport = document.querySelector("[data-canvas-viewport]");
            if (!viewport) return false;
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
        for (const item of report.outputs) {
            await page.locator(`[data-node-id="${item.nodeId}"]`).waitFor();
            await page.waitForFunction(id => [...document.querySelectorAll(`[data-node-id="${id}"] img`)].some(image => image.complete && image.naturalWidth === 37 && image.naturalHeight === 19), item.nodeId);
        }
        await page.mouse.move(5, 450);
        for (const item of [source, ...report.outputs.map(item => ({ id: item.nodeId }))]) {
            const bounds = await page.locator(`[data-node-id="${item.id}"]`).boundingBox();
            assert.ok(bounds && bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= 1440 && bounds.y + bounds.height <= 900);
        }
        await page.waitForFunction(ids => ids.every(id => !document.querySelector(`[data-node-id="${id}"] .animate-spin`)), report.outputs.map(item => item.nodeId));
        await page.screenshot({ path: resolve(output, "fresh-restored.png") });
        assert.equal(report.submissions.length, count);
        assert.equal(report.uploads, count + 1);
        report.final_fresh_context_restored = true;
    }
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    report.step = "complete";
} catch (error) {
    report.failure = error.stack ?? error.message;
    if (page) {
        report.body = await page.locator("body").innerText().catch(() => "");
        await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    }
    process.exitCode = 1;
} finally {
    await browser?.close();
    await server.close();
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    process.stdout.write(JSON.stringify(report) + "\n");
}
