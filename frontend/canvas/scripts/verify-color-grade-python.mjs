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
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}/my-document`;
const resources = "/api/v1/canvas-runtime/resources";
const tasks = "/api/v1/canvas-runtime/tasks";
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = { step: "start", width: config.width, protocol: config.protocol, page_errors: [], http_errors: [], resource_posts: 0, task_posts: 0 };
const server = await createServer({ cacheDir: resolve(".runtime/vite-color-grade"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let owner;
let page;
const card = id => page.locator(`[data-node-id="${id}"]`);
const grade = () => card("existing-grade");
const digest = data => createHash("sha256").update(data).digest("hex");
async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function eventually(readValue, predicate, label) {
    let value;
    for (let attempt = 0; attempt < 300; attempt++) {
        value = await readValue();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`${label}: ${JSON.stringify(value)}`);
}
async function graph(predicate = () => true, user = owner) {
    return eventually(async () => (await read(await user.request.get(origin + path))).source_document, predicate, "调色作品没有保存到预期状态");
}
async function context(username, cookies) {
    const value = await browser.newContext({ viewport: { width: config.width, height: 900 }, ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    value.setDefaultTimeout(20000);
    value.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await value.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await value.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await value.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return value;
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
            const value = ["--canvas-live-x", "--canvas-live-y", "--canvas-live-scale"].map(key => viewport.style.getPropertyValue(key)).join(",");
            stable = value === previous ? stable + 1 : 0;
            previous = value;
        }
        return true;
    });
}
async function open(user) {
    page = await user.newPage();
    page.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    page.on("request", request => {
        const pathname = new URL(request.url()).pathname;
        if (request.method() === "POST" && pathname === resources) report.resource_posts++;
        if (request.method() === "POST" && pathname === tasks) report.task_posts++;
    });
    page.on("response", response => {
        const pathname = new URL(response.url()).pathname;
        if (pathname.startsWith("/api/") && response.status() >= 400) report.http_errors.push({ path: pathname, status: response.status() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    await fit();
}
async function save() {
    const previous = await page.evaluateHandle(() => new Set([...document.querySelectorAll(".ant-message span")].filter(element => /^画布已保存(?:到云端)?$/.test(element.textContent || ""))));
    try {
        await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
        await page.waitForFunction(previous => [...document.querySelectorAll(".ant-message span")].some(element => /^画布已保存(?:到云端)?$/.test(element.textContent || "") && !previous.has(element)), previous);
        await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
    } finally {
        await previous.dispose();
    }
}
async function dimBrightness() {
    const slider = grade().getByRole("slider", { name: /^亮度/ });
    await slider.press("Home");
    for (let index = 0; index < 50; index++) await slider.press("ArrowRight");
    await graph(document => document.nodes.find(node => node.id === "existing-grade")?.metadata.colorGrade?.brightness === 50);
}
async function checkPreview(filename) {
    const image = grade().getByRole("img").first();
    await image.waitFor({ state: "visible" });
    await eventually(() => image.evaluate(element => element.complete && element.naturalWidth === 256), Boolean, "原调色预览没有加载");
    const filter = await image.evaluate(element => getComputedStyle(element).filter);
    assert.match(filter, /brightness\(0\.5\)/);
    await image.screenshot({ path: resolve(output, filename) });
    return filter;
}
try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    owner = await context(config.username);
    const member = await context(config.memberUsername);
    await open(owner);
    report.step = "已有空调色节点原图片连线";
    await grade().getByText("连接一张图片即可调色", { exact: true }).waitFor({ state: "visible" });
    await eventually(() => card("grade-source").getByRole("img").evaluate(element => element.complete && element.naturalWidth === 256), Boolean, "原图片尚未加载");
    await save();
    await card("grade-source").click();
    await card("grade-source").hover();
    const from = await card("grade-source").locator('[data-canvas-connection-rail="right"]').boundingBox();
    const target = await grade().boundingBox();
    assert.ok(from && target);
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(target.x + 1, target.y + target.height / 2, { steps: 18 });
    await page.mouse.up();
    const connected = await graph(document => document.connections.length === 1);
    const original = connected.nodes.find(node => node.id === "grade-source");
    assert.equal(original.width, 472);
    assert.equal(original.height, 236);
    assert.deepEqual(original.position, { x: -526, y: -198 });
    await grade().getByText("未调色", { exact: true }).waitFor({ state: "visible" });
    report.original_input_connected = true;

    report.step = "原调色滑杆键盘、复位和CSS预览";
    await grade().getByRole("slider", { name: /^对比/ }).press("ArrowRight");
    await grade().getByRole("slider", { name: /^饱和/ }).press("Home");
    await grade().getByRole("slider", { name: /^色相/ }).press("ArrowRight");
    await grade().getByRole("button", { name: "复位", exact: true }).click();
    await graph(document => Object.entries(config.neutralGrade).every(([key, value]) => document.nodes.find(node => node.id === "existing-grade")?.metadata.colorGrade?.[key] === value));
    await dimBrightness();
    report.preview_filter = await checkPreview("color-grade-preview.png");
    assert.equal(report.resource_posts, 0, "原滑杆预览不上传媒体");
    report.original_sliders_and_reset = true;

    report.step = "原调色输出连接点创建文字并先保存生成";
    await grade().hover();
    await grade().locator('[data-canvas-connection-rail="right"]').click();
    await page.locator("[data-connection-create-menu]").getByRole("button", { name: "文本", exact: true }).click();
    const linked = await graph(document => document.nodes.length === 3 && document.connections.length === 2);
    report.text_node_id = linked.nodes.find(node => node.type === "text").id;
    await fit();
    await card(report.text_node_id).click({ position: { x: 20, y: 12 } });
    await page.getByRole("textbox", { name: "文本提示词", exact: true }).fill(config.prompt);
    await save();
    const admission = page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST");
    await page.getByRole("button", { name: "生成", exact: true }).click();
    const task = await read(await admission);
    report.task_id = task.id;
    const declared = JSON.parse(task.inputJson);
    assert.equal(declared.prompt, config.prompt);
    assert.equal(declared.referenceImages.length, 1);
    const reference = declared.referenceImages[0];
    assert.match(reference.storageKey, /^resource:\d+$/);
    report.graded_resource_id = reference.storageKey.slice("resource:".length);
    assert.notEqual(report.graded_resource_id, config.sourceResourceId);
    const download = await owner.request.get(`${origin}${resources}/${report.graded_resource_id}/file`);
    assert.ok(download.ok());
    const data = await download.body();
    report.graded_checksum = digest(data);
    report.graded_bytes = data.length;
    await writeFile(resolve(output, "color-grade-reference.png"), data);
    assert.equal(report.resource_posts, 1);
    report.original_generation_reference = true;

    report.step = "私人首段、关闭旧窗口后恢复同一任务";
    const draft = await eventually(async () => read(await owner.request.get(`${origin}${tasks}/${task.id}/text-deltas`)), value => value.textDraft === config.first, "调色文字首段没有归档");
    assert.equal(draft.status, "running");
    const pending = await graph(document => document.nodes.find(node => node.id === report.text_node_id)?.metadata.taskId === task.id);
    assert.equal(pending.nodes.find(node => node.id === report.text_node_id).metadata.status, "loading");
    assert.ok(!pending.nodes.find(node => node.id === report.text_node_id).metadata.content);
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}/text-deltas`)).status(), 404);
    await save();
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(undefined, cookies);
    await open(owner);
    const resumed = await graph(document => document.nodes.find(node => node.id === report.text_node_id)?.metadata.taskId === task.id);
    assert.equal(resumed.nodes.find(node => node.id === report.text_node_id).metadata.status, "loading");
    assert.ok(!(await card(report.text_node_id).innerText()).includes(config.first));
    await checkPreview("color-grade-pending-restored.png");
    assert.equal(report.resource_posts, 1);
    await writeFile(config.releasePath, "Cookie-only original color-grade task restored.\n");
    await graph(document => document.nodes.find(node => node.id === report.text_node_id)?.metadata.content === config.first + config.second);
    await save();
    assert.deepEqual((await graph()).nodes.find(node => node.id === "grade-source"), original);
    await page.screenshot({ path: resolve(output, "color-grade-restored.png"), fullPage: true });
    report.cookie_only_task_recovery = true;

    report.step = "成员原调色预览和共享终态";
    await open(member);
    await checkPreview("color-grade-member-preview.png");
    const shared = await graph(() => true, member);
    assert.equal(shared.nodes.find(node => node.id === report.text_node_id).metadata.content, config.first + config.second);
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
    await page.screenshot({ path: resolve(output, "color-grade-member.png"), fullPage: true });
    report.member_result_seen = true;

    report.step = "源未注册调色创建菜单保持隐藏";
    await page.locator("[data-canvas-viewport]").click({ button: "right", position: { x: 25, y: 150 } });
    await page.locator("[data-canvas-context-menu]").getByRole("button", { name: "添加节点", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "调色", exact: true }).count(), 0);
    await page.getByRole("button", { name: "搜索节点", exact: true }).click();
    await page.getByRole("textbox", { name: "搜索节点", exact: true }).fill("调色");
    assert.equal(await page.getByRole("button", { name: "调色", exact: true }).count(), 0);
    report.creation_menu_absent_like_source = true;
    assert.equal(report.task_posts, 1);
    assert.equal(report.resource_posts, 1);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    report.step = "complete";
    process.stdout.write(JSON.stringify(report));
} catch (error) {
    report.error = error.stack ?? String(error);
    if (page && !page.isClosed()) {
        await page.screenshot({ path: resolve(output, "failure.png"), fullPage: true }).catch(() => {});
        const snapshot = await page.locator("body").ariaSnapshot().catch(() => undefined);
        if (snapshot) await writeFile(resolve(output, "failure.yml"), snapshot);
    }
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
