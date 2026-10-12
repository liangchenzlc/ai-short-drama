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
assert.equal(config.count, 1);
assert.equal(config.maskSupported, false);
const origin = "http://127.0.0.1:" + port;
const resources = "/api/v1/canvas-runtime/resources";
const tasks = "/api/v1/canvas-runtime/tasks";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const output = resolve(config.outputDirectory);
const labels = ["欣喜若狂", "兴高采烈", "惊喜", "震惊", "惊恐", "开怀", "期待", "专注", "警觉", "紧张", "温柔", "浅然莞尔", "中性克制", "隐忍", "疏离", "安心", "释然", "疲惫", "失落", "悲伤", "满足", "平静", "冷淡", "隐忍心伤", "绝望"];
const report = { scope: "real Agnes original manual emotion UI without provider mask", width: config.width, scenario: "recovery", step: "start", page_errors: [], http_errors: [], observation_errors: [], uploads: 0, submissions: [], submission_keys: [], admissions: [], model_loaded: false, media_requests: [], detection_states: [], source_views: [], viewport_events: [], pre_close_viewport_acks: [], restored_viewport_acks: [], node_geometry_save_acks: [], result_geometry: {}, node_bind_operations: 0 };
const observations = [];
let windowSequence = 0;
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(output, "node_modules/.vite"), optimizeDeps: { include: ["@mediapipe/tasks-vision"] }, server: { host: "127.0.0.1", port, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
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
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
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
    assert.fail(`表情编辑未达到预期：${JSON.stringify(current)}`);
}

async function graph(context, accepted, limit) {
    return eventually(async () => (await read(await context.request.get(`${origin}${path}/my-document`))).source_document, accepted, limit);
}

async function readTask(owner, id) {
    return read(await owner.request.get(`${origin}${tasks}/${id}`));
}

function nodeGeometry(node) {
    return { position: { x: node.position.x, y: node.position.y }, width: node.width, height: node.height };
}

function sameGeometry(node, expected) {
    return node?.position?.x === expected.position.x && node.position.y === expected.position.y && node.width === expected.width && node.height === expected.height;
}

function nodeCenter(node) {
    return { x: node.position.x + node.width / 2, y: node.position.y + node.height / 2 };
}

async function acknowledgeNodeGeometry(owner, nodeId, expected, phase, accepted, { decoded = false, saveAck = false } = {}) {
    const stage = report.result_geometry[phase] = { expected };
    const element = page.locator(`[data-node-id="${nodeId}"]`);
    await element.waitFor();
    if (decoded) {
        const image = element.locator("img").first();
        await image.waitFor({ state: "visible" });
        await page.waitForFunction(({ id, width, height }) => [...document.querySelectorAll(`[data-node-id="${id}"] img`)].some(image => image.complete && image.naturalWidth === width && image.naturalHeight === height), { id: nodeId, ...config.sourceDimensions });
        stage.image = await image.evaluate(async image => { await image.decode(); return { width: image.naturalWidth, height: image.naturalHeight }; });
        assert.deepEqual(stage.image, config.sourceDimensions);
    }
    stage.dom = await eventually(() => element.evaluate(element => {
        const transform = new DOMMatrixReadOnly(element.style.transform);
        return { position: { x: transform.m41, y: transform.m42 }, width: parseFloat(element.style.width), height: parseFloat(element.style.height) };
    }), current => sameGeometry(current, expected));
    assert.deepEqual(stage.dom, expected);
    const document = await graph(owner, document => document.nodes.some(node => node.id === nodeId && accepted(node) && sameGeometry(node, expected)));
    const node = document.nodes.find(node => node.id === nodeId);
    stage.stored = nodeGeometry(node);
    stage.center = nodeCenter(node);
    assert.deepEqual(stage.stored, expected);
    if (saveAck) {
        stage.save_ack = await eventually(() => report.node_geometry_save_acks.find(ack => ack.window === windowSequence && ack.node_id === nodeId && ack.task_id === node.metadata?.taskId && ack.state === node.metadata?.status && ack.storage_key === (node.metadata?.storageKey ?? null) && sameGeometry(ack.geometry, expected)), ack => Boolean(ack));
        assert.match(stage.save_ack.row_version, /^[1-9][0-9]*$/);
    }
    return { node, document };
}

