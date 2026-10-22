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
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const resources = "/api/v1/canvas-runtime/resources";
const tasks = "/api/v1/canvas-runtime/tasks";
const output = resolve(config.outputDirectory);
const tryOnPrompt = "参考图1是人物原图，参考图2是目标服装。保持人物身份、五官、姿态和背景不变，将人物服装替换为参考图2中的款式。准确还原服装版型、颜色、材质、纹理和装饰细节，穿着关系自然，光影与原图一致。";
const creativePrompt = "基于参考图创作一张新的商业图片，保留主体身份和关键产品细节，画面构图完整，光影自然。";
const report = { step: "start", scenario: config.scenario, width: config.width, page_errors: [], http_errors: [], uploads: 0, submissions: [] };
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(".runtime/vite-batch-table-browser"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
let owner;
let tableId;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function eventually(readValue, predicate) {
    let value;
    for (let attempt = 0; attempt < 400; attempt++) {
        value = await readValue();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`批量表状态未达到预期：${JSON.stringify(value)}`);
}
async function graph(context = owner, predicate = () => true) {
    return eventually(async () => (await read(await context.request.get(`${origin}${path}/my-document`))).source_document, predicate);
}
async function context(username, cookies) {
    const result = await browser.newContext({ viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference", ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    result.setDefaultTimeout(20000);
    result.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await result.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await result.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await result.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return result;
}
async function open(context) {
    page = await context.newPage();
    page.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    page.on("request", request => {
        const route = new URL(request.url()).pathname;
        if (request.method() === "POST" && route === resources) report.uploads++;
        if (request.method() === "POST" && route === tasks) report.submissions.push(request.postDataJSON());
    });
    page.on("response", response => {
        if (response.status() >= 400) report.http_errors.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
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
    await page.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "detached" });
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await page.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
}
async function fresh() {
    await save();
    const cookies = await owner.cookies();
    await owner.close();
    owner = await context(undefined, cookies);
    await open(owner);
    await table().waitFor({ state: "visible" });
    await fit();
}
const table = () => page.locator(`[data-node-id="${tableId}"] [data-canvas-batch-table]`);
const tableNode = document => document.nodes.find(node => node.id === tableId);
const generate = () => table().getByRole("button", { name: /^生成未完成项/ });
const rowPrompt = index => table().getByRole("textbox", { name: `任务 ${index + 1} 提示词`, exact: true });
const promptValue = index => rowPrompt(index).evaluate(element => element instanceof HTMLTextAreaElement ? element.value : element.innerText);
const confirmation = count => page.getByRole("dialog").filter({ has: page.getByText(`确认提交 ${count} 个批量图片任务`, { exact: true }) });
async function chooseImage(rowId, index) {
    const chooser = page.waitForEvent("filechooser");
    await table().locator(`[data-batch-reference-cell][data-row-id="${rowId}"][data-column-index="${index}"]`).click();
    await (await chooser).setFiles({ name: `batch-reference-${index + 1}.png`, mimeType: "image/png", buffer: Buffer.from(config.images[index], "base64") });
    await graph(owner, document => Boolean(tableNode(document)?.metadata.batchTable.rows[0].inputNodeIds[index]) && document.nodes.filter(node => node.type === "image" && node.metadata?.assetId).length === index + 1);
}
async function imagesReady() {
    await eventually(() => table().getByRole("img", { name: "生成结果", exact: true }).evaluateAll(elements => elements.length === 2 && elements.every(element => element.complete && element.naturalWidth > 0)), Boolean);
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    owner = await context(config.username);
    await open(owner);
    report.step = "原右键搜索创建批量表";
    await page.locator("[data-canvas-viewport]").click({ button: "right", position: { x: 25, y: 150 } });
    await page.locator("[data-canvas-context-menu]").getByRole("button", { name: "添加节点", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "批量创作表", exact: true }).count(), 0);
    await page.getByRole("button", { name: "搜索节点", exact: true }).click();
    await page.getByRole("textbox", { name: "搜索节点", exact: true }).fill("批量创作表");
    await page.getByRole("button", { name: "批量创作表", exact: true }).click();
    const defaults = await graph(owner, document => document.nodes.length === 1 && document.nodes[0].type === "batch-table");
    tableId = defaults.nodes[0].id;
    report.table_node_id = tableId;
    assert.equal(defaults.nodes[0].width, 1280);
    assert.equal(defaults.nodes[0].height, 560);
    assert.deepEqual(defaults.nodes[0].metadata.batchTable, { operation: "try_on", concurrency: 10, referenceColumns: [{ id: "reference-1", label: "参考图 1" }, { id: "reference-2", label: "参考图 2" }, { id: "reference-3", label: "参考图 3" }], rows: [] });
    await fit();
    await table().getByText("还没有批量任务", { exact: true }).waitFor({ state: "visible" });
    assert.ok(await generate().isDisabled());
    report.original_menu_created = true;
    await page.screenshot({ path: resolve(output, "batch-table-defaults.png") });
    report.step = "并发选项与参考列上下限";
    const concurrency = table().getByRole("group", { name: "并发数", exact: true });
    assert.deepEqual(await concurrency.getByRole("button").allTextContents(), ["1", "5", "10"]);
    assert.equal(await concurrency.getByRole("button", { name: "10", exact: true }).getAttribute("aria-pressed"), "true");
    await concurrency.getByRole("button", { name: "5", exact: true }).click();
    await graph(owner, document => tableNode(document).metadata.batchTable.concurrency === 5);
    await concurrency.getByRole("button", { name: "1", exact: true }).click();
    for (const count of [4, 5, 6]) await table().getByRole("button", { name: `新增参考图 ${count}`, exact: true }).click();
    assert.ok(await table().getByRole("button", { name: "新增参考图 7", exact: true }).isDisabled());
    for (let count = 6; count > 1; count--) await table().getByRole("button", { name: "减少一组参考图", exact: true }).click();
    assert.ok(await table().getByRole("button", { name: "减少一组参考图", exact: true }).isDisabled());
    await table().getByRole("button", { name: "新增参考图 2", exact: true }).click();
    await graph(owner, document => tableNode(document).metadata.batchTable.referenceColumns.length === 2 && tableNode(document).metadata.batchTable.concurrency === 1);
    report.defaults_and_column_limits = true;
    report.step = "原文件选择上传两张参考图并继承新行";
    await table().getByRole("button", { name: "添加第一条任务", exact: true }).click();
    assert.equal(await promptValue(0), tryOnPrompt);
    assert.ok(await generate().isDisabled());
    const firstRow = (await graph(owner, document => tableNode(document).metadata.batchTable.rows.length === 1)).nodes.find(node => node.id === tableId).metadata.batchTable.rows[0];
    await chooseImage(firstRow.id, 0);
    assert.ok(await generate().isDisabled());
    await chooseImage(firstRow.id, 1);
    await rowPrompt(0).fill(config.rowPrompts[0]);
    await table().getByRole("group", { name: "批量任务类型", exact: true }).getByRole("button", { name: "创意生图", exact: true }).click();
    await table().getByRole("button", { name: "添加任务", exact: true }).click();
    const inherited = await graph(owner, document => tableNode(document).metadata.batchTable.rows.length === 2);
    const rows = tableNode(inherited).metadata.batchTable.rows;
    assert.deepEqual(rows[1].inputNodeIds, rows[0].inputNodeIds);
    assert.equal(rows[1].prompt, creativePrompt);
    assert.equal(await promptValue(1), creativePrompt);
    assert.equal(inherited.connections.length, 0);
    assert.equal(report.uploads, 2);
    report.original_filechoosers_used = true;
    report.inherited_references_and_operation_prompt = true;
    await rowPrompt(1).fill(config.rowPrompts[1]);
    await table().getByRole("group", { name: "批量任务类型", exact: true }).getByRole("button", { name: "批量换装", exact: true }).click();
    await table().getByRole("textbox", { name: "全局提示词", exact: true }).fill(config.globalPrompt);
    await table().getByRole("switch", { name: "启用任务 2", exact: true }).click();
    await eventually(() => generate().innerText(), value => /· 1$/.test(value));
    await table().getByRole("switch", { name: "启用任务 2", exact: true }).click();
    await eventually(() => generate().innerText(), value => /· 2$/.test(value));
    report.disabled_row_excluded = true;
    const idle = await graph(owner, document => tableNode(document).metadata.batchTable.rows.every((row, index) => row.enabled && row.prompt === config.rowPrompts[index]) && tableNode(document).metadata.batchTable.operation === "try_on" && (tableNode(document).metadata.batchTable.globalPrompt || "") === config.globalPrompt);
    report.source_nodes = rows[0].inputNodeIds.map(id => idle.nodes.find(node => node.id === id));
    const idleTable = tableNode(idle).metadata.batchTable;
    report.step = "仅 cookies 恢复本人表参数与原取消确认";
    await fresh();
    assert.deepEqual(tableNode(await graph()).metadata.batchTable, idleTable);
    for (let index = 0; index < 2; index++) assert.equal(await promptValue(index), config.rowPrompts[index]);
    assert.equal(await table().getByRole("textbox", { name: "全局提示词", exact: true }).inputValue(), config.globalPrompt);
    report.idle_cookie_only_recovery = true;
    await generate().click();
    let dialog = confirmation(2);
    await dialog.waitFor({ state: "visible" });
    assert.match(await dialog.innerText(), /并发上限：1/);
    await dialog.getByRole("button", { name: /^取\s*消$/ }).click();
    await dialog.waitFor({ state: "hidden" });
    const cancelled = await graph();
    assert.equal(cancelled.nodes.length, 3);
    assert.equal(cancelled.connections.length, 0);
    assert.equal(tableNode(cancelled).metadata.generationBatches, undefined);
    assert.deepEqual(await read(await owner.request.get(`${origin}${tasks}`)), []);
    assert.equal(report.submissions.length, 0);
    report.cancelled_confirmation_kept_graph = true;
    await page.screenshot({ path: resolve(output, "batch-table-idle-restored.png") });
    report.step = "原确认后第一行运行与第二行等待";
    await generate().click();
    dialog = confirmation(2);
    await dialog.getByRole("button", { name: "确认生成", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    const pending = await graph(owner, document => {
        const batch = tableNode(document)?.metadata.generationBatches?.at(-1);
        return batch?.items.length === 2 && batch.items[0].status === "running" && batch.items[1].status === "waiting";
    });
    const batch = tableNode(pending).metadata.generationBatches.at(-1);
    report.batch_id = batch.id;
    assert.equal(batch.concurrency, 1);
    assert.equal(report.submissions.length, 1);
    assert.equal(pending.nodes.length, 5);
    assert.equal(pending.connections.length, 6);
    const firstTask = await read(await owner.request.get(`${origin}${tasks}/${batch.items[0].taskId}`));
    assert.equal(firstTask.status, "running");
    assert.equal(batch.items[1].taskId, undefined);
    await fit();
    await table().getByRole("button", { name: "等待中", exact: true }).waitFor({ state: "visible" });
    await page.screenshot({ path: resolve(output, "batch-table-running-and-waiting.png") });
    report.step = "关闭旧窗口后仅 cookies 恢复运行和等待项";
    await fresh();
    const resumed = await graph(owner, document => tableNode(document)?.metadata.generationBatches?.at(-1)?.items[0].taskId === batch.items[0].taskId);
    const resumedBatch = tableNode(resumed).metadata.generationBatches.at(-1);
    assert.equal(resumedBatch.id, batch.id);
    assert.equal(resumedBatch.items[1].status, "waiting");
    assert.equal(report.submissions.length, 1);
    await table().getByRole("button", { name: "等待中", exact: true }).waitFor({ state: "visible" });
    await page.screenshot({ path: resolve(output, "batch-table-pending-restored.png") });
    report.running_and_waiting_cookie_only_recovery = true;
    await writeFile(config.imageReleaseFile, "original batch window restored\n");
    report.step = "恢复窗口自动调度原第二行";
    const completed = await graph(owner, document => tableNode(document)?.metadata.generationBatches?.at(-1)?.status === "completed");
    assert.equal(tableNode(completed).metadata.generationBatches.at(-1).id, batch.id);
    assert.equal(report.submissions.length, 2);
    assert.equal(report.uploads, 2);
    await imagesReady();
    assert.ok(await generate().isDisabled());
    report.completed_header_disabled = true;
    report.waiting_row_scheduled_after_recovery = true;
    report.output_nodes = [];
    for (const row of tableNode(completed).metadata.batchTable.rows) {
        const node = completed.nodes.find(node => node.id === row.outputNodeId);
        const key = node.metadata.storageKey;
        assert.ok(key.startsWith("resource:"));
        const response = await owner.request.get(`${origin}${resources}/${key.slice(9)}/file`);
        assert.ok(response.ok());
        report.output_nodes.push({ id: node.id, task_id: node.metadata.taskId, storage_key: key, sha256: createHash("sha256").update(await response.body()).digest("hex") });
    }
    report.step = "终态仅 cookies 恢复与成员成品查看";
    await fresh();
    await imagesReady();
    assert.ok(await generate().isDisabled());
    assert.deepEqual(tableNode(await graph()).metadata.batchTable, tableNode(completed).metadata.batchTable);
    report.final_cookie_only_recovery = true;
    await page.screenshot({ path: resolve(output, "batch-table-completed-restored.png") });
    report.step = "complete";
} catch (error) {
    report.error = error.stack ?? String(error);
    if (page && !page.isClosed()) await page.screenshot({ path: resolve(output, "failed.png") }).catch(() => {});
    process.exitCode = 1;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    if (browser) await browser.close();
    await server.close();
    process.stdout.write(JSON.stringify(report) + "\n");
}
