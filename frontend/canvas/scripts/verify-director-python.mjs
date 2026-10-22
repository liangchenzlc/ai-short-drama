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
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const videoOutput = config.outputKind === "video";
const multitrackScene = config.sceneKind === "multitrack";
const actorScene = config.sceneKind === "actor" || multitrackScene;
const renderViews = config.sceneKind === "render_views";
const scenePrompt = actorScene ? "演员姿势、直线路径与跟拍恢复验证" : "立方体构图与永久截图验证";
const report = { step: "start", width: config.width, output_kind: config.outputKind, scene_kind: config.sceneKind, page_errors: [], http_errors: [], resource_posts: 0, task_posts: 0 };
const server = await createServer({ cacheDir: resolve(".runtime/vite-director"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let owner;
let page;
const workbench = () => page.locator('[data-director-workbench="true"]');
const dock = () => page.getByRole("navigation", { name: "导演台视口工具", exact: true });
const director = document => document.nodes.find(node => node.type === "director");
const scene = document => document.directorScenes.find(item => item.id === director(document)?.metadata?.directorSceneId);
const digest = bytes => createHash("sha256").update(bytes).digest("hex");
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
async function graph(predicate = () => true) {
    return eventually(async () => (await read(await owner.request.get(origin + path))).source_document, predicate, "导演台作品没有达到预期");
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
async function open(context) {
    page = await context.newPage();
    page.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    page.on("request", request => {
        const url = new URL(request.url()).pathname;
        if (request.method() === "POST" && url === "/api/v1/canvas-runtime/resources") report.resource_posts++;
        if (request.method() === "POST" && url === "/api/v1/canvas-runtime/tasks") report.task_posts++;
    });
    page.on("response", response => {
        const url = new URL(response.url()).pathname;
        if (url.startsWith("/api/") && response.status() >= 400) report.http_errors.push({ path: url, status: response.status() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
}
async function ready() {
    await workbench().waitFor({ state: "visible", timeout: 60000 });
    await page.locator('.director-viewport-shell[data-renderer-ready="true"]').waitFor({ state: "visible", timeout: 60000 });
    const skip = page.getByRole("button", { name: "跳过引导", exact: true });
    if (await skip.isVisible()) await skip.click();
    await eventually(() => dock().getByRole("button", { name: "截图", exact: true }).isEnabled(), Boolean, "WebGL截图没有就绪");
}
async function save() {
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await page.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}
async function close() {
    await page.getByRole("button", { name: "关闭导演台", exact: true }).click();
    await workbench().waitFor({ state: "hidden" });
    await save();
}
async function fitCanvas() {
    const focus = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focus.isVisible()) await focus.getByRole("button", { name: "退出专注模式", exact: true }).press("Control+0");
    else await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
}
async function openExisting() {
    await fitCanvas();
    await page.locator(`[data-node-id="${report.director_node_id}"]`).getByRole("button", { name: "打开导演台", exact: true }).click();
    await ready();
}
async function checkGallery() {
    await page.getByRole("button", { name: "机位视角", exact: true }).click();
    await page.getByRole("tab", { name: "截图", exact: true }).click();
    const image = page.getByRole("region", { name: "相机截图", exact: true }).getByRole("img", { name: report.screenshot.name, exact: true });
    await image.waitFor({ state: "visible" });
    await eventually(() => image.evaluate(element => element.complete && element.naturalWidth > 0), Boolean, "永久截图没有加载");
    const displayed = await image.evaluate(async element => {
        const response = await fetch(element.currentSrc);
        if (!response.ok) throw new Error(`截图显示读取失败：${response.status}`);
        return Array.from(new Uint8Array(await response.arrayBuffer()));
    });
    assert.equal(digest(Uint8Array.from(displayed)), report.screenshot.checksum);
}
async function checkVideo(label) {
    await fitCanvas();
    const card = page.locator(`[data-node-id="${report.video.node_id}"]`);
    await card.getByRole("button", { name: /^播放 / }).click();
    const video = card.locator("video").first();
    await eventually(() => video.evaluate(element => !element.paused && element.currentTime > 0.2 && element.readyState >= 2 && element.videoWidth > 0), Boolean, `${label}视频没有实际解码播放`);
    const facts = await video.evaluate(async element => {
        element.pause();
        const response = await fetch(element.currentSrc);
        if (!response.ok) throw new Error(`视频显示读取失败：${response.status}`);
        return { width: element.videoWidth, height: element.videoHeight, duration: element.duration, bytes: Array.from(new Uint8Array(await response.arrayBuffer())) };
    });
    assert.equal(digest(Uint8Array.from(facts.bytes)), report.video.checksum);
    assert.ok(Number.isFinite(facts.duration) && facts.duration >= report.duration_seconds - 0.5 && facts.duration <= report.duration_seconds + 1.5);
    assert.equal(facts.width, report.video.width);
    assert.equal(facts.height, report.video.height);
    const seekTime = facts.duration / 2;
    await video.evaluate((element, time) => { element.currentTime = time; }, seekTime);
    await eventually(() => video.evaluate((element, time) => !element.seeking && element.readyState >= 2 && Math.abs(element.currentTime - time) < 0.1, seekTime), Boolean, `${label}视频无法跳转`);
    report.video_playback ??= [];
    report.video_playback.push({ label, width: facts.width, height: facts.height, duration: facts.duration, seek_time: seekTime });
    await page.screenshot({ path: resolve(output, `director-video-${label}.png`), fullPage: true });
}
async function captureResource(storageKey, filename) {
    assert.match(storageKey, /^resource:\d+$/);
    const id = storageKey.slice("resource:".length);
    const response = await owner.request.get(`${origin}/api/v1/canvas-runtime/resources/${id}/file`);
    assert.ok(response.ok());
    const bytes = await response.body();
    if (filename) await writeFile(resolve(output, filename), bytes);
    return { resource_id: id, checksum: digest(bytes) };
}
async function advancedTools() {
    await dock().getByRole("button", { name: "移动", exact: true }).click({ button: "right" });
}
async function setDirectorMode(label, mode) {
    await advancedTools();
    await page.getByRole("menuitem", { name: /^导演台导航(?: right)?$/ }).hover();
    await page.getByRole("menuitem", { name: label, exact: true }).click();
    await eventually(() => dock().getAttribute("data-director-mode"), value => value === mode, "原导演模式没有切换");
    await workbench().locator(".director-viewport-shell").hover({ position: { x: 10, y: 10 } });
    await eventually(() => page.getByRole("menu").count(), value => value === 0, "原导航菜单尚未收起");
}
async function setWorkspace(label, view) {
    await advancedTools();
    await page.getByRole("menuitem", { name: /^导演台导航(?: right)?$/ }).hover();
    await page.getByRole("menuitem", { name: label, exact: true }).click();
    await eventually(() => workbench().getAttribute("data-workspace-view"), value => value === view, "原工作区视图没有切换");
    const viewport = page.viewportSize();
    await page.mouse.move(viewport.width - 1, viewport.height - 1);
    await eventually(() => page.getByRole("menu").count(), value => value === 0, "原工作区菜单尚未收起");
}
async function checkMultitrackHistory() {
    report.step = "原人物、几何体与摄影机三轨及撤销恢复";
    const sequencer = workbench().locator(".director-sequencer");
    await sequencer.getByRole("button", { name: "展开时间轴", exact: true }).click();
    await sequencer.getByRole("button", { name: "绘制主机位轨迹", exact: true }).click();
    await page.getByRole("menuitem", { name: /矩形路径/ }).click();
    const baseline = scene(await graph(document => scene(document)?.cameras.some(camera => camera.drawnPath?.kind === "rectangle")));
    const prop = baseline.objects.find(object => object.id === report.prop_id);
    const actor = baseline.objects.find(object => object.id === report.actor_id);
    const camera = baseline.cameras.find(item => item.id === baseline.shots[0].cameraId);
    assert.equal(prop.motionPath.kind, "rectangle");
    assert.equal(prop.animationTrackEnabled, true);
    assert.equal(camera.drawnPath.kind, "rectangle");
    report.multitrack = { prop_id: prop.id, camera_id: camera.id };
    assert.equal(await sequencer.locator(".director-sequencer-main-label").count(), 3);
    await sequencer.getByRole("button", { name: `移除${prop.name}轨迹`, exact: true }).click();
    const removed = scene(await graph(document => !scene(document)?.objects.find(object => object.id === prop.id)?.motionPath));
    assert.deepEqual(removed.objects.find(object => object.id === prop.id).keyframes, prop.motionPath.originalKeyframes);
    assert.deepEqual(removed.objects.find(object => object.id === actor.id), actor);
    assert.deepEqual(removed.cameras, baseline.cameras);
    await page.keyboard.press("Control+z");
    const undone = scene(await graph(document => scene(document)?.objects.find(object => object.id === prop.id)?.motionPath?.kind === "rectangle"));
    assert.deepEqual(undone.objects, baseline.objects);
    assert.deepEqual(undone.cameras, baseline.cameras);
    await page.keyboard.press("Control+Shift+z");
    await graph(document => !scene(document)?.objects.find(object => object.id === prop.id)?.motionPath);
    await page.keyboard.press("Control+z");
    const restored = scene(await graph(document => scene(document)?.objects.find(object => object.id === prop.id)?.motionPath?.kind === "rectangle"));
    assert.deepEqual(restored.objects, baseline.objects);
    assert.deepEqual(restored.cameras, baseline.cameras);
    report.multitrack.history_restored = true;
}
async function checkMultitrackPreview(label) {
    report.step = `${label}原三轨预演、键盘边界与实际WebGL帧`;
    if (await dock().getByRole("button", { name: "动画时间轴", exact: true }).getAttribute("aria-pressed") !== "true") await dock().getByRole("button", { name: "动画时间轴", exact: true }).click();
    const baseline = scene(await graph());
    const actor = baseline.objects.find(object => object.id === report.actor_id);
    const prop = baseline.objects.find(object => object.id === report.prop_id);
    const sequencer = workbench().locator(".director-sequencer");
    for (const name of [actor.name, prop.name, "主机位"]) await sequencer.getByRole("button", { name, exact: true }).waitFor({ state: "visible" });
    for (const time of [10, 0]) {
        const key = sequencer.getByRole("button", { name: `选择 ${actor.name} 位置 ${time.toFixed(2)}s 的关键帧`, exact: true }).first();
        await key.press("Enter");
        await eventually(() => sequencer.locator("span.tabular-nums").first().innerText(), value => value === `${time.toFixed(2)}s`, "原人物关键帧没有定位");
    }
    await setWorkspace("成片预演", "preview");
    const slider = sequencer.getByRole("slider", { name: "预演时间线", exact: true });
    const time = async () => Number(await slider.getAttribute("aria-valuenow"));
    const duration = baseline.shots[0].duration;
    const fps = baseline.shots[0].fps || 24;
    await slider.press("ArrowLeft");
    assert.equal(await time(), 0);
    await slider.press("ArrowRight");
    assert.ok(Math.abs(await time() - 1 / fps) < 1e-6);
    await slider.press("ArrowLeft");
    const first = await stableViewport(`multitrack-${label}-start`);
    const bounds = await slider.boundingBox();
    assert.ok(bounds);
    await slider.click({ position: { x: bounds.width / 2, y: bounds.height / 2 } });
    await eventually(time, value => Math.abs(value - duration / 2) <= 1 / fps, "原预演指针没有定位半程");
    const middle = await stableViewport(`multitrack-${label}-middle`);
    assert.notEqual(first.checksum, middle.checksum, "三轨半程必须改变真实WebGL画面");
    report.multitrack_frames ??= [];
    report.multitrack_frames.push({ label, first, middle });
    await slider.click({ position: { x: bounds.width - 1, y: bounds.height / 2 } });
    for (let index = 0; index < 3; index++) await slider.press("ArrowRight");
    assert.equal(await time(), duration);
    await slider.press("ArrowRight");
    assert.equal(await time(), duration);
    await slider.press("ArrowLeft");
    assert.ok(Math.abs(await time() - (duration - 1 / fps)) < 1e-6);
    const zoom = sequencer.getByRole("slider", { name: "时间线缩放", exact: true });
    await zoom.press("ArrowLeft");
    await zoom.press("ArrowLeft");
    assert.equal(await zoom.inputValue(), "0.75");
    for (let index = 0; index < 8; index++) await zoom.press("ArrowRight");
    assert.equal(await zoom.inputValue(), "2.5");
    for (let index = 0; index < 6; index++) await zoom.press("ArrowLeft");
    assert.equal(await zoom.inputValue(), "1");
    await slider.click({ position: { x: 0.1, y: bounds.height / 2 } });
    await sequencer.getByRole("button", { name: "播放", exact: true }).click();
    await eventually(time, value => value > 0.2 && value < duration, "原预演播放没有推进时间");
    await sequencer.getByRole("button", { name: "暂停", exact: true }).click();
    const paused = await time();
    await sequencer.getByRole("button", { name: "播放", exact: true }).waitFor({ state: "visible" });
    await page.screenshot({ path: resolve(output, `director-multitrack-${label}.png`), fullPage: true });
    await setWorkspace("场景调度", "scene");
    const after = scene(await graph());
    for (const field of ["objects", "cameras", "shots"]) assert.deepEqual(after[field], baseline[field], `原预演不得改写${field}`);
    report.multitrack_transport ??= [];
    report.multitrack_transport.push({ label, duration, fps, paused_time: paused, keyboard_bounds: true, zoom_bounds: true, scene_unchanged: true });
}
async function stableViewport(mode) {
    const canvas = workbench().locator(".director-viewport-shell canvas");
    let previous;
    let equalFrames = 0;
    const snapshot = await eventually(async () => canvas.evaluate(element => {
        const rect = element.getBoundingClientRect();
        return { dataUrl: element.toDataURL("image/png"), width: element.width, height: element.height, client_width: element.clientWidth, client_height: element.clientHeight, rect_width: rect.width, rect_height: rect.height };
    }), value => {
        const current = JSON.stringify(value);
        equalFrames = current === previous ? equalFrames + 1 : 0;
        previous = current;
        return equalFrames >= 4;
    }, "原WebGL视图没有稳定");
    const { dataUrl, ...facts } = snapshot;
    const bytes = Buffer.from(dataUrl.split(",")[1], "base64");
    const filename = `director-render-${mode}.png`;
    await writeFile(resolve(output, filename), bytes);
    return { mode, filename, checksum: digest(bytes), ...facts };
}
async function checkRenderViews() {
    await advancedTools();
    for (const label of ["深度视图", "法线视图"]) assert.equal(await page.getByRole("menuitem", { name: label, exact: true }).count(), 0, "摆场保留高级视图门控");
    await page.getByRole("menuitem", { name: "构图预览", exact: true }).click();
    await setDirectorMode("摄影机", "camera");
    await page.getByRole("tab", { name: "属性", exact: true }).click();
    report.reference_viewport = await stableViewport("reference-ready");
    await dock().getByRole("button", { name: "截图", exact: true }).click();
    const reference = await graph(document => scene(document)?.shots[0].screenshots?.length === 1);
    const beauty = scene(reference).shots[0].screenshots[0];
    report.reference_screenshot = { ...await captureResource(beauty.storageKey, "director-reference-screenshot.png"), name: beauty.name, width: beauty.width, height: beauty.height };
    const baseline = structuredClone(scene(reference));
    report.render_views = [await stableViewport("beauty")];
    for (const [mode, label] of [["depth", "深度视图"], ["normal", "法线视图"]]) {
        await advancedTools();
        await page.getByRole("menuitem", { name: label, exact: true }).click();
        const result = await stableViewport(mode);
        assert.notEqual(result.checksum, report.render_views.at(-1).checksum);
        report.render_views.push(result);
        await page.screenshot({ path: resolve(output, `director-${mode}-ui.png`), fullPage: true });
    }
    await setDirectorMode("摆场", "layout");
    await advancedTools();
    for (const label of ["深度视图", "法线视图"]) assert.equal(await page.getByRole("menuitem", { name: label, exact: true }).count(), 0);
    const beautyItem = page.getByRole("menuitem", { name: "构图预览", exact: true });
    assert.match(await beautyItem.getAttribute("class"), /selected/, "退回摆场恢复构图预览");
    await beautyItem.click();
    assert.deepEqual(scene(await graph()), baseline, "查看模式不得修改场景内容");
    await setDirectorMode("摄影机", "camera");
    await page.getByRole("tab", { name: "属性", exact: true }).click();
    await advancedTools();
    await page.getByRole("menuitem", { name: "深度视图", exact: true }).click();
    report.capture_viewport = await stableViewport("depth-before-capture");
    for (const field of ["width", "height", "client_width", "client_height", "rect_width", "rect_height"]) assert.equal(report.capture_viewport[field], report.reference_viewport[field], `原截图参照视口不同：${field}`);
    report.original_render_mode_gate = true;
}
try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    owner = await context(config.username);
    await open(owner);
    report.step = "原菜单创建空导演台";
    await page.locator("[data-canvas-viewport]").click({ button: "right", position: { x: Math.min(420, config.width / 2), y: 350 } });
    await page.locator("[data-canvas-context-menu]").getByRole("button", { name: "添加节点", exact: true }).click();
    await page.getByRole("button", { name: "导演台", exact: true }).click();
    await ready();
    const created = await graph(document => document.nodes.length === 1 && Boolean(director(document)) && Boolean(scene(document)));
    report.director_node_id = director(created).id;
    report.scene_id = scene(created).id;
    report.shot_id = scene(created).activeShotId;
    assert.equal(scene(created).objects.length, 0);
    assert.equal(scene(created).cameras.length, 1);
    report.created_from_original_menu = true;

    report.step = actorScene ? "原演员、姿势与直线路径操作" : "原几何体、画幅与机位操作";
    const rail = page.getByRole("navigation", { name: "导演台工作区", exact: true });
    await rail.getByRole("button", { name: "添加角色", exact: true }).click();
    if (actorScene) {
        await page.getByTestId("director-actor-preset-standard_male").click();
        const rigged = await graph(document => scene(document)?.objects[0]?.rig?.status === "ready");
        report.actor_id = scene(rigged).objects[0].id;
        assert.ok(Object.keys(scene(rigged).objects[0].rig.boneMap).length >= 8);
        await page.getByRole("tablist", { name: "角色属性", exact: true }).getByRole("tab", { name: "姿势", exact: true }).click();
        await page.getByRole("button", { name: "行走", exact: true }).click();
        await graph(document => scene(document)?.objects[0]?.pose === "walk");
        await dock().getByRole("button", { name: "动画时间轴", exact: true }).click();
        await page.getByRole("tab", { name: "运动轨迹", exact: true }).click();
        await page.getByRole("button", { name: "创建运动轨迹", exact: true }).click();
        await page.getByRole("menuitem", { name: /直线路径/ }).click();
        await graph(document => scene(document)?.objects[0]?.motionPath?.kind === "line" && scene(document)?.shots[0].duration === 10);
        await rail.getByRole("button", { name: "添加角色", exact: true }).click();
        await page.getByTestId("director-actor-preset-geometric").click();
        await page.getByTestId("director-geometry-box").click();
        const withProp = await graph(document => scene(document)?.objects.length === 2);
        if (multitrackScene) {
            report.prop_id = scene(withProp).objects[1].id;
            const sequencer = workbench().locator(".director-sequencer");
            await sequencer.getByRole("button", { name: "新建轨道", exact: true }).click();
            const tracked = await graph(document => scene(document)?.objects[1]?.animationTrackEnabled);
            await sequencer.getByRole("button", { name: `绘制${scene(tracked).objects[1].name}轨迹`, exact: true }).click();
            await page.getByRole("menuitem", { name: /矩形路径/ }).click();
            await graph(document => scene(document)?.objects[1]?.motionPath?.kind === "rectangle");
        }
        await rail.getByRole("button", { name: "添加机位", exact: true }).click();
        await page.getByRole("button", { name: "侧面跟拍", exact: true }).click();
        await graph(document => scene(document)?.cameras.some(camera => camera.followObjectId === report.actor_id));
    } else {
        await page.getByTestId("director-actor-preset-geometric").click();
        await page.getByTestId("director-geometry-box").click();
    }
    await graph(document => scene(document)?.objects.length === (actorScene ? 2 : 1));
    await rail.getByRole("button", { name: "选择画幅比例", exact: true }).click();
    await page.getByRole("button", { name: "16:9", exact: true }).click();
    await page.getByRole("textbox", { name: "场景描述", exact: true }).fill(scenePrompt);
    await page.getByRole("button", { name: "机位视角", exact: true }).click();
    await graph(document => scene(document)?.aspectRatio === "16:9" && scene(document)?.shots[0].prompt === scenePrompt);
    assert.equal(await page.getByRole("button", { name: "将当前场景发送到画布", exact: true }).isDisabled(), true);
    if (multitrackScene) {
        await checkMultitrackHistory();
        await checkMultitrackPreview("produced");
    }
    if (renderViews) {
        report.step = "原摄影机深度法线与模式门控";
        await checkRenderViews();
    }
    report.step = "真实WebGL截图与素材保存";
    await dock().getByRole("button", { name: "截图", exact: true }).click();
    const captured = await graph(document => scene(document)?.shots[0].screenshots?.length === (renderViews ? 2 : 1));
    const screenshot = scene(captured).shots[0].screenshots.at(-1);
    report.duration_seconds = scene(captured).shots[0].duration;
    report.screenshot = { ...await captureResource(screenshot.storageKey, "director-screenshot.png"), name: screenshot.name, width: screenshot.width, height: screenshot.height };
    if (renderViews) {
        await advancedTools();
        const depthItem = page.getByRole("menuitem", { name: "深度视图", exact: true });
        assert.match(await depthItem.getAttribute("class"), /selected/, "截图仍按源保留当前查看模式");
        await depthItem.click();
        report.beauty_screenshot_in_depth_view = true;
    }
    await checkGallery();
    await page.screenshot({ path: resolve(output, "director-captured.png"), fullPage: true });

    report.step = videoOutput ? "原右键菜单导出白膜视频" : "原右键菜单应用构图到镜头";
    await dock().getByRole("button", { name: "移动", exact: true }).click({ button: "right" });
    await page.getByRole("menuitem", { name: videoOutput ? "导出白膜视频" : "应用到镜头", exact: true }).click();
    if (videoOutput) await page.getByRole("status").filter({ hasText: "白膜视频已导出，可在画布中预览播放" }).waitFor({ state: "visible", timeout: 60000 });
    const applied = await graph(document => Boolean(director(document)?.metadata.directorPreviewNodeId) && document.nodes.length === (videoOutput ? 3 : 2) && document.connections.length === (videoOutput ? 2 : 1));
    const preview = applied.nodes.find(node => node.id === director(applied).metadata.directorPreviewNodeId);
    report.preview = { ...await captureResource(preview.metadata.storageKey), node_id: preview.id, asset_id: preview.metadata.assetId };
    if (videoOutput) {
        const video = applied.nodes.find(node => node.id === director(applied).metadata.directorClayVideoNodeId);
        assert.equal(video.type, "video");
        assert.equal(video.metadata.hasAudio, false);
        assert.equal(video.metadata.videoPreview.sourceKey, video.metadata.storageKey);
        const filename = video.metadata.mimeType.includes("webm") ? "director-clay.webm" : "director-clay.mp4";
        report.video = { ...await captureResource(video.metadata.storageKey, filename), filename, node_id: video.id, asset_id: video.metadata.assetId, width: video.metadata.naturalWidth, height: video.metadata.naturalHeight, duration_ms: video.metadata.durationMs, mime_type: video.metadata.mimeType };
        report.poster = await captureResource(video.metadata.videoPreview.storageKey);
    }
    report.director_node_version = director(applied).updatedAt;
    assert.notEqual(report.director_node_version, director(created).updatedAt, "构图回写必须推进导演节点版本");
    await eventually(() => page.getByLabel("导演台保存状态", { exact: true }).innerText(), value => value === "已保存", "导演台保存没有确认");
    await close();
    const saved = await graph(document => Boolean(director(document)?.metadata.directorCoverStorageKey));
    report.cover = await captureResource(director(saved).metadata.directorCoverStorageKey);
    report.scene = scene(saved);
    if (videoOutput) await checkVideo("produced");
    const uploadsBeforeRecovery = report.resource_posts;

    report.step = "关闭旧窗口，仅cookies恢复场景与截图";
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(undefined, cookies);
    await open(owner);
    await openExisting();
    if (renderViews) {
        assert.equal(await dock().getAttribute("data-director-mode"), "layout", "仅cookies窗口不继承本机查看模式");
        report.fresh_render_mode_reset = true;
    }
    await checkGallery();
    assert.deepEqual(scene(await graph()), report.scene);
    if (multitrackScene) await checkMultitrackPreview("restored");
    await page.screenshot({ path: resolve(output, "director-restored.png"), fullPage: true });
    await close();
    if (videoOutput) await checkVideo("restored");
    report.cookie_only_recovery = true;


    assert.equal(report.resource_posts, uploadsBeforeRecovery, "恢复不应重复上传截图或封面");
    assert.equal(report.resource_posts, videoOutput ? 5 : renderViews ? 4 : 3);
    assert.equal(report.task_posts, 0);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    report.step = "complete";
    process.stdout.write(JSON.stringify(report));
} catch (error) {
    report.error = error.stack ?? String(error);
    if (page && !page.isClosed()) {
        await page.screenshot({ path: resolve(output, "failure.png"), fullPage: true }).catch(() => {});
        const content = await page.content().catch(() => undefined);
        if (content) await writeFile(resolve(output, "failure.html"), content).catch(() => {});
        const snapshot = await page.locator("body").ariaSnapshot().catch(() => undefined);
        if (snapshot) await writeFile(resolve(output, "failure.yml"), snapshot).catch(() => {});
    }
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
