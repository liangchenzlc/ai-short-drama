import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const port = config.browserPort ?? 4199;
assert.ok(Number.isInteger(port) && port >= 1024 && port <= 65535);
const origin = "http://127.0.0.1:" + port;
const resources = "/api/v1/canvas-runtime/resources";
const tasks = "/api/v1/canvas-runtime/tasks";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const output = resolve(config.outputDirectory);
const title = "AI 多角度：向左旋转 90 度，俯视 60 度，镜头距离 10.0，广角镜头";
const prompt = `基于参考图重新生成同一主体的新视角，保持主体、颜色、材质和画面风格一致，不要只做透视变形。${title}。`;
const report = { scope: "real Agnes original angle UI", width: config.width, source_views: [], angle_prompt: prompt, step: "start", scenario: config.scenario, page_errors: [], http_errors: [], uploads: 0, submissions: [] };
await mkdir(output, { recursive: true });
const server = await createServer({
    cacheDir: resolve(output, "node_modules/.vite"),
    server: { host: "127.0.0.1", port, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } },
    logLevel: "silent",
});
await server.listen();
let browser;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}`);
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

async function eventually(readCurrent, accepted, limit = 45000) {
    let current;
    const deadline = Date.now() + limit;
    while (Date.now() < deadline) {
        current = await readCurrent();
        if (accepted(current)) return current;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`多角度未达到预期：${JSON.stringify(current)}`);
}

async function graph(context, accepted, limit) {
    return eventually(async () => (await read(await context.request.get(`${origin}${path}/my-document`))).source_document, accepted, limit);
}

async function inspectSource(owner, source, phase) {
    const image = page.locator(`[data-node-id="${source.id}"] img`).first();
    await image.waitFor({ state: "visible" });
    const dimensions = await image.evaluate(async image => { await image.decode(); return { width: image.naturalWidth, height: image.naturalHeight }; });
    assert.deepEqual(dimensions, config.sourceDimensions);
    const response = await owner.request.get(`${origin}${resources}/${source.metadata.storageKey.slice(9)}/file`);
    assert.ok(response.ok());
    const sha256 = createHash("sha256").update(await response.body()).digest("hex");
    assert.equal(sha256, config.sourceSha256);
    report.source_views.push({ phase, sha256, ...dimensions });
}

async function fresh(owner) {
    const cookies = (await owner.storageState()).cookies;
    await owner.close();
    const context = await browser.newContext({ storageState: { cookies, origins: [] }, viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference" });
    await authorize(context);
    page = await context.newPage();
    observe(page);
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    return context;
}

function observe(nextPage) {
    nextPage.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    nextPage.on("response", response => {
        if (response.status() >= 400) report.http_errors.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
    });
    nextPage.on("request", request => {
        const pathname = new URL(request.url()).pathname;
        if (pathname === resources && request.method() === "POST") report.uploads++;
        if (pathname === tasks && request.method() === "POST") report.submissions.push(request.postDataJSON());
    });
}

async function openEditor(source) {
    const node = page.locator(`[data-node-id="${source.id}"]`);
    await node.click();
    await node.hover();
    await page.getByRole("toolbar", { name: "节点快捷工具" }).getByRole("button", { name: config.width < 640 ? "工具" : "视角调整", exact: true }).click();
    await page.getByRole("menuitem", { name: "多角度", exact: true }).click();
    const editor = page.locator(".canvas-angle-panel");
    await editor.waitFor();
    await page.waitForFunction(async () => {
        const panel = document.querySelector(".canvas-angle-panel");
        if (!panel || getComputedStyle(panel).opacity !== "1") return false;
        let previous;
        let stable = 0;
        while (stable < 8) {
            await new Promise(requestAnimationFrame);
            const bounds = panel.getBoundingClientRect();
            const current = `${bounds.x},${bounds.y},${bounds.width},${bounds.height},${getComputedStyle(panel).opacity}`;
            stable = current === previous ? stable + 1 : 0;
            previous = current;
        }
        return true;
    });
    return editor;
}

async function values(editor, expected) {
    const sliders = editor.getByRole("slider");
    assert.equal(await sliders.count(), 3);
    for (let index = 0; index < expected.length; index++) {
        assert.equal(Number(await sliders.nth(index).getAttribute("aria-valuenow")), expected[index]);
    }
}

async function dragScene(editor, mode) {
    const scene = editor.locator(`.canvas-angle-unified.is-${mode}`);
    const box = await scene.boundingBox();
    assert.ok(box);
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 36, box.y + box.height / 2 + 22, { steps: 8 });
    await page.mouse.up();
}

async function fit() {
    const focusBar = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focusBar.isVisible()) await focusBar.getByRole("button", { name: "退出专注模式", exact: true }).press("Control+0");
    else await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
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
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    let owner = await login(config.username);
    const member = await login(config.memberUsername);
    page = await owner.newPage();
    observe(page);
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor();
    await page.evaluate(() => document.fonts.ready);
    report.step = "原上传与角度默认值";
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({
        name: "agnes-angle-reference.png", mimeType: "image/png", buffer: Buffer.from(config.image, "base64"),
    });
    const original = await graph(owner, document => document.nodes.length === 1 && document.nodes[0].metadata?.assetId);
    const source = original.nodes[0];
    report.source_node = source;
    await inspectSource(owner, source, "initial");
    let editor = await openEditor(source);
    await values(editor, [45, 0, 4.8]);
    assert.equal(await editor.getByRole("button", { name: "摄像头", exact: true }).getAttribute("aria-pressed"), "true");
    assert.equal(await editor.getByRole("button", { name: "标准", exact: true }).getAttribute("aria-pressed"), "true");
    await page.screenshot({ path: resolve(output, "angle-defaults.png") });
    report.step = "六预设、键盘边界、方向与天空盒拖拽";
    for (const [name, horizontal, pitch] of [["正面", 0, 0], ["左侧", -90, 0], ["右侧", 90, 0], ["背面", 180, 0], ["俯拍", 0, 60], ["仰拍", 0, -60]]) {
        await editor.locator(".canvas-angle-preset").filter({ hasText: new RegExp(`^${name}$`) }).click();
        await values(editor, [horizontal, pitch, 4.8]);
    }
    const sliders = editor.getByRole("slider");
    for (const [index, minimum, maximum] of [[0, -180, 180], [1, -60, 60], [2, 1, 10]]) {
        await sliders.nth(index).press("Home");
        assert.equal(Number(await sliders.nth(index).getAttribute("aria-valuenow")), minimum);
        await sliders.nth(index).press("End");
        assert.equal(Number(await sliders.nth(index).getAttribute("aria-valuenow")), maximum);
    }
    await editor.getByRole("button", { name: "重置", exact: true }).click();
    await values(editor, [45, 0, 4.8]);
    await editor.getByRole("button", { name: "向左环绕", exact: true }).click();
    await values(editor, [40, 0, 4.8]);
    await editor.getByRole("button", { name: "向上俯仰", exact: true }).click();
    await values(editor, [40, 5, 4.8]);
    await editor.getByRole("button", { name: "向右环绕", exact: true }).click();
    await editor.getByRole("button", { name: "向下俯仰", exact: true }).click();
    await values(editor, [45, 0, 4.8]);
    await editor.getByRole("button", { name: "天空盒", exact: true }).click();
    await dragScene(editor, "skybox");
    await values(editor, [65, 10, 4.8]);
    await editor.getByRole("button", { name: "摄像头", exact: true }).click();
    await dragScene(editor, "camera");
    await values(editor, [85, 0, 4.8]);
    await editor.getByRole("button", { name: "关闭多角度编辑器", exact: true }).click();
    await editor.waitFor({ state: "hidden" });
    assert.deepEqual((await graph(owner, document => document.nodes.length === 1)).nodes, original.nodes);
    assert.equal(report.submissions.length, 0);
    assert.equal(report.uploads, 1);
    report.presets_keyboard_drag_reset_cancel_kept_source = true;
    report.step = "提交前关闭旧窗口仅cookies恢复原参考";
    owner = await fresh(owner);
    await page.locator(`[data-node-id="${source.id}"]`).waitFor();
    await fit();
    assert.deepEqual((await graph(owner, document => document.nodes.length === 1)).nodes, original.nodes);
    await inspectSource(owner, source, "before-submit-fresh");
    assert.equal(report.submissions.length, 0);
    assert.equal(report.uploads, 1);
    report.before_submit_fresh_context_restored = true;
    editor = await openEditor(source);
    await values(editor, [45, 0, 4.8]);
    assert.equal(await editor.getByRole("button", { name: "摄像头", exact: true }).getAttribute("aria-pressed"), "true");
    await editor.locator(".canvas-angle-preset").filter({ hasText: /^左侧$/ }).click();
    await editor.getByRole("slider").nth(1).press("End");
    await editor.getByRole("slider").nth(2).press("End");
    await editor.getByRole("button", { name: "广角", exact: true }).click();
    await values(editor, [-90, 60, 10]);
    await page.screenshot({ path: resolve(output, "angle-selected.png") });
    report.step = "原单份角度提交与私人任务";
    const admitted = page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST" && response.ok());
    await editor.getByRole("button", { name: "生成新角度", exact: true }).click();
    const response = await admitted;
    const task = await read(response);
    assert.equal(report.submissions.length, 1);
    const submitted = report.submissions[0];
    assert.equal(submitted.prompt, prompt);
    assert.equal(Number(submitted.input.config.count), 1);
    assert.equal(submitted.input.metadata.edit, "angle");
    assert.equal(submitted.input.metadata.sourceNodeId, source.id);
    assert.equal(submitted.input.referenceImages[0].storageKey, source.metadata.storageKey);
    assert.ok(!submitted.input.mask);
    report.task_id = task.id;
    report.target_node_id = submitted.input.metadata.nodeId;
    const running = await eventually(async () => read(await owner.request.get(`${origin}${tasks}/${task.id}`)), row => row.status === "running");
    assert.notEqual(running.resultState, "READY");
    const pending = await graph(owner, document => document.nodes.length === 2 && document.nodes.some(node => node.id === report.target_node_id && node.metadata?.taskId === task.id));
    assert.deepEqual(pending.nodes.find(node => node.id === source.id), source);
    assert.ok(!pending.nodes.find(node => node.id === report.target_node_id).metadata.content);
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
    if (config.scenario === "recovery") {
        report.step = "关闭旧窗口仅cookies恢复原待执行任务";
        owner = await fresh(owner);
        await page.locator(`[data-node-id="${report.target_node_id}"]`).waitFor();
        const restored = await graph(owner, document => document.nodes.some(node => node.id === report.target_node_id && node.metadata?.taskId === task.id));
        assert.ok(!restored.nodes.find(node => node.id === report.target_node_id).metadata.content);
        assert.equal((await read(await owner.request.get(`${origin}${tasks}/${task.id}`))).status, "running");
        assert.equal(report.submissions.length, 1);
        report.pending_fresh_context_restored = true;
        await fit();
        await inspectSource(owner, source, "running-fresh");
    }
    await writeFile(config.imageReleaseFile, "browser-ready");
    report.step = "终态直接回填与原永久图片身份";
    const terminal = await eventually(async () => read(await owner.request.get(`${origin}${tasks}/${task.id}`)), row => ["succeeded", "failed", "cancelled"].includes(row.status), 360000);
    report.terminal_status = terminal.status;
    report.terminal_error_code = terminal.errorCode;
    assert.equal(terminal.status, "succeeded", JSON.stringify(terminal.errorCode));
    const final = await graph(owner, document => document.nodes.length === 2 && document.nodes.some(node => node.id === report.target_node_id && node.metadata?.assetId && node.metadata?.storageKey));
    assert.deepEqual(final.nodes.find(node => node.id === source.id), source);
    const target = final.nodes.find(node => node.id === report.target_node_id);
    const completed = await read(await owner.request.get(`${origin}${tasks}/${task.id}`));
    assert.equal(completed.status, "succeeded");
    assert.equal(completed.resultState, "READY");
    const result = JSON.parse(completed.resultJson).images[0];
    assert.equal(target.metadata.storageKey, result.storageKey);
    assert.equal(target.title, title);
    assert.equal(final.connections.length, 1);
    assert.equal(final.connections[0].fromNodeId, source.id);
    assert.equal(final.connections[0].toNodeId, target.id);
    report.output_storage_key = result.storageKey;
    report.final_nodes = final.nodes;
    const shared = await member.request.get(`${origin}${resources}/${result.storageKey.slice(9)}/file`);
    assert.ok(shared.ok());
    report.output_sha256 = createHash("sha256").update(await shared.body()).digest("hex");
    owner = await fresh(owner);
    await page.locator(`[data-node-id="${source.id}"]`).waitFor();
    await fit();
    await page.waitForFunction(({ id, dimensions }) => [...document.querySelectorAll(`[data-node-id="${id}"] img`)].some(image => image.complete && image.naturalWidth === dimensions.width && image.naturalHeight === dimensions.height), { id: target.id, dimensions: config.outputDimensions });
    await inspectSource(owner, source, "terminal-fresh");
    report.output_dimensions = await page.locator(`[data-node-id="${target.id}"] img`).first().evaluate(async image => { await image.decode(); return { width: image.naturalWidth, height: image.naturalHeight }; });
    assert.deepEqual(report.output_dimensions, config.outputDimensions);
    await page.mouse.move(5, 450);
    for (const node of [source, target]) {
        const bounds = await page.locator(`[data-node-id="${node.id}"]`).boundingBox();
        assert.ok(bounds && bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= config.width && bounds.y + bounds.height <= 900);
    }
    await page.screenshot({ path: resolve(output, "fresh-restored.png") });
    assert.equal(report.submissions.length, 1);
    assert.equal(report.uploads, 1);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    report.final_fresh_context_restored = true;
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
