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
const tasks = "/api/v1/canvas-runtime/tasks";
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = { step: "start", mode: config.mode, kind: config.kind, task_posts: [], page_errors: [], failed_routes: [], original_hidden_menu_preserved: false, dynamic_fields_edited: false, invalid_parameters_blocked: false, source_saved_before_admission: false, closed_owner_before_completion: false, same_task_recovered: false, fresh_report_restored: false, restored_parameters: false, unknown_retry_unavailable: false };
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
        if (request.method() === "POST" && new URL(request.url()).pathname === tasks) {
            const body = request.postDataJSON();
            report.task_posts.push(body);
        }
    });
    target.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (route.startsWith("/api/") && response.status() >= 400) report.failed_routes.push({ route, status: response.status() });
    });
}
async function context(cookies) {
    const target = await browser.newContext({ viewport: { width: 1440, height: 900 }, ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    target.setDefaultTimeout(25000);
    target.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await target.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username: config.username, password: config.password } }));
    const csrf = (await target.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await target.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return target;
}
async function poll(target, route, predicate) {
    let result;
    for (let i = 0; i < 400; i++) {
        result = await read(await target.request.get(`${origin}${route}`));
        if (predicate(result)) return result;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`RunningHub 状态未达到预期 ${route}: ${JSON.stringify(result)}`);
}
async function open(target, taskId) {
    page = await target.newPage();
    observe(page);
    const recovery = taskId ? page.waitForResponse(async response => {
        const route = new URL(response.url()).pathname;
        if (response.request().method() !== "GET" || !route.startsWith(tasks) || !response.ok() || route.endsWith("/logs")) return false;
        const data = await response.json().catch(() => undefined);
        return Array.isArray(data) ? data.some(item => item.id === taskId) : data?.id === taskId;
    }) : undefined;
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    if (recovery) await recovery;
    const closeAssistant = page.getByRole("button", { name: "关闭助手", exact: true });
    if (await closeAssistant.isVisible()) await closeAssistant.click();
}
async function inspectResult() {
    await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    const target = page.locator(`[data-node-id="${report.node_id}"]`);
    if (config.unknown) {
        await target.locator(".canvas-node-error-content").waitFor({ state: "visible" });
        assert.equal(await target.getByRole("button", { name: /^重新生成/ }).count(), 0);
        return;
    }
    if (config.mode === "image") {
        await target.locator("img").first().waitFor({ state: "visible" });
        await page.waitForFunction(nodeId => [...document.querySelectorAll(`[data-node-id="${nodeId}"] img`)].some(image => image.complete && image.naturalWidth > 0), report.node_id);
    } else if (config.mode === "video") {
        await target.getByRole("button", { name: /^播放/ }).click();
        await target.locator("video").waitFor({ state: "attached" });
        await page.waitForFunction(nodeId => {
            const video = document.querySelector(`[data-node-id="${nodeId}"] video`);
            return video && video.readyState >= 2 && video.videoWidth > 0 && video.currentTime > 0;
        }, report.node_id);
        await page.waitForFunction(nodeId => {
            const indicator = document.querySelector(`[data-node-id="${nodeId}"] .vds-buffering-spinner`);
            return indicator && Number(getComputedStyle(indicator).opacity) === 0;
        }, report.node_id);
    } else {
        const player = target.getByRole("group", { name: /播放控件$/ });
        await player.getByRole("button", { name: /^播放/ }).click();
        await player.getByRole("button", { name: /^暂停/ }).waitFor({ state: "visible" });
        await page.waitForFunction(nodeId => Number(document.querySelector(`[data-node-id="${nodeId}"] input[type="range"]`)?.value) > 0, report.node_id);
        const pause = player.getByRole("button", { name: /^暂停/ });
        if (await pause.isVisible()) await pause.click();
        assert.equal(await player.getByRole("status").count(), 0);
    }
}
async function inspectWorkflowParameters() {
    const panel = page.locator(".canvas-config-node-panel");
    const field = label => panel.locator("label").filter({ hasText: label });
    assert.equal(await field("引导系数").getByRole("spinbutton").inputValue(), "7");
    assert.equal(await field("采样器").locator(".ant-select-selection-item").innerText(), "dpmpp_2m");
    assert.equal(await field("质量强度").getByRole("spinbutton").inputValue(), "0.55");
    assert.equal(await field("质量强度").getByRole("slider").count(), config.kind === "app" ? 1 : 0);
    assert.equal(await field("工作流水印").getByRole("switch").getAttribute("aria-checked"), "true");
    assert.equal(await field("负向提示词").getByRole("textbox").inputValue(), config.workflowParameters["field:3:negative_prompt"]);
    const seed = field("随机种子");
    assert.equal(await seed.locator("button[aria-pressed]").getAttribute("aria-pressed"), "false");
    assert.equal(await seed.getByRole("spinbutton").inputValue(), "73");
    assert.equal(await seed.getByRole("spinbutton").isEnabled(), true);
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await context();
    await open(owner);
    report.step = "original-workflow-menu";
    await page.getByRole("button", { name: "添加节点", exact: true }).click();
    await page.getByRole("button", { name: "搜索节点", exact: true }).click();
    await page.getByRole("textbox", { name: "搜索节点", exact: true }).fill("工作流");
    assert.equal(await page.getByRole("button", { name: "工作流", exact: true }).count(), 0);
    await page.getByRole("button", { name: "添加节点", exact: true }).click();
    report.original_hidden_menu_preserved = true;
    await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    const panel = page.locator(".canvas-config-node-panel");
    await panel.waitFor({ state: "visible" });
    report.source_node_id = await panel.locator("xpath=ancestor::*[@data-node-id][1]").getAttribute("data-node-id");
    assert.ok(report.source_node_id);
    report.step = "workflow-selection";
    await panel.locator(".canvas-config-mode").getByText({ image: "生图", video: "视频", audio: "音频" }[config.mode], { exact: true }).click();
    await panel.locator(".canvas-config-provider").getByText("RunningHub", { exact: true }).click();
    await panel.locator(".ant-select-selector").first().click();
    await page.locator(".ant-select-dropdown:visible").getByText("已保存工作流", { exact: true }).click();
    await panel.locator(".canvas-config-prompt-button").click();
    const composer = page.locator(".canvas-config-composer-editor [contenteditable]");
    await composer.fill("原 RunningHub 界面在生成前保存的新提示词");
    await page.locator(".canvas-config-composer").getByRole("button").filter({ has: page.locator("svg.lucide-x") }).click();
    report.step = "dynamic-parameters";
    const generate = panel.getByRole("button", { name: /生成.*开始生成/ });
    await generate.waitFor({ state: "visible" });
    assert.equal(await generate.isEnabled(), true);
    const field = label => panel.locator("label").filter({ hasText: label });
    const parameter = field("引导系数").getByRole("spinbutton");
    await parameter.fill("7.5");
    await panel.getByText("引导系数：数值不符合步长", { exact: true }).waitFor({ state: "visible" });
    assert.equal(await generate.isDisabled(), true);
    assert.equal(report.task_posts.length, 0);
    await parameter.fill("");
    await panel.getByText("引导系数：请输入有效数字", { exact: true }).waitFor({ state: "visible" });
    assert.equal(await generate.isDisabled(), true);
    assert.equal(report.task_posts.length, 0);
    report.invalid_parameters_blocked = true;
    await parameter.fill("7");
    await parameter.press("Tab");
    await field("采样器").locator(".ant-select-selector").click();
    await page.locator(".ant-select-dropdown:visible .ant-select-item-option-content").filter({ hasText: /^dpmpp_2m$/ }).click();
    const quality = field("质量强度").getByRole("spinbutton");
    if (config.kind === "app") {
        await field("质量强度").getByRole("slider").press("ArrowRight");
    } else {
        await quality.fill("0.55");
        await quality.press("Tab");
    }
    const enabled = field("工作流水印").getByRole("switch");
    assert.equal(await enabled.getAttribute("aria-checked"), "false");
    await enabled.press("Space");
    await field("负向提示词").getByRole("textbox").fill(config.workflowParameters["field:3:negative_prompt"]);
    const seed = field("随机种子");
    assert.equal(await seed.getByRole("spinbutton").isDisabled(), true);
    assert.equal(await seed.locator("button[aria-pressed]").getAttribute("aria-pressed"), "true");
    await seed.locator("button[aria-pressed]").press("Enter");
    await seed.getByRole("spinbutton").fill("73");
    await seed.getByRole("spinbutton").press("Tab");
    await inspectWorkflowParameters();
    assert.equal(await generate.isEnabled(), true);
    report.dynamic_fields_edited = true;
    report.step = "submit-workflow";
    const [response] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST"),
        generate.click(),
    ]);
    const task = await read(response);
    report.task_id = task.id;
    report.node_id = task.clientContext.nodeId;
    const body = report.task_posts[0];
    assert.equal(body.logicalModelId, config.logicalModelId);
    assert.equal(body.input.execution.provider, "runninghub");
    assert.equal(body.input.execution.kind, config.kind);
    for (const key of ["apiKey", "baseUrl", "workflowJson", "workflowFields", "runningHubUploadApiKey", "secretKey"]) {
        assert.equal(key in body.input.config, false);
        assert.equal(key in body.input.execution, false);
    }
    const saved = await read(await owner.request.get(`${origin}${path}/my-document`));
    const source = saved.source_document.nodes.find(node => node.id === report.source_node_id);
    assert.equal(source.metadata.composerContent, body.prompt);
    assert.equal(source.metadata.runningHubModelId, config.logicalModelId);
    assert.deepEqual(source.metadata.workflowParameters, config.workflowParameters);
    assert.equal(JSON.parse(task.inputJson).prompt, body.prompt);
    report.source_saved_before_admission = true;
    await poll(owner, `${tasks}/${task.id}`, current => config.unknown ? current.status === "failed" : current.status === "running");
    report.step = "close-owner";
    const cookies = await owner.cookies();
    await owner.close();
    report.closed_owner_before_completion = true;
    const restored = await context(cookies);
    await open(restored, task.id);
    await page.locator(`[data-node-id="${report.node_id}"]`).waitFor({ state: "visible" });
    const recovered = await read(await restored.request.get(`${origin}${tasks}/${task.id}`));
    assert.equal(recovered.id, task.id);
    assert.equal(report.task_posts.length, 1);
    report.same_task_recovered = true;
    report.step = "workflow-completion";
    if (config.unknown) {
        assert.equal(recovered.canRetry, false);
        assert.equal(recovered.canResume, false);
        report.unknown_retry_unavailable = true;
    } else {
        const binding = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && response.request().method() === "POST" && response.ok());
        await writeFile(config.releasePath, "仅 cookies 新 context 已恢复原 RunningHub 任务。\n");
        const bound = await read(await binding);
        assert.equal(bound.result.taskId, task.id);
        assert.equal(bound.result.node.id, report.node_id);
        const ready = await poll(restored, `${tasks}/${task.id}`, value => value.status === "succeeded");
        const result = JSON.parse(ready.resultJson);
        const media = config.mode === "image" ? result.images[0] : result[config.mode];
        report.storage_key = media.storageKey;
        const downloaded = await restored.request.get(`${origin}${media.url}`);
        assert.ok(downloaded.ok());
        assert.equal(createHash("sha256").update(await downloaded.body()).digest("hex"), config.mediaHash);
        await poll(restored, `${path}/my-document`, value => value.source_document.nodes.find(node => node.id === report.node_id)?.metadata.storageKey === media.storageKey);
    }
    await inspectResult();
    await inspectWorkflowParameters();
    await page.screenshot({ path: resolve(output, "runninghub-restored.png") });
    const finalCookies = await restored.cookies();
    await restored.close();
    const finalContext = await context(finalCookies);
    await open(finalContext);
    await inspectResult();
    await inspectWorkflowParameters();
    report.restored_parameters = true;
    await page.screenshot({ path: resolve(output, "runninghub-final-fresh.png") });
    const listed = await read(await finalContext.request.get(`${origin}${tasks}?projectId=${config.sourceKey}`));
    assert.deepEqual(listed.map(item => item.id), [task.id]);
    assert.equal(report.task_posts.length, 1);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.failed_routes, []);
    report.fresh_report_restored = true;
    report.step = "complete";
} catch (error) {
    report.failure_stack = (error.stack || error.message || String(error)).split("\n").filter((line, index) => index === 0 || /^\s*at\s/.test(line)).slice(0, 12).join("\n");
    if (page && !page.isClosed()) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await new Promise(resolve => server.httpServer.close(resolve));
}
process.stdout.write(JSON.stringify(report));
