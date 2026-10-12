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
const report = { step: "start", protocol: config.protocol, format: config.format, task_posts: [], page_errors: [], failed_routes: [], source_saved_before_admission: false, closed_owner_before_completion: false, same_task_recovered: false, original_play_clicked: false, real_audio_decoded: false, pcm_decode_error_shown: false, fresh_report_restored: false, unknown_retry_unavailable: false };
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
            report.task_posts.push({ operation_key: body.input.metadata.clientOperationId, source_node: body.input.metadata.sourceNodeId, target_node: body.input.metadata.nodeId, prompt: body.prompt, config: body.input.config, provider_options: body.input.metadata.providerOptions });
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
    assert.fail(`音频状态未达到预期 ${route}: ${JSON.stringify(result)}`);
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
}
async function inspectAudio(nodeId, screenshot) {
    const closeAssistant = page.getByRole("button", { name: "关闭助手", exact: true });
    if (await closeAssistant.isVisible()) await closeAssistant.click();
    await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    const player = page.locator(`[data-node-id="${nodeId}"]`).getByRole("group", { name: /播放控件$/ });
    await player.waitFor({ state: "visible" });
    await player.getByRole("button", { name: /^播放/ }).click();
    report.original_play_clicked = true;
    if (config.format === "pcm") {
        const error = player.getByRole("status");
        await error.waitFor({ state: "visible" });
        assert.ok((await error.textContent()).trim());
        assert.equal(await player.getByRole("slider").isDisabled(), true);
        assert.equal(await player.getByRole("button", { name: /^暂停/ }).count(), 0);
        report.pcm_decode_error_shown = true;
        await page.screenshot({ path: resolve(output, screenshot) });
        return;
    }
    await player.getByRole("button", { name: /^暂停/ }).waitFor({ state: "visible" });
    const slider = player.getByRole("slider");
    await page.waitForFunction(nodeId => {
        const slider = document.querySelector(`[data-node-id="${nodeId}"] input[type="range"]`);
        return slider && Number(slider.value) > 0 && Number(slider.max) >= 900 && Number(slider.max) <= 1500;
    }, nodeId);
    const pause = player.getByRole("button", { name: /^暂停/ });
    if (await pause.isVisible()) await pause.click();
    assert.equal(await player.getByRole("status").count(), 0);
    await slider.focus();
    await slider.press("Home");
    await slider.press("ArrowRight");
    assert.ok(Number(await slider.inputValue()) <= 20);
    report.real_audio_decoded = true;
    await page.screenshot({ path: resolve(output, screenshot) });
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await context();
    await open(owner);
    report.step = "source-prompt";
    const source = page.locator('[data-node-id="runtime-node"]');
    await source.waitFor({ state: "visible" });
    await source.locator(".canvas-node-shell").click({ position: { x: 160, y: 110 } });
    const prompt = page.getByRole("textbox", { name: "音频提示词", exact: true });
    await prompt.waitFor({ state: "visible" });
    const typed = "原音频界面在生成前保存的新旁白";
    await prompt.fill(typed);
    const [response] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST"),
        page.getByRole("button", { name: "生成", exact: true }).click(),
    ]);
    const task = await read(response);
    report.task_id = task.id;
    report.node_id = task.clientContext.nodeId;
    const saved = await read(await owner.request.get(`${origin}${path}/my-document`));
    assert.equal(saved.source_document.nodes.find(node => node.id === "runtime-node").metadata.prompt, typed);
    assert.equal(JSON.parse(task.inputJson).prompt, typed);
    assert.equal(report.task_posts.length, 1);
    report.source_saved_before_admission = true;
    report.step = "close-owner";
    await poll(owner, `${tasks}/${task.id}`, current => config.protocol === "unknown" ? current.status === "failed" : current.status === "running");
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
    report.step = "source-completion";
    if (config.protocol === "unknown") {
        assert.equal(recovered.canRetry, false);
        assert.equal(recovered.canResume, false);
        const node = page.locator(`[data-node-id="${report.node_id}"]`);
        await node.locator(".canvas-node-error-content").waitFor({ state: "visible" });
        assert.equal(await node.getByRole("button", { name: /^重新生成/ }).count(), 0);
        report.unknown_retry_unavailable = true;
        await page.screenshot({ path: resolve(output, "unknown-restored.png") });
    } else {
        const boundResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && response.request().method() === "POST" && response.ok());
        await writeFile(config.releasePath, "仅cookies新context已恢复原音频任务。\n");
        const bound = await read(await boundResponse);
        assert.equal(bound.result.taskId, task.id);
        assert.equal(bound.result.node.id, report.node_id);
        const ready = await poll(restored, `${tasks}/${task.id}`, value => value.status === "succeeded");
        const media = JSON.parse(ready.resultJson).audio;
        report.storage_key = media.storageKey;
        const downloaded = await restored.request.get(`${origin}${media.url}`);
        assert.ok(downloaded.ok());
        assert.equal(createHash("sha256").update(await downloaded.body()).digest("hex"), config.mediaHash);
        await poll(restored, `${path}/my-document`, value => value.source_document.nodes.find(node => node.id === report.node_id)?.metadata.storageKey === media.storageKey);
        await inspectAudio(report.node_id, "audio-restored.png");
    }
    const finalCookies = await restored.cookies();
    await restored.close();
    const finalContext = await context(finalCookies);
    await open(finalContext);
    if (config.protocol === "unknown") {
        const node = page.locator(`[data-node-id="${report.node_id}"]`);
        await node.locator(".canvas-node-error-content").waitFor({ state: "visible" });
        assert.equal(await node.getByRole("button", { name: /^重新生成/ }).count(), 0);
    } else await inspectAudio(report.node_id, "audio-final-fresh.png");
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
