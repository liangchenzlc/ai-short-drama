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
const labels = ["欣喜若狂", "兴高采烈", "惊喜", "震惊", "惊恐", "开怀", "期待", "专注", "警觉", "紧张", "温柔", "浅然莞尔", "中性克制", "隐忍", "疏离", "安心", "释然", "疲惫", "失落", "悲伤", "满足", "平静", "冷淡", "隐忍心伤", "绝望"];
const report = { step: "start", scenario: config.scenario, page_errors: [], http_errors: [], uploads: 0, submissions: [], model_loaded: false, media_requests: [] };
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(".runtime/vite-emotion-browser"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
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
    for (let attempt = 0; attempt < 300; attempt++) {
        current = await readCurrent();
        if (accepted(current)) return current;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`表情编辑未达到预期：${JSON.stringify(current)}`);
}
async function graph(context, accepted) {
    return eventually(async () => (await read(await context.request.get(`${origin}${path}/my-document`))).source_document, accepted);
}
function observe(nextPage) {
    nextPage.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    nextPage.on("response", response => {
        const pathname = new URL(response.url()).pathname;
        if (response.status() >= 400) report.http_errors.push({ method: response.request().method(), path: pathname, status: response.status() });
        if (/face-detector|blaze-face|mediapipe|facecap/.test(pathname)) report.media_requests.push({ path: pathname, status: response.status() });
        if (pathname.endsWith("/canvas/models/facecap.glb") && response.ok()) report.model_loaded = true;
    });
    nextPage.on("request", request => {
        const pathname = new URL(request.url()).pathname;
        if (pathname === resources && request.method() === "POST") report.uploads++;
        if (pathname === tasks && request.method() === "POST") report.submissions.push(request.postDataJSON());
    });
}
async function stableViewport() {
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
async function fit() {
    const focus = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focus.isVisible()) await focus.getByTitle("适应画布", { exact: true }).click();
    else await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await stableViewport();
}
async function fresh(owner, width) {
    await stableViewport();
    const live = await page.locator("[data-canvas-viewport]").evaluate(viewport => ({ x: parseFloat(viewport.style.getPropertyValue("--canvas-live-x")), y: parseFloat(viewport.style.getPropertyValue("--canvas-live-y")), k: parseFloat(viewport.style.getPropertyValue("--canvas-live-scale")) }));
    await graph(owner, document => Object.entries(live).every(([key, value]) => document.viewport[key] === value));
    const cookies = (await owner.storageState()).cookies;
    await owner.close();
    const context = await browser.newContext({ storageState: { cookies, origins: [] }, viewport: { width, height: 900 }, reducedMotion: "no-preference" });
    await authorize(context);
    page = await context.newPage();
    observe(page);
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    return context;
}
async function openEditor(source) {
    const node = page.locator(`[data-node-id="${source.id}"]`);
    await node.click();
    await node.hover();
    await page.getByRole("toolbar", { name: "节点快捷工具" }).getByRole("button", { name: page.viewportSize().width < 640 ? "工具" : "人像调整", exact: true }).click();
    await page.getByRole("menuitem", { name: "表情调整", exact: true }).click();
    await page.getByText("未识别到清晰人脸，请手动框选", { exact: true }).waitFor();
    await page.getByRole("button", { name: "手动框选", exact: true }).click();
}
async function manualBox(x, y, width, height) {
    const overlay = page.locator("[data-canvas-no-zoom].cursor-crosshair").filter({ has: page.locator(`svg[viewBox="0 0 ${config.imageWidth} ${config.imageHeight}"]`) });
    await overlay.waitFor();
    const bounds = await overlay.boundingBox();
    assert.ok(bounds);
    const sx = bounds.width / config.imageWidth;
    const sy = bounds.height / config.imageHeight;
    await page.mouse.move(bounds.x + x * sx, bounds.y + y * sy);
    await page.mouse.down();
    await page.mouse.move(bounds.x + (x + width) * sx, bounds.y + (y + height) * sy, { steps: 8 });
    await page.mouse.up();
}
async function selected(label) {
    await eventually(() => page.getByRole("slider", { name: "情绪强度", exact: true }).getAttribute("aria-valuetext"), value => value === label);
}
async function stablePreview(label) {
    await page.waitForFunction(label => {
        const captions = [...document.querySelectorAll("[data-canvas-node-panel] span")].filter(element => element.textContent.trim() === label || element.textContent.trim() === `实时预览 · ${label}`);
        return captions.length === 2 && captions.every(element => {
            const style = getComputedStyle(element);
            return style.opacity === "1" && (style.filter === "none" || style.filter === "blur(0px)");
        });
    }, label);
    const head = page.locator("[data-canvas-node-panel] canvas");
    await eventually(async () => {
        const screenshot = await head.screenshot();
        return page.evaluate(async encoded => {
            const bitmap = await createImageBitmap(await (await fetch(`data:image/png;base64,${encoded}`)).blob());
            try {
                const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
                const context = canvas.getContext("2d");
                context.drawImage(bitmap, 0, 0);
                const pixels = context.getImageData(0, 0, bitmap.width, bitmap.height).data;
                let visible = 0;
                for (let i = 0; i < pixels.length; i += 4) if (pixels[i] > 60 && pixels[i + 1] > 60 && pixels[i + 2] > 60) visible++;
                return visible;
            } finally { bitmap.close(); }
        }, screenshot.toString("base64"));
    }, visible => visible > 1000);
    await stableViewport();
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
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: "emotion-source-pattern.png", mimeType: "image/png", buffer: Buffer.from(config.image, "base64") });
    const original = await graph(owner, document => document.nodes.length === 1 && document.nodes[0].metadata?.assetId);
    const source = original.nodes[0];
    report.source_node = source;
    report.step = "原自动检测无脸与手工框选边界";
    await openEditor(source);
    report.actual_no_face_detection = true;
    await manualBox(80, 45, 3, 3);
    assert.equal(await page.getByRole("slider", { name: "情绪强度", exact: true }).count(), 0);
    await manualBox(80, 45, 60, 50);
    await selected("中性克制");
    await eventually(() => report.model_loaded, value => value);
    report.step = "25预设、键盘边界和实际鼠标拖拽";
    const pad = page.getByRole("slider", { name: "情绪强度", exact: true });
    for (const label of labels) {
        await pad.getByRole("button", { name: label, exact: true }).click();
        await selected(label);
    }
    await pad.getByRole("button", { name: "中性克制", exact: true }).click();
    for (let i = 0; i < 3; i++) await pad.press("ArrowLeft");
    await selected("温柔");
    for (let i = 0; i < 5; i++) await pad.press("ArrowRight");
    await selected("疏离");
    for (let i = 0; i < 4; i++) await pad.press("ArrowUp");
    await selected("惊恐");
    for (let i = 0; i < 5; i++) await pad.press("ArrowDown");
    await selected("绝望");
    const bounds = await pad.boundingBox();
    assert.ok(bounds);
    await page.mouse.move(bounds.x + bounds.width * 0.95, bounds.y + bounds.height * 0.95);
    await page.mouse.down();
    await page.mouse.move(bounds.x + bounds.width * 0.05, bounds.y + bounds.height * 0.05, { steps: 10 });
    await page.mouse.up();
    await selected("欣喜若狂");
    await pad.getByRole("button", { name: "中性克制", exact: true }).click();
    await stablePreview("中性克制");
    await page.screenshot({ path: resolve(output, "emotion-defaults.png") });
    await page.getByRole("button", { name: "关闭情绪调节", exact: true }).click();
    await pad.waitFor({ state: "hidden" });
    assert.deepEqual((await graph(owner, document => document.nodes.length === 1)).nodes, original.nodes);
    assert.equal(report.uploads, 1);
    assert.equal(report.submissions.length, 0);
    report.controls_cancel_kept_source = true;
    report.step = "重新打开默认与两个人物切换";
    await openEditor(source);
    await manualBox(80, 45, 60, 50);
    await selected("中性克制");
    await page.getByRole("button", { name: "手动框选", exact: true }).click();
    await manualBox(225, 50, 55, 55);
    await selected("中性克制");
    await page.getByRole("button", { name: "角色1", exact: true }).click();
    await page.getByRole("button", { name: "角色2", exact: true }).click();
    await page.getByRole("slider", { name: "情绪强度", exact: true }).getByRole("button", { name: "期待", exact: true }).click();
    await selected("期待");
    await stablePreview("期待");
    await page.screenshot({ path: resolve(output, "emotion-selected.png") });
    report.step = "单份多参考图编辑受理与私人运行中恢复";
    const admitted = page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST" && response.ok());
    await page.locator("[data-canvas-node-panel]").getByRole("button", { name: "生成", exact: true }).click();
    const task = await read(await admitted);
    assert.equal(report.submissions.length, 1);
    const submitted = report.submissions[0];
    assert.equal(Number(submitted.input.config.count), 1);
    assert.equal(submitted.input.config.quality, "high");
    assert.equal(submitted.input.metadata.edit, "emotion");
    assert.equal(submitted.input.metadata.sourceNodeId, source.id);
    assert.equal(submitted.input.referenceImages.length, 2);
    assert.equal(Boolean(submitted.input.mask), config.maskSupported);
    const targetId = submitted.input.metadata.nodeId;
    report.task_id = task.id;
    report.target_node_id = targetId;
    const pending = await graph(owner, document => document.nodes.length === 2 && document.nodes.some(node => node.id === targetId && node.metadata?.taskId === task.id));
    const target = pending.nodes.find(node => node.id === targetId);
    assert.equal(target.title, "角色2 · 期待");
    assert.equal(target.metadata.emotionEdit.faceBox.source, "manual");
    assert.equal(target.metadata.emotionEdit.characterName, "角色2");
    assert.equal(target.metadata.emotionEdit.presetId, "emotion-1-1");
    assert.equal(target.metadata.emotionEdit.editMode, config.maskSupported ? "provider-mask" : "local-composite");
    report.emotion_edit = target.metadata.emotionEdit;
    assert.ok(submitted.prompt.startsWith('仅修改第一张输入图中“角色2”脸部的表情，其他像素保持源图一致。\n目标情绪：期待；表情要求：期待而兴奋，表情明亮。'));
    await eventually(async () => read(await owner.request.get(`${origin}${tasks}/${task.id}`)), row => row.status === "running");
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
    if (config.scenario === "recovery") {
        owner = await fresh(owner, config.width);
        await page.locator(`[data-node-id="${targetId}"]`).waitFor();
        assert.equal((await read(await owner.request.get(`${origin}${tasks}/${task.id}`))).status, "running");
        report.pending_fresh_context_restored = true;
    }
    await writeFile(config.imageReleaseFile, "browser-ready");
    report.step = "原局部羽化合成与永久派生图片";
    const final = await graph(owner, document => document.nodes.length === 2 && document.nodes.some(node => node.id === targetId && node.metadata?.assetId && node.metadata?.storageKey && node.metadata?.status === "success"));
    assert.deepEqual(final.nodes.find(node => node.id === source.id), source);
    const completed = await read(await owner.request.get(`${origin}${tasks}/${task.id}`));
    assert.equal(completed.status, "succeeded");
    const providerResult = JSON.parse(completed.resultJson).images[0];
    const derived = final.nodes.find(node => node.id === targetId);
    assert.notEqual(derived.metadata.storageKey, providerResult.storageKey);
    assert.equal(derived.metadata.naturalWidth, config.imageWidth);
    assert.equal(derived.metadata.naturalHeight, config.imageHeight);
    assert.equal(final.connections.length, 1);
    assert.equal(final.connections[0].fromNodeId, source.id);
    assert.equal(final.connections[0].toNodeId, targetId);
    report.provider_storage_key = providerResult.storageKey;
    report.output_storage_key = derived.metadata.storageKey;
    report.final_nodes = final.nodes;
    const shared = await owner.request.get(`${origin}${resources}/${derived.metadata.storageKey.slice(9)}/file`);
    assert.equal((await member.request.get(`${origin}${resources}/${derived.metadata.storageKey.slice(9)}/file`)).status(), 404);
    assert.ok(shared.ok());
    report.output_sha256 = createHash("sha256").update(await shared.body()).digest("hex");
    owner = await fresh(owner, 1440);
    await page.locator(`[data-node-id="${source.id}"]`).waitFor();
    await fit();
    await page.waitForFunction(({ id, width, height }) => [...document.querySelectorAll(`[data-node-id="${id}"] img`)].some(image => image.complete && image.naturalWidth === width && image.naturalHeight === height), { id: targetId, width: config.imageWidth, height: config.imageHeight });
    const restored = await graph(owner, document => document.nodes.some(node => node.id === targetId && node.metadata?.storageKey === derived.metadata.storageKey));
    assert.equal(restored.connections.length, 1);
    await page.mouse.move(5, 450);
    await page.screenshot({ path: resolve(output, "fresh-restored.png") });
    assert.equal(report.submissions.length, 1);
    assert.equal(report.uploads, config.maskSupported ? 5 : 4);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    report.final_fresh_context_restored = true;
    report.step = "complete";
} catch (error) {
    report.failure = error.stack ?? error.message;
    if (page) {
        report.body = await page.locator("body").innerText().catch(() => "");
        report.editor_dom = await page.evaluate(() => ({ text: document.body.textContent, overlays: [...document.querySelectorAll("[data-canvas-no-zoom]")].map(element => element.outerHTML.slice(0, 1600)), canvas: document.querySelector("[data-canvas-viewport]")?.getAttribute("data-canvas-node-dragging") })).catch(() => null);
        report.workers = page.workers().map(worker => worker.url());
        await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    }
    process.exitCode = 1;
} finally {
    await browser?.close();
    await server.close();
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    process.stdout.write(JSON.stringify(report) + "\n");
}
