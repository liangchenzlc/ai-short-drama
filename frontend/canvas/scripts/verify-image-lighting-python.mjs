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
const consistency = [
    "this is a lighting-only edit of the reference image: same scene, same people, same action, same clothing, same background, only the lighting changes",
    "the following instructions describe how light falls on the subject, they are not new objects to add to the scene",
    "do not add any lamp, spotlight, light fixture, reflector, softbox, torch, candle, or photography equipment into the image",
    "preserve identity, face, outfit, hairstyle, body pose, and the background layout from the input image",
    "if multiple people are present, preserve the same number of people and their spatial relationship",
    "only change lighting direction, light color, brightness, shadows, contrast, and mood",
].join(", ");
const placeholders = ["consistencyPrompt", "presetPrompt", "smartDesc", "lightDirectionPrompt", "brightnessPrompt", "rimLightPrompt", "lightColorPrompt", "lightingMeta"];
const defaultTemplate = placeholders.map(key => `{{${key}}}`).join(", ");
const defaultPrompt = `${consistency}, main key light coming from the front, [lighting azimuth:0° elevation:0° brightness:50%]`;
const prompt = `${consistency}, custom lighting, Rembrandt lighting, 45-degree angle key light, dramatic chiaroscuro, painterly shadows, classical portraiture, 柔和的蓝色氛围光, main key light coming from the left side, pure side-light, high-key, bright and well-lit scene, lifted exposure, add clear rim light and edge highlight along the silhouette, key light color temperature tinted toward #3344cc, [lighting azimuth:270° elevation:0° brightness:100% rim:on]`;
const title = "AI 打光：伦勃朗光，亮度 100%，轮廓光，光色 #3344cc";
const report = { step: "start", scenario: config.scenario, page_errors: [], http_errors: [], uploads: 0, submissions: [], viewport_events: [] };
const observations = [];
let windowSequence = 0;
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(".runtime/vite-lighting-browser"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
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
    await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
    context.setDefaultTimeout(20000);
    context.setDefaultNavigationTimeout(60000);
}
async function login(username) {
    const context = await browser.newContext({ viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference" });
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
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
    assert.fail(`打光未达到预期：${JSON.stringify(current)}`);
}
async function graph(context, accepted) {
    return eventually(async () => (await read(await context.request.get(`${origin}${path}/my-document`))).source_document, accepted);
}
function observe(nextPage) {
    const windowId = ++windowSequence;
    nextPage.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    nextPage.on("response", response => {
        if (response.status() >= 400) report.http_errors.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
        const pathname = new URL(response.url()).pathname;
        if (pathname === `${path}/viewport` || pathname === `${path}/my-document`) {
            const event = { window: windowId, step: report.step, time: Date.now(), method: response.request().method(), path: pathname, status: response.status() };
            observations.push(response.json().then(body => {
                report.viewport_events.push({ ...event, viewport: body.viewport ?? body.source_document?.viewport, error: body.error?.code });
            }).catch(error => report.viewport_events.push({ ...event, unavailable: error.message })));
        }
    });
    nextPage.on("request", request => {
        const pathname = new URL(request.url()).pathname;
        if (pathname === resources && request.method() === "POST") report.uploads++;
        if (pathname === tasks && request.method() === "POST") report.submissions.push(request.postDataJSON());
        if (pathname === `${path}/viewport` && request.method() === "PUT") report.viewport_events.push({ window: windowId, step: report.step, time: Date.now(), method: "PUT", payload: request.postDataJSON() });
    });
}
async function openEditor(source) {
    const node = page.locator(`[data-node-id="${source.id}"]`);
    await node.click();
    await node.hover();
    await page.getByRole("toolbar", { name: "节点快捷工具" }).getByRole("button", { name: config.width < 640 ? "工具" : "视角调整", exact: true }).click();
    await page.getByRole("menuitem", { name: "打光", exact: true }).click();
    const editor = page.getByRole("dialog").filter({ has: page.getByText("打光效果", { exact: true }) });
    await editor.waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll(".ant-modal, .ant-modal-mask")].every(element => getComputedStyle(element).animationName === "none"));
    return editor;
}
async function copy(editor, accepted) {
    await editor.getByRole("button", { name: "复制提示词", exact: true }).click();
    return eventually(() => page.evaluate(() => navigator.clipboard.readText()), accepted);
}
async function settings(editor) {
    await editor.getByRole("button", { name: "设置提示词", exact: true }).click();
    return page.getByText("设置打光默认提示词", { exact: true }).locator("..").locator("..");
}
async function fresh(owner, width) {
    const cookies = (await owner.storageState()).cookies;
    await owner.close();
    const context = await browser.newContext({ storageState: { cookies, origins: [] }, viewport: { width, height: 900 }, reducedMotion: "no-preference" });
    await authorize(context);
    page = await context.newPage();
    observe(page);
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    return context;
}
async function fit() {
    const focusBar = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focusBar.isVisible()) await focusBar.getByTitle("适应画布", { exact: true }).click();
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
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: "lighting-source-pattern.png", mimeType: "image/png", buffer: Buffer.from(config.image, "base64") });
    const original = await graph(owner, document => document.nodes.length === 1 && document.nodes[0].metadata?.assetId);
    const source = original.nodes[0];
    report.source_node = source;
    report.step = "原打光默认值与真实剪贴板";
    let editor = await openEditor(source);
    assert.equal(await editor.getByRole("switch", { name: "智能模式", exact: true }).getAttribute("aria-checked"), "true");
    assert.equal(await editor.getByRole("switch", { name: "轮廓光", exact: true }).getAttribute("aria-checked"), "false");
    assert.equal(await editor.getByLabel("光线颜色", { exact: true }).inputValue(), "#ffffff");
    assert.equal(await editor.getByRole("slider").getAttribute("aria-valuenow"), "50");
    assert.equal(await copy(editor, value => value === defaultPrompt), defaultPrompt);
    await page.screenshot({ path: resolve(output, "lighting-defaults.png") });
    report.step = "六光位、八风格、键盘边界与球面拖拽";
    for (const [name, azimuth, elevation] of [["左侧", 270, 0], ["顶部", 0, 80], ["右侧", 90, 0], ["前方", 0, 0], ["底部", 0, -80], ["后方", 180, 0]]) {
        await editor.getByRole("button", { name, exact: true }).click();
        assert.equal(await editor.getByRole("button", { name, exact: true }).getAttribute("aria-pressed"), "true");
        await copy(editor, value => value.endsWith(`[lighting azimuth:${azimuth}° elevation:${elevation}° brightness:50%]`));
    }
    for (const [name, text] of [["过曝胶片", "overexposed film aesthetic"], ["蓝色逆光", "dramatic backlighting"], ["伦勃朗光", "Rembrandt lighting"], ["赛博朋克", "cyberpunk neon lighting"], ["落日迷幻", "golden hour lighting"], ["神秘暗调", "low-key noir lighting"], ["黄金时刻", "golden hour photography"], ["诺兰冷灰", "Christopher Nolan cinematography"]]) {
        await editor.getByRole("button", { name, exact: true }).click();
        assert.equal(await editor.getByRole("button", { name, exact: true }).getAttribute("aria-pressed"), "true");
        await copy(editor, value => value.includes(text));
        await editor.getByRole("button", { name, exact: true }).click();
        assert.equal(await editor.getByRole("button", { name, exact: true }).getAttribute("aria-pressed"), "false");
    }
    await editor.getByRole("slider").press("Home");
    await copy(editor, value => value.includes("brightness:0%") && value.includes("low-key, dim and moody scene"));
    await editor.getByRole("slider").press("End");
    await copy(editor, value => value.includes("brightness:100%") && value.includes("high-key, bright and well-lit scene"));
    await editor.getByRole("button", { name: "重置参数", exact: true }).click();
    await editor.getByRole("button", { name: "正面", exact: true }).click();
    const box = await editor.getByLabel("拖动调整主光源方向").boundingBox();
    assert.ok(box);
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 30, box.y + box.height / 2 - 20, { steps: 8 });
    await page.mouse.up();
    await copy(editor, value => value.endsWith("[lighting azimuth:45° elevation:20° brightness:50%]"));
    await editor.getByRole("switch", { name: "智能模式", exact: true }).click();
    assert.ok(await editor.getByPlaceholder("简单描述你想要实现的打光效果，或者情绪风格").isDisabled());
    await editor.getByRole("button", { name: "关闭打光效果", exact: true }).click();
    await editor.waitFor({ state: "hidden" });
    assert.deepEqual((await graph(owner, document => document.nodes.length === 1)).nodes, original.nodes);
    assert.equal(report.uploads, 1);
    assert.equal(report.submissions.length, 0);
    report.lighting_controls_cancel_kept_source = true;
    editor = await openEditor(source);
    assert.equal(await copy(editor, value => value === defaultPrompt), defaultPrompt);
    report.step = "原模板设置、保护描述和参数";
    let template = await settings(editor);
    assert.equal(await template.getByRole("textbox").inputValue(), defaultTemplate);
    const customTemplate = `custom lighting, ${placeholders.filter(key => key !== "consistencyPrompt").map(key => `{{${key}}}`).join(", ")}`;
    await template.getByRole("textbox").fill(customTemplate);
    await template.getByRole("button", { name: "关闭提示词设置", exact: true }).click();
    await copy(editor, value => value.startsWith(`${consistency}, custom lighting, `));
    template = await settings(editor);
    assert.equal(await template.getByRole("textbox").inputValue(), customTemplate);
    await template.getByRole("button", { name: "恢复默认", exact: true }).click();
    assert.equal(await template.getByRole("textbox").inputValue(), defaultTemplate);
    await template.getByRole("textbox").fill(customTemplate);
    await template.getByRole("button", { name: "保存", exact: true }).click();
    await editor.getByRole("button", { name: "左侧", exact: true }).click();
    await editor.getByRole("button", { name: "伦勃朗光", exact: true }).click();
    await editor.getByRole("slider").press("End");
    await editor.getByLabel("光线颜色", { exact: true }).fill("#3344cc");
    await editor.getByRole("switch", { name: "轮廓光", exact: true }).click();
    await editor.getByPlaceholder("简单描述你想要实现的打光效果，或者情绪风格").fill("柔和的蓝色氛围光");
    assert.equal(await copy(editor, value => value === prompt), prompt);
    report.copied_prompt = prompt;
    await page.screenshot({ path: resolve(output, "lighting-selected.png") });
    report.step = "原打光按钮只创建待生成节点";
    await editor.getByRole("button", { name: "生成打光效果", exact: true }).click();
    const idle = await graph(owner, document => document.nodes.length === 2 && document.nodes.some(node => node.metadata?.status === "idle" && node.metadata?.prompt === prompt));
    const prepared = idle.nodes.find(node => node.id !== source.id);
    report.idle_node_id = prepared.id;
    assert.equal(prepared.title, title);
    assert.equal(prepared.metadata.count, 1);
    assert.deepEqual(prepared.metadata.references, [source.metadata.storageKey]);
    assert.ok(!prepared.metadata.taskId && !prepared.metadata.storageKey);
    assert.deepEqual(idle.nodes.find(node => node.id === source.id), source);
    assert.equal(report.submissions.length, 0);
    owner = await fresh(owner, config.width);
    await page.locator(`[data-node-id="${prepared.id}"]`).waitFor();
    const restoredIdle = await graph(owner, document => document.nodes.length === 2);
    assert.deepEqual(restoredIdle.nodes.find(node => node.id === prepared.id), prepared);
    assert.equal(report.submissions.length, 0);
    report.idle_fresh_context_restored = true;
    await fit();
    await page.locator(`[data-node-id="${prepared.id}"]`).click();
    report.step = "原发送按钮提交单份与私人任务";
    const admitted = page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST" && response.ok());
    await page.getByRole("button", { name: "生成", exact: true }).click();
    const task = await read(await admitted);
    assert.equal(report.submissions.length, 1);
    const submitted = report.submissions[0];
    assert.equal(submitted.prompt, prompt);
    assert.equal(Number(submitted.input.config.count), 1);
    assert.equal(submitted.input.metadata.sourceNodeId, prepared.id);
    assert.equal(submitted.input.metadata.nodeId, prepared.id);
    assert.equal(submitted.input.referenceImages[0].storageKey, source.metadata.storageKey);
    report.task_id = task.id;
    report.target_node_id = prepared.id;
    await eventually(async () => read(await owner.request.get(`${origin}${tasks}/${task.id}`)), row => row.status === "running");
    await graph(owner, document => document.nodes.some(node => node.id === prepared.id && node.metadata?.taskId === task.id));
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
    if (config.scenario === "recovery") {
        owner = await fresh(owner, config.width);
        await page.locator(`[data-node-id="${prepared.id}"]`).waitFor();
        assert.equal((await read(await owner.request.get(`${origin}${tasks}/${task.id}`))).status, "running");
        assert.equal(report.submissions.length, 1);
        report.pending_fresh_context_restored = true;
    }
    await writeFile(config.imageReleaseFile, "browser-ready");
    report.step = "终态与原永久产物直接回填";
    const final = await graph(owner, document => document.nodes.length === 2 && document.nodes.some(node => node.id === prepared.id && node.metadata?.assetId && node.metadata?.storageKey));
    assert.deepEqual(final.nodes.find(node => node.id === source.id), source);
    const completed = await read(await owner.request.get(`${origin}${tasks}/${task.id}`));
    assert.equal(completed.status, "succeeded");
    const result = JSON.parse(completed.resultJson).images[0];
    assert.equal(final.nodes.find(node => node.id === prepared.id).metadata.storageKey, result.storageKey);
    assert.equal(final.connections.length, 1);
    assert.equal(final.connections[0].fromNodeId, source.id);
    assert.equal(final.connections[0].toNodeId, prepared.id);
    report.output_storage_key = result.storageKey;
    report.final_nodes = final.nodes;
    const shared = await owner.request.get(`${origin}${resources}/${result.storageKey.slice(9)}/file`);
    assert.equal((await member.request.get(`${origin}${resources}/${result.storageKey.slice(9)}/file`)).status(), 404);
    assert.ok(shared.ok());
    report.output_sha256 = createHash("sha256").update(await shared.body()).digest("hex");
    owner = await fresh(owner, 1440);
    await page.locator(`[data-node-id="${source.id}"]`).waitFor();
    await fit();
    await page.waitForFunction(id => [...document.querySelectorAll(`[data-node-id="${id}"] img`)].some(image => image.complete && image.naturalWidth === 37 && image.naturalHeight === 19), prepared.id);
    await page.mouse.move(5, 450);
    for (const node of [source, prepared]) {
        const bounds = await page.locator(`[data-node-id="${node.id}"]`).boundingBox();
        assert.ok(bounds && bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= 1440 && bounds.y + bounds.height <= 900);
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
    await Promise.all(observations);
    await browser?.close();
    await server.close();
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    process.stdout.write(JSON.stringify(report) + "\n");
}
