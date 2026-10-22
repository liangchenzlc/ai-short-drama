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
const defaults = { personSceneFusion: "deep", lightingFusion: "natural", skin: "natural", texture: "natural", sharpness: "standard" };
const prompt = [
    "@图片1",
    "请基于参考图片进行人物质感调节，只优化下列视觉属性：",
    "- 人景融合（深度融合）：深度融合人物与场景，细致统一边缘、环境色和空间层次",
    "- 光影融合（自然匹配）：让人物光向、色温与场景光线自然匹配",
    "- 皮肤（自然肤质）：保留自然肤质、毛孔和真实肤色过渡",
    "- 纹理（自然纹理）：保持服装、头发、皮肤和场景材质的自然纹理",
    "- 锐度（标准清晰）：保持标准清晰度，细节自然且不过度锐化",
    "必须保持原人物身份、五官、发型、服装、姿势、构图、场景和画面比例；不得新增或删除主体，不得添加文字、水印或标志。",
].join("\n");
const report = { step: "start", scenario: config.scenario, expected_prompt: prompt, page_errors: [], http_errors: [], uploads: 0, submissions: [], admissions: [], viewport_events: [], results: [] };
const observations = [];
let windowSequence = 0;
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(".runtime/vite-texture-browser"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
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
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    await authorize(context);
    return context;
}
async function eventually(readCurrent, accepted) {
    let current;
    for (let attempt = 0; attempt < 400; attempt++) {
        current = await readCurrent();
        if (accepted(current)) return current;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`质感调整未达到预期：${JSON.stringify(current)}`);
}
async function graph(context, accepted) {
    return eventually(async () => (await read(await context.request.get(`${origin}${path}/my-document`))).source_document, accepted);
}
function observe(nextPage) {
    const windowId = ++windowSequence;
    nextPage.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    nextPage.on("response", response => {
        const pathname = new URL(response.url()).pathname;
        if (response.status() >= 400) report.http_errors.push({ method: response.request().method(), path: pathname, status: response.status() });
        if (pathname === tasks && response.request().method() === "POST" && response.ok()) observations.push(response.json().then(body => report.admissions.push(body)));
        if (pathname === `${path}/viewport` || pathname === `${path}/my-document`) {
            const event = { window: windowId, step: report.step, time: Date.now(), method: response.request().method(), path: pathname, status: response.status() };
            observations.push(response.json().then(body => report.viewport_events.push({ ...event, viewport: body.viewport ?? body.source_document?.viewport, error: body.error?.code })).catch(error => report.viewport_events.push({ ...event, unavailable: error.message })));
        }
    });
    nextPage.on("request", request => {
        const pathname = new URL(request.url()).pathname;
        if (pathname === resources && request.method() === "POST") report.uploads++;
        if (pathname === tasks && request.method() === "POST") report.submissions.push(request.postDataJSON());
        if (pathname === `${path}/viewport` && request.method() === "PUT") report.viewport_events.push({ window: windowId, step: report.step, time: Date.now(), method: "PUT", payload: request.postDataJSON() });
    });
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
async function checkHiddenTexturePanel(id) {
    await page.locator(`[data-node-id="${id}"]`).click();
    const trigger = page.getByRole("button", { name: "打开质感调整面板", exact: true, includeHidden: true });
    await trigger.waitFor({ state: "attached" });
    assert.equal(await trigger.isVisible(), false);
    assert.equal(await trigger.evaluate(element => getComputedStyle(element.closest(".canvas-node-composer-header")).display), "none");
    assert.equal(await page.getByRole("radiogroup").count(), 0);
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
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: "texture-source-pattern.png", mimeType: "image/png", buffer: Buffer.from(config.image, "base64") });
    const original = await graph(owner, document => document.nodes.length === 1 && document.nodes[0].metadata?.assetId);
    const source = original.nodes[0];
    report.source_node = source;
    report.step = "原人像菜单创建空质感节点";
    const sourceElement = page.locator(`[data-node-id="${source.id}"]`);
    await sourceElement.click();
    await sourceElement.hover();
    if (config.width >= 640) {
        const portrait = page.getByRole("toolbar", { name: "节点快捷工具" }).getByRole("button", { name: "人像调整", exact: true });
        await portrait.click();
        await page.getByRole("menuitem", { name: "表情调整", exact: true }).waitFor();
        assert.equal(await page.getByRole("menuitem", { name: "质感调整", exact: true }).count(), 0);
        await page.waitForFunction(() => [...document.querySelectorAll(".ant-dropdown:not(.ant-dropdown-hidden)")].some(element => Number(getComputedStyle(element).opacity) >= 0.99 && getComputedStyle(element).animationName === "none"));
        await page.screenshot({ path: resolve(output, "texture-desktop-hidden.png") });
        await portrait.click();
        report.desktop_creation_hidden_like_source = true;
        await page.setViewportSize({ width: 600, height: 900 });
        await sourceElement.hover();
    }
    await page.getByRole("toolbar", { name: "节点快捷工具" }).getByRole("button", { name: "工具", exact: true }).click();
    await page.getByRole("menuitem", { name: "质感调整", exact: true }).click();
    const idle = await graph(owner, document => document.nodes.length === 2 && document.nodes.some(node => node.metadata?.portraitTexture));
    const prepared = idle.nodes.find(node => node.id !== source.id);
    report.idle_node_id = prepared.id;
    assert.equal(prepared.title, `${source.title} · 人物质感`);
    assert.deepEqual(prepared.position, { x: source.position.x + source.width + 96, y: source.position.y });
    assert.equal(prepared.width, source.width);
    assert.equal(prepared.height, source.height);
    assert.equal(prepared.metadata.prompt, "@图片1");
    assert.equal(prepared.metadata.composerContent, "@图片1");
    assert.equal(prepared.metadata.generationType, "edit");
    assert.deepEqual(prepared.metadata.portraitTexture, defaults);
    assert.ok(!prepared.metadata.taskId && !prepared.metadata.storageKey && !prepared.metadata.content);
    assert.deepEqual(idle.nodes.find(node => node.id === source.id), source);
    assert.equal(idle.connections.length, 1);
    assert.equal(idle.connections[0].fromNodeId, source.id);
    assert.equal(idle.connections[0].toNodeId, prepared.id);
    assert.equal(report.submissions.length, 0);
    if (config.width >= 640) await page.setViewportSize({ width: config.width, height: 900 });
    await fit();
    report.step = "原五组默认值与源隐藏标题栏";
    await checkHiddenTexturePanel(prepared.id);
    await page.screenshot({ path: resolve(output, "texture-defaults.png") });
    report.texture_controls_hidden_like_source = true;
    const advanced = page.getByRole("button", { name: /^高级设置：/ });
    assert.ok((await advanced.getAttribute("aria-label")).includes("1 张"));
    await advanced.click();
    await page.locator(".canvas-image-settings-popover").getByRole("button", { name: String(config.count), exact: true }).click();
    await advanced.click();
    assert.ok((await advanced.getAttribute("aria-label")).includes(`${config.count} 张`));
    const selectedIdle = await graph(owner, document => document.nodes.some(node => node.id === prepared.id && Object.entries(defaults).every(([key, value]) => node.metadata?.portraitTexture?.[key] === value) && Number(node.metadata?.count) === config.count));
    const savedIdle = selectedIdle.nodes.find(node => node.id === prepared.id);
    assert.deepEqual(selectedIdle.nodes.find(node => node.id === source.id), source);
    assert.equal(report.uploads, 1);
    assert.equal(report.submissions.length, 0);
    owner = await fresh(owner, config.width);
    await page.locator(`[data-node-id="${prepared.id}"]`).waitFor();
    assert.deepEqual((await graph(owner, document => document.nodes.length === 2)).nodes.find(node => node.id === prepared.id), savedIdle);
    await fit();
    await checkHiddenTexturePanel(prepared.id);
    report.idle_fresh_context_restored = true;
    report.step = "原发送按钮与每份独立任务";
    await page.getByRole("button", { name: "生成", exact: true }).click();
    await eventually(() => report.admissions, rows => rows.length === config.count);
    assert.equal(report.submissions.length, config.count);
    const operations = new Set();
    for (const submitted of report.submissions) {
        assert.equal(submitted.prompt, prompt);
        assert.equal(submitted.input.prompt, prompt);
        assert.equal(Number(submitted.input.config.count), 1);
        assert.equal(submitted.input.metadata.sourceNodeId, prepared.id);
        assert.deepEqual(submitted.input.referenceImages.map(image => image.storageKey), [source.metadata.storageKey]);
        operations.add(submitted.input.metadata.clientOperationId);
    }
    assert.equal(operations.size, config.count);
    const pending = await graph(owner, document => document.nodes.filter(node => node.metadata?.taskId).length === config.count);
    const targetIds = report.submissions.map(row => row.input.metadata.nodeId);
    const root = pending.nodes.find(node => node.id === prepared.id);
    assert.deepEqual(root.metadata.portraitTexture, defaults);
    if (config.count > 1) {
        assert.equal(root.metadata.isBatchRoot, true);
        assert.deepEqual(new Set(root.metadata.batchChildIds), new Set(targetIds));
        assert.equal(pending.nodes.length, config.count + 2);
        assert.equal(pending.connections.length, config.count + 1);
    } else assert.deepEqual(targetIds, [prepared.id]);
    for (const task of report.admissions) assert.equal((await member.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
    await eventually(async () => Promise.all(report.admissions.map(task => readTask(owner, task.id))), rows => rows.some(row => row.status === "running"));
    if (config.scenario !== "normal") {
        owner = await fresh(owner, config.width);
        await page.locator(`[data-node-id="${prepared.id}"]`).waitFor();
        assert.deepEqual((await graph(owner, document => document.nodes.length === pending.nodes.length)).nodes.find(node => node.id === prepared.id).metadata.portraitTexture, defaults);
        assert.equal(report.submissions.length, config.count);
        report.pending_fresh_context_restored = true;
    }
    await writeFile(config.imageReleaseFile, "browser-ready");
    report.step = "永久结果、批次代表图与共享权限";
    const final = await graph(owner, document => targetIds.every(id => document.nodes.some(node => node.id === id && node.metadata?.assetId && node.metadata?.storageKey)) && document.nodes.some(node => node.id === prepared.id && node.metadata?.storageKey));
    assert.deepEqual(final.nodes.find(node => node.id === source.id), source);
    assert.equal(final.nodes.length, config.count > 1 ? config.count + 2 : 2);
    assert.equal(final.connections.length, config.count > 1 ? config.count + 1 : 1);
    for (const nodeId of targetIds) {
        const node = final.nodes.find(item => item.id === nodeId);
        const task = await readTask(owner, node.metadata.taskId);
        assert.equal(task.status, "succeeded");
        const result = JSON.parse(task.resultJson).images[0];
        assert.equal(node.metadata.storageKey, result.storageKey);
        const shared = await owner.request.get(`${origin}${resources}/${result.storageKey.slice(9)}/file`);
        assert.equal((await member.request.get(`${origin}${resources}/${result.storageKey.slice(9)}/file`)).status(), 404);
        assert.ok(shared.ok());
        report.results.push({ node_id: nodeId, task_id: task.id, storage_key: result.storageKey, sha256: createHash("sha256").update(await shared.body()).digest("hex") });
    }
    const finalRoot = final.nodes.find(node => node.id === prepared.id);
    assert.deepEqual(finalRoot.metadata.portraitTexture, defaults);
    if (config.count > 1) {
        const primary = final.nodes.find(node => node.id === finalRoot.metadata.primaryImageId);
        assert.ok(primary);
        assert.equal(finalRoot.metadata.storageKey, primary.metadata.storageKey);
        assert.equal(finalRoot.metadata.batchFailedCount, 0);
    }
    owner = await fresh(owner, 1440);
    await page.locator(`[data-node-id="${source.id}"]`).waitFor();
    await fit();
    for (const nodeId of targetIds) await page.waitForFunction(id => [...document.querySelectorAll(`[data-node-id="${id}"] img`)].some(image => image.complete && image.naturalWidth === 37 && image.naturalHeight === 19), nodeId);
    await page.mouse.move(5, 450);
    await page.screenshot({ path: resolve(output, "texture-fresh-restored.png") });
    assert.equal(report.submissions.length, config.count);
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

async function readTask(context, id) {
    return read(await context.request.get(`${origin}${tasks}/${id}`));
}