function observe(nextPage) {
    const windowId = ++windowSequence;
    nextPage.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    nextPage.on("response", response => {
        const pathname = new URL(response.url()).pathname;
        if (response.status() >= 400) report.http_errors.push({ method: response.request().method(), path: pathname, status: response.status() });
        if (/face-detector|blaze-face|mediapipe|facecap/.test(pathname)) report.media_requests.push({ path: pathname, status: response.status() });
        if (pathname.endsWith("/canvas/models/facecap.glb") && response.ok()) report.model_loaded = true;
        if (pathname === tasks && response.request().method() === "POST" && response.ok()) observations.push(response.json().then(body => report.admissions.push(body)).catch(error => report.observation_errors.push({ path: pathname, message: error.message })));
        if (pathname === `${path}/commits` && response.request().method() === "POST" && response.ok()) {
            const node = response.request().postDataJSON()?.source_document?.nodes?.find(node => node.id === report.target_node_id);
            if (node) observations.push(response.json().then(body => report.node_geometry_save_acks.push({ window: windowId, step: report.step, time: Date.now(), node_id: node.id, task_id: node.metadata?.taskId, state: node.metadata?.status, storage_key: node.metadata?.storageKey ?? null, row_version: body.row_version, geometry: nodeGeometry(node) })).catch(error => report.observation_errors.push({ path: pathname, message: error.message })));
        }
        if (pathname === `${path}/viewport` || pathname === `${path}/my-document`) {
            const event = { window: windowId, step: report.step, time: Date.now(), method: response.request().method(), path: pathname, status: response.status() };
            observations.push(response.json().then(body => report.viewport_events.push({ ...event, viewport: body.viewport ?? body.source_document?.viewport, error: body.error?.code })).catch(error => report.viewport_events.push({ ...event, unavailable: error.message })));
        }
    });
    nextPage.on("request", request => {
        const pathname = new URL(request.url()).pathname;
        if (pathname === resources && request.method() === "POST") report.uploads++;
        if (pathname === tasks && request.method() === "POST") {
            report.submissions.push(request.postDataJSON());
            report.submission_keys.push(request.headers()["idempotency-key"] ?? null);
        }
        if (pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && request.method() === "POST") report.node_bind_operations++;
        if (pathname === `${path}/viewport` && request.method() === "PUT") report.viewport_events.push({ window: windowId, step: report.step, time: Date.now(), method: "PUT", payload: request.postDataJSON() });
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
    if (await focus.isVisible()) await focus.getByRole("button", { name: "退出专注模式", exact: true }).press("Control+0");
    else await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await page.evaluate(() => document.fonts.ready);
    await stableViewport();
}

async function fresh(owner) {
    function float64Ordinal(value) {
        if (!Number.isFinite(value)) return null;
        const view = new DataView(new ArrayBuffer(8));
        view.setFloat64(0, value);
        const bits = view.getBigUint64(0);
        const sign = 1n << 63n;
        // 相邻编码严格对应一个 Float64 值，包含 subnormal 和 normal 的边界。
        return bits >= sign ? -(bits - sign) : bits;
    }
    function ulpDistance(actual, expected) {
        const left = float64Ordinal(actual);
        const right = float64Ordinal(expected);
        if (left === null || right === null) return null;
        const distance = left - right;
        return distance < 0n ? -distance : distance;
    }
    const keys = ["x", "y", "k"];
    const equivalent = (actual, expected) => actual && keys.every(key => {
        const distance = ulpDistance(actual[key], expected[key]);
        return distance !== null && distance <= 1n;
    });
    const difference = (actual, expected) => Object.fromEntries(keys.map(key => [key, actual[key] - expected[key]]));
    const ulps = (actual, expected) => Object.fromEntries(keys.map(key => [key, ulpDistance(actual[key], expected[key])?.toString() ?? null]));
    await stableViewport();
    const live = await page.locator("[data-canvas-viewport]").evaluate(viewport => ({ x: parseFloat(viewport.style.getPropertyValue("--canvas-live-x")), y: parseFloat(viewport.style.getPropertyValue("--canvas-live-y")), k: parseFloat(viewport.style.getPropertyValue("--canvas-live-scale")) }));
    assert.ok(Object.values(live).every(Number.isFinite));
    const acknowledged = await graph(owner, document => equivalent(document.viewport, live));
    const stored = acknowledged.viewport;
    report.pre_close_viewport_acks.push({ window: windowSequence, step: report.step, live, stored, difference: difference(stored, live), ulp_distance: ulps(stored, live) });
    const cookies = (await owner.storageState()).cookies;
    await owner.close();
    const context = await browser.newContext({ storageState: { cookies, origins: [] }, viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference" });
    await authorize(context);
    page = await context.newPage();
    observe(page);
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.waitForFunction(expected => {
        const viewport = document.querySelector("[data-canvas-viewport]");
        if (!viewport) return false;
        return [["x", "--canvas-live-x"], ["y", "--canvas-live-y"], ["k", "--canvas-live-scale"]].every(([key, property]) => parseFloat(viewport.style.getPropertyValue(property)) === expected[key]);
    }, stored);
    const actual = await page.locator("[data-canvas-viewport]").evaluate(viewport => ({ x: parseFloat(viewport.style.getPropertyValue("--canvas-live-x")), y: parseFloat(viewport.style.getPropertyValue("--canvas-live-y")), k: parseFloat(viewport.style.getPropertyValue("--canvas-live-scale")) }));
    report.restored_viewport_acks.push({ window: windowSequence, step: report.step, live, expected: stored, actual, difference: difference(actual, stored), ulp_distance: ulps(actual, stored) });
    assert.ok(keys.every(key => actual[key] === stored[key]), "新窗口视口必须精确恢复持久化值");
    return context;
}

function expectedPrompt(faceBox, editRegion) {
    const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
    const x = clamp(Math.round(faceBox.x - editRegion.x), 0, Math.max(0, editRegion.width - 1));
    const y = clamp(Math.round(faceBox.y - editRegion.y), 0, Math.max(0, editRegion.height - 1));
    const width = Math.max(1, Math.min(editRegion.width - x, Math.round(faceBox.width)));
    const height = Math.max(1, Math.min(editRegion.height - y, Math.round(faceBox.height)));
    const centerX = Math.round(((x + width / 2) / Math.max(1, editRegion.width)) * 100);
    const centerY = Math.round(((y + height / 2) / Math.max(1, editRegion.height)) * 100);
    return [
        "仅修改第一张输入图中“角色1”脸部的表情，其他像素保持源图一致。",
        "目标情绪：欣喜若狂；表情要求：自然、明亮的露齿笑，嘴角对称上扬，脸颊自然抬起，眼角有轻微笑纹。情绪强度通过眉眼、嘴角和脸颊的肌肉张力表达，不要通过夸大嘴巴或重绘整张脸表达。",
        "第一张输入图是唯一编辑目标，第二张输入图仅用于核对同一人物身份，不得复制第二张图的构图、背景或光线。",
        `目标人脸框（相对于第一张裁切输入图）：x=${x}px，y=${y}px，width=${width}px，height=${height}px；人脸中心约位于裁切图的 ${centerX}% 横向、${centerY}% 纵向。最终仅将该人脸周围的椭圆区域融合回原图。`,
        "如果第一张输入图中出现其他人脸，其他人脸全部视为不可编辑背景；只允许修改目标人脸框及其邻近表情区域。",
        "只改变眉眼开合、眼角、嘴角、脸颊和口腔内部的表情细节；保持眼睛大小与方向、嘴裂宽度、牙齿数量大小排列、嘴唇厚度、下巴轮廓和脸型自然且与原图一致。",
        "输入图已裁切到目标人物头部区域；只重绘目标人脸及其表情相关细节，不要生成或修改裁切图中的其他内容。",
        "严格保持人物身份、五官比例、肤色、发型、发丝、耳朵、配饰、服装、姿势、头部朝向、镜头、景深、光线、背景及画面其他人物不变；不要重绘眼镜、帽子或遮挡物。",
        "禁止夸张卡通笑、嘴巴过大或拉宽、牙齿像整齐白墙、露出不自然牙龈、眼睛变形或眯成线、脸颊鼓包、塑料磨皮、重复五官，以及任何身份漂移。",
    ].join("\n");
}

async function freezeExpectedPrompt() {
    const face = await page.getByRole("button", { name: "选择角色1", exact: true }).evaluate((element, dimensions) => ({
        x: parseFloat(element.style.left) * dimensions.width / 100,
        y: parseFloat(element.style.top) * dimensions.height / 100,
        width: parseFloat(element.style.width) * dimensions.width / 100,
        height: parseFloat(element.style.height) * dimensions.height / 100,
    }), config.sourceDimensions);
    assert.ok(Object.values(face).every(Number.isFinite));
    const left = Math.floor(Math.max(0, face.x - face.width * 0.85));
    const top = Math.floor(Math.max(0, face.y - face.height * 0.75));
    const right = Math.ceil(Math.min(config.sourceDimensions.width, face.x + face.width * 1.85));
    const bottom = Math.ceil(Math.min(config.sourceDimensions.height, face.y + face.height * 2));
    const region = { x: left, y: top, width: Math.max(1, right - left), height: Math.max(1, bottom - top) };
    report.expected_face_box = face;
    report.expected_edit_region = region;
    report.expected_prompt = expectedPrompt(face, region);
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

async function openEditor(source) {
    const node = page.locator(`[data-node-id="${source.id}"]`);
    await node.click();
    await node.hover();
    await page.getByRole("toolbar", { name: "节点快捷工具" }).getByRole("button", { name: config.width < 640 ? "工具" : "人像调整", exact: true }).click();
    await page.getByRole("menuitem", { name: "表情调整", exact: true }).click();
    const manual = page.getByRole("button", { name: "手动框选", exact: true });
    await manual.waitFor({ state: "visible", timeout: 60000 });
    const text = await page.locator("body").innerText();
    report.detection_states.push({ detected_face_count: await page.getByRole("button", { name: "选择此人脸", exact: true }).count(), actual_notice: text.split("\n").find(line => /识别到.*张人脸|未识别到清晰人脸|人脸识别.*手动框选|精简版未包含自动人脸识别/.test(line)) ?? "manual selection available" });
    await manual.click();
}

async function manualBox(box) {
    const overlay = page.locator("[data-canvas-no-zoom].cursor-crosshair").filter({ has: page.locator(`svg[viewBox="0 0 ${config.sourceDimensions.width} ${config.sourceDimensions.height}"]`) });
    await overlay.waitFor();
    const bounds = await overlay.boundingBox();
    assert.ok(bounds);
    const sx = bounds.width / config.sourceDimensions.width;
    const sy = bounds.height / config.sourceDimensions.height;
    await page.mouse.move(bounds.x + box.x * sx, bounds.y + box.y * sy);
    await page.mouse.down();
    await page.mouse.move(bounds.x + (box.x + box.width) * sx, bounds.y + (box.y + box.height) * sy, { steps: 8 });
    await page.mouse.up();
}

async function selected(label) {
    await eventually(() => page.getByRole("slider", { name: "情绪强度", exact: true }).getAttribute("aria-valuetext"), value => value === label);
    await stableCaptions(label);
}

async function stableCaptions(label) {
    await page.waitForFunction(label => {
        const captions = [...document.querySelectorAll("[data-canvas-node-panel] span")].filter(element => (element.classList.contains("font-semibold") && element.textContent.trim() === label) || element.textContent.trim() === `实时预览 · ${label}`);
        return captions.length === 2 && captions.every(element => {
            const style = getComputedStyle(element);
            return style.opacity === "1" && (style.filter === "none" || style.filter === "blur(0px)");
        });
    }, label);
}

async function stablePreview(label) {
    await stableCaptions(label);
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
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: "agnes-anonymous-emotion-reference.png", mimeType: "image/png", buffer: Buffer.from(config.image, "base64") });
    const original = await graph(owner, document => document.nodes.length === 1 && document.nodes[0].metadata?.assetId);
    const source = original.nodes[0];
    report.source_node = source;
    await fit();
    await inspectSource(owner, source, "initial");
    report.step = "原实际检测与手动框选边界";
    await openEditor(source);
    await manualBox({ ...config.faceBox, width: 3, height: 3 });
    assert.equal(await page.getByRole("slider", { name: "情绪强度", exact: true }).count(), 0);
    await manualBox(config.faceBox);
    await selected("中性克制");
    await eventually(() => report.model_loaded, value => value);
    report.step = "原25预设、键盘和鼠标拖拽";
    const pad = page.getByRole("slider", { name: "情绪强度", exact: true });
    for (const label of labels) {
        await pad.getByRole("button", { name: label, exact: true }).click();
        await selected(label);
    }
    await pad.getByRole("button", { name: "中性克制", exact: true }).click();
    for (const label of ["浅然莞尔", "温柔", "温柔"]) { await pad.press("ArrowLeft"); await selected(label); }
    await selected("温柔");
    for (const label of ["浅然莞尔", "中性克制", "隐忍", "疏离", "疏离"]) { await pad.press("ArrowRight"); await selected(label); }
    await selected("疏离");
    for (const label of ["紧张", "惊恐", "惊恐", "惊恐"]) { await pad.press("ArrowUp"); await selected(label); }
    await selected("惊恐");
    for (const label of ["紧张", "疏离", "悲伤", "绝望", "绝望"]) { await pad.press("ArrowDown"); await selected(label); }
    await selected("绝望");
    const bounds = await pad.boundingBox();
    assert.ok(bounds);
    await page.mouse.move(bounds.x + bounds.width * 0.95, bounds.y + bounds.height * 0.95);
    await page.mouse.down();
    for (let step = 1; step <= 10; step++) {
        const fraction = 0.95 - 0.09 * step;
        await page.mouse.move(bounds.x + bounds.width * fraction, bounds.y + bounds.height * fraction);
        const index = Math.round(Math.max(0, Math.min(4, fraction * 5 - 0.5)));
        await selected(labels[index * 5 + index]);
    }
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
    owner = await fresh(owner);
    await page.locator(`[data-node-id="${source.id}"]`).waitFor();
    await fit();
    await inspectSource(owner, source, "pre-submit-fresh");
    assert.deepEqual((await graph(owner, document => document.nodes.length === 1)).nodes, original.nodes);
    report.pre_submit_fresh_context_restored = true;
    report.step = "原重新打开默认与单个人物表情";
    await openEditor(source);
    await manualBox(config.faceBox);
    await selected("中性克制");
    await page.getByRole("slider", { name: "情绪强度", exact: true }).getByRole("button", { name: "欣喜若狂", exact: true }).click();
    await selected("欣喜若狂");
    await stablePreview("欣喜若狂");
    await page.screenshot({ path: resolve(output, "emotion-selected.png") });
    report.panel_geometry = await page.locator("[data-canvas-node-panel]").evaluate(element => {
        const rect = element.getBoundingClientRect();
        const head = element.querySelector("canvas");
        const pad = element.querySelector('[role="slider"]');
        const box = value => { const bounds = value?.getBoundingClientRect(); return bounds ? { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height } : null; };
        return { panel: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }, head: box(head), pad: box(pad), scrollWidth: element.scrollWidth, clientWidth: element.clientWidth };
    });
    await freezeExpectedPrompt();
    report.step = "原单份无蒙版多参考提交与私人running恢复";
    const admitted = page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST" && response.ok());
    await page.locator("[data-canvas-node-panel]").getByRole("button", { name: "生成", exact: true }).click();
    const task = await read(await admitted);
    report.task_id = task.id;
    assert.equal(report.submissions.length, 1);
    const submitted = report.submissions[0];
    assert.equal(Number(submitted.input.config.count), 1);
    assert.equal(submitted.input.config.quality, "high");
    assert.equal(submitted.input.metadata.edit, "emotion");
    assert.equal(submitted.input.metadata.sourceNodeId, source.id);
    assert.equal(submitted.input.metadata.emotionEditMode, "local-composite");
    assert.equal(submitted.input.referenceImages.length, 2);
    assert.ok(!submitted.input.mask);
    assert.equal(submitted.input.referenceImages[0].type, "image/png");
    assert.equal(submitted.input.referenceImages[1].type, "image/jpeg");
    const targetId = submitted.input.metadata.nodeId;
    report.target_node_id = targetId;
    const pending = await graph(owner, document => document.nodes.length === 2 && document.nodes.some(node => node.id === targetId && node.metadata?.taskId === task.id));
    const target = pending.nodes.find(node => node.id === targetId);
    assert.deepEqual({ width: target.width, height: target.height }, { width: 520, height: 520 });
    assert.deepEqual(target.position, { x: source.position.x + source.width + 96, y: source.position.y });
    assert.equal(target.metadata.size, "1024x1024");
    await acknowledgeNodeGeometry(owner, targetId, nodeGeometry(target), "pending", node => node.metadata?.taskId === task.id && node.metadata?.status === "loading");
    const expectedRestoredGeometry = { position: { x: target.position.x + target.width / 2 - 210, y: target.position.y + target.height / 2 - 210 }, width: 420, height: 420 };
    assert.equal(target.title, "角色1 · 欣喜若狂");
    assert.equal(target.metadata.emotionEdit.faceBox.source, "manual");
    assert.equal(target.metadata.emotionEdit.characterName, "角色1");
    assert.equal(target.metadata.emotionEdit.presetId, "emotion-2-2");
    assert.equal(target.metadata.emotionEdit.editMode, "local-composite");
    assert.equal(target.metadata.emotionEdit.sourceWidth, config.sourceDimensions.width);
    assert.equal(target.metadata.emotionEdit.sourceHeight, config.sourceDimensions.height);
    assert.equal(submitted.input.config.size, target.metadata.emotionEdit.providerSize);
    assert.deepEqual(target.metadata.references, [source.metadata.storageKey]);
    report.emotion_edit = target.metadata.emotionEdit;
    assert.deepEqual(report.emotion_edit.editRegion, report.expected_edit_region);
    assert.equal(expectedPrompt(report.emotion_edit.faceBox, report.emotion_edit.editRegion), report.expected_prompt);
    assert.equal(submitted.prompt, report.expected_prompt);
    assert.equal(submitted.input.prompt, report.expected_prompt);
    assert.equal(target.metadata.prompt, report.expected_prompt);
    assert.equal(task.prompt, report.expected_prompt);
    await eventually(() => readTask(owner, task.id), row => row.status === "running");
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
    owner = await fresh(owner);
    await page.locator(`[data-node-id="${targetId}"]`).waitFor();
    assert.equal((await readTask(owner, task.id)).status, "running");
    const { node: restoredTarget, document: restoredPending } = await acknowledgeNodeGeometry(owner, targetId, expectedRestoredGeometry, "running_fresh", node => node.metadata?.taskId === task.id && node.metadata?.status === "loading" && !node.metadata?.content, { saveAck: true });
    assert.equal(restoredPending.nodes.length, 2);
    assert.deepEqual(nodeCenter(restoredTarget), nodeCenter(target));
    assert.deepEqual(restoredTarget.metadata.emotionEdit, report.emotion_edit);
    assert.equal(restoredTarget.metadata.prompt, report.expected_prompt);
    assert.equal(report.submissions.length, 1);
    assert.equal(report.uploads, 3);
    report.pending_fresh_context_restored = true;
    await fit();
    await inspectSource(owner, source, "running-fresh");
    await writeFile(config.imageReleaseFile, "browser-ready");
    report.step = "真实模型终态与原局部羽化合成";
    const terminal = await eventually(() => readTask(owner, task.id), row => ["succeeded", "failed", "cancelled"].includes(row.status), 360000);
    report.terminal_status = terminal.status;
    report.terminal_error_code = terminal.errorCode;
    assert.equal(terminal.status, "succeeded", JSON.stringify(terminal.errorCode));
    const effectKey = `attach-node:${task.id}:${targetId}:0`;
    const final = await graph(owner, document => document.nodes.length === 2 && document.nodes.some(node => node.id === targetId && node.metadata?.assetId && node.metadata?.storageKey && node.metadata?.status === "success" && node.metadata?.generationEffectKeys?.includes(effectKey)), 90000);
    assert.deepEqual(final.nodes.find(node => node.id === source.id), source);
    const completed = await readTask(owner, task.id);
    assert.equal(completed.status, "succeeded");
    assert.equal(completed.prompt, report.expected_prompt);
    const providerResult = JSON.parse(completed.resultJson).images[0];
    const derived = final.nodes.find(node => node.id === targetId);
    report.result_geometry.attached = { stored: nodeGeometry(derived), center: nodeCenter(derived) };
    assert.deepEqual(derived.position, restoredTarget.position);
    assert.equal(derived.metadata.prompt, report.expected_prompt);
    assert.notEqual(derived.metadata.storageKey, providerResult.storageKey);
    assert.equal(derived.metadata.naturalWidth, config.sourceDimensions.width);
    assert.equal(derived.metadata.naturalHeight, config.sourceDimensions.height);
    assert.equal(final.connections.length, 1);
    assert.equal(final.connections[0].fromNodeId, source.id);
    assert.equal(final.connections[0].toNodeId, targetId);
    report.provider_storage_key = providerResult.storageKey;
    report.provider_dimensions = { width: providerResult.width, height: providerResult.height };
    report.output_storage_key = derived.metadata.storageKey;
    report.effect_key = effectKey;
    report.final_nodes = final.nodes;
    const ownerOutput = await owner.request.get(`${origin}${resources}/${derived.metadata.storageKey.slice(9)}/file`);
    assert.ok(ownerOutput.ok());
    await writeFile(resolve(output, "derived-result.png"), await ownerOutput.body());
    const shared = await member.request.get(`${origin}${resources}/${derived.metadata.storageKey.slice(9)}/file`);
    assert.ok(shared.ok());
    report.output_sha256 = createHash("sha256").update(await shared.body()).digest("hex");
    assert.equal((await member.request.get(`${origin}${resources}/${providerResult.storageKey.slice(9)}/file`)).status(), 404);
    for (const reference of submitted.input.referenceImages) assert.equal((await member.request.get(`${origin}${resources}/${reference.storageKey.slice(9)}/file`)).status(), 404);
    assert.equal(report.uploads, 4);
    await fit();
    const expectedDecodedGeometry = { position: { ...restoredTarget.position }, width: 520, height: 520 };
    const isDerived = node => node.metadata?.taskId === task.id && node.metadata?.status === "success" && node.metadata?.assetId === derived.metadata.assetId && node.metadata?.storageKey === derived.metadata.storageKey && node.metadata?.generationEffectKeys?.includes(effectKey);
    const { node: decodedDerived, document: decodedDocument } = await acknowledgeNodeGeometry(owner, targetId, expectedDecodedGeometry, "decoded", isDerived, { decoded: true, saveAck: true });
    assert.deepEqual(decodedDerived.position, restoredTarget.position);
    assert.deepEqual(decodedDocument.nodes.find(node => node.id === source.id), source);
    assert.equal(decodedDocument.connections.length, 1);
    report.final_nodes = decodedDocument.nodes;
    owner = await fresh(owner);
    await page.locator(`[data-node-id="${source.id}"]`).waitFor();
    await fit();
    const { node: restoredDerived, document: restored } = await acknowledgeNodeGeometry(owner, targetId, nodeGeometry(decodedDerived), "terminal_fresh", isDerived, { decoded: true });
    await inspectSource(owner, source, "terminal-fresh");
    assert.equal(restored.nodes.length, 2);
    assert.equal(restored.connections.length, 1);
    assert.deepEqual(restored.nodes.find(node => node.id === source.id), source);
    assert.equal(restoredDerived.metadata.prompt, report.expected_prompt);
    assert.deepEqual(nodeGeometry(restoredDerived), nodeGeometry(decodedDerived));
    report.output_dimensions = report.result_geometry.terminal_fresh.image;
    assert.deepEqual(report.output_dimensions, config.sourceDimensions);
    const finalBytes = await owner.request.get(`${origin}${resources}/${derived.metadata.storageKey.slice(9)}/file`);
    assert.ok(finalBytes.ok());
    assert.equal(createHash("sha256").update(await finalBytes.body()).digest("hex"), report.output_sha256);
    for (const id of [source.id, targetId]) {
        const bounds = await page.locator(`[data-node-id="${id}"]`).boundingBox();
        assert.ok(bounds && bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= config.width && bounds.y + bounds.height <= 900);
    }
    await page.mouse.move(5, 450);
    await page.screenshot({ path: resolve(output, "fresh-restored.png") });
    await Promise.all(observations);
    assert.equal(report.admissions.length, 1);
    assert.equal(report.submissions.length, 1);
    assert.equal(report.uploads, 4);
    assert.equal(report.node_bind_operations, 0);
    assert.equal(report.pre_close_viewport_acks.length, 3);
    assert.equal(report.restored_viewport_acks.length, 3);
    assert.deepEqual(report.observation_errors, []);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    report.final_fresh_context_restored = true;
    report.content_verdict = "requires inspection of actual supplier and composite images";
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
    await Promise.all(observations);
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    process.stdout.write(JSON.stringify(report) + "\n");
}
