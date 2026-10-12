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
const output = resolve(config.outputDirectory);
const report = { step: "start", mode: config.mode, width: config.width, page_errors: [], http_errors: [], submissions: [], resource_posts: 0, decoded_videos: [] };
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(".runtime/vite-storyboard-media"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
let owner;
const script = () => page.locator('[data-node-id="existing-script"]');
const scriptData = document => document.nodes.find(node => node.id === "existing-script");
const latestBatch = document => scriptData(document).metadata.generationBatches?.at(-1);
const label = config.mode === "storyboard_image" ? "生成未完成分镜图" : config.mode === "storyboard_video" ? "生成未完成视频" : "生成动作拆分 12 宫格";
const confirmation = () => page.getByRole("dialog").filter({ has: page.getByText(`确认提交 2 个${config.mode === "storyboard_image" ? "图片生成" : config.mode === "storyboard_video" ? "视频生成" : "动作板生成"}任务`, { exact: true }) });

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function eventually(readValue, predicate) {
    let value;
    for (let attempt = 0; attempt < 500; attempt++) {
        value = await readValue();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`分镜媒体状态未达到预期：${JSON.stringify(value)}`);
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
        if (request.method() === "POST" && route === "/api/v1/canvas-runtime/tasks") report.submissions.push(request.postDataJSON());
        if (request.method() === "POST" && route === "/api/v1/canvas-runtime/resources") report.resource_posts++;
    });
    page.on("response", response => {
        if (response.status() >= 400) report.http_errors.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    await script().getByRole("region", { name: "分镜镜头列表", exact: true }).waitFor({ state: "visible" });
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
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await page.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}
async function fresh() {
    await save();
    const cookies = await owner.cookies();
    await owner.close();
    owner = await context(undefined, cookies);
    await open(owner);
    await fit();
}
async function menu() {
    await script().getByRole("button", { name: "更多操作", exact: true }).click();
    const item = page.getByRole("menuitem", { name: label, exact: true });
    await item.waitFor({ state: "visible" });
    return item;
}
async function imagesReady(ids) {
    for (const id of ids) {
        const node = page.locator(`[data-node-id="${id}"]`);
        if (config.kind === "video") {
            await node.getByRole("button", { name: /^播放 / }).first().click();
            const video = node.locator("video").first();
            await video.waitFor({ state: "visible" });
            await eventually(() => video.evaluate(media => media.readyState >= 1 && media.videoWidth > 0), Boolean);
            const measured = await video.evaluate(media => ({ width: media.videoWidth, height: media.videoHeight, duration: media.duration }));
            assert.equal(measured.width, 160);
            assert.equal(measured.height, 90);
            assert.ok([4, 8].includes(measured.duration));
            report.decoded_videos.push({ id, ...measured });
        } else {
            const image = node.getByRole("img").first();
            await eventually(() => image.evaluate(element => element.complete && element.naturalWidth > 0), Boolean);
        }
    }
}

async function previewsReady(ids) {
    return graph(owner, document => ids.every(id => {
        const metadata = document.nodes.find(node => node.id === id)?.metadata;
        return metadata?.videoPreview?.storageKey?.startsWith("resource:") && metadata.videoPreview.sourceKey === metadata.storageKey && metadata.videoPreview.captureVersion === 2;
    }));
}

try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined });
    owner = await context(config.username);
    const member = await context(config.memberUsername);
    await open(owner);
    await fit();
    if (config.kind === "video") await previewsReady(["completed-image"]);
    await imagesReady(["completed-image"]);
    await graph(owner, document => scriptData(document).metadata.storyboardComposerHeight === 104 && document.nodes.find(node => node.id === "completed-image").metadata.naturalWidth === (config.kind === "video" ? 160 : 37));
    if (config.videoInputMode === "keyframe") {
        await script().getByRole("button", { name: "全屏编辑", exact: true }).click();
        const editor = page.getByRole("dialog", { name: "分镜视频批次", exact: true });
        await editor.getByText("先做首帧", { exact: true }).click();
        await graph(owner, document => scriptData(document).metadata.storyboardVideoInputMode === "keyframe");
        await editor.getByRole("button", { name: "Close", exact: true }).click();
        await editor.waitFor({ state: "hidden" });
    }
    if (config.kind === "video") report.video_input_mode = scriptData(await graph()).metadata.storyboardVideoInputMode || "direct";
    await save();
    const original = await graph();
    const originalImage = original.nodes.find(node => node.id === "completed-image");
    report.original_image = originalImage;
    const rowIds = scriptData(original).metadata.storyboard.rows.map(row => row.id);
    report.step = "原菜单取消批次";
    const first = await menu();
    assert.notEqual(await first.getAttribute("aria-disabled"), "true");
    await first.click();
    await confirmation().waitFor({ state: "visible" });
    assert.equal(report.submissions.length, 0);
    await confirmation().getByRole("button", { name: /^取\s*消$/, exact: true }).click();
    await confirmation().waitFor({ state: "hidden" });
    const cancelled = await graph();
    assert.deepEqual(cancelled.nodes, original.nodes);
    assert.deepEqual(cancelled.connections, original.connections);
    assert.equal(report.submissions.length, 0);
    report.original_menu_and_cancel = true;
    report.completed_row_excluded = true;

    report.step = "批准原批次并等待后台执行";
    await (await menu()).click();
    await confirmation().getByRole("button", { name: "确认生成", exact: true }).click();
    const running = await graph(owner, document => {
        const batch = latestBatch(document);
        return batch?.items.length === 2 && batch.items.every(item => item.taskId && ["running", "queued"].includes(item.status));
    });
    const batch = latestBatch(running);
    const targets = batch.items.map(item => item.nodeId);
    const taskIds = batch.items.map(item => item.taskId);
    report.batch_id = batch.id;
    report.running_task_ids = taskIds;
    report.running_prompts = Object.fromEntries(targets.map(id => [id, running.nodes.find(node => node.id === id).metadata.prompt]));
    assert.equal(report.submissions.length, 2);
    assert.deepEqual(batch.items.map(item => item.rowId), rowIds.slice(1));
    assert.deepEqual(running.nodes.find(node => node.id === "completed-image"), originalImage);
    assert.ok(targets.every(id => !running.nodes.find(node => node.id === id).metadata.content));
    const activeMenu = await menu();
    assert.equal(await activeMenu.getAttribute("aria-disabled"), "true");
    await script().getByRole("button", { name: "更多操作", exact: true }).click();
    report.active_menu_disabled = true;
    await page.screenshot({ path: resolve(output, "storyboard-media-running.png") });

    report.step = "关闭旧窗口，仅 cookies 恢复运行批次";
    await fresh();
    const restored = await graph(owner, document => latestBatch(document)?.items.every(item => item.taskId));
    assert.equal(latestBatch(restored).id, batch.id);
    assert.deepEqual(latestBatch(restored).items.map(item => item.taskId), taskIds);
    assert.deepEqual(scriptData(restored).metadata.storyboard.rows.map(row => row.id), rowIds);
    assert.equal(report.submissions.length, 2);
    report.running_cookie_only_recovery = true;
    report.same_tasks_and_batch = true;
    await page.screenshot({ path: resolve(output, "storyboard-media-running-restored.png") });
    await writeFile(config.releaseFile, "original storyboard running recovery verified\n");

    report.step = "后台归档回填及批次完成";
    const completed = await graph(owner, document => latestBatch(document)?.status === "completed" && targets.every(id => document.nodes.find(node => node.id === id)?.metadata.status === "success"));
    assert.deepEqual(latestBatch(completed).items.map(item => item.taskId), taskIds);
    assert.deepEqual(completed.nodes.find(node => node.id === "completed-image"), originalImage);
    report.output_nodes = [];
    for (const id of targets) {
        const node = completed.nodes.find(node => node.id === id);
        const key = node.metadata.storageKey;
        assert.ok(key.startsWith("resource:"));
        const response = await owner.request.get(`${origin}/api/v1/canvas-runtime/resources/${key.slice(9)}/file`);
        assert.ok(response.ok());
        report.output_nodes.push({ id, task_id: node.metadata.taskId, storage_key: key, sha256: createHash("sha256").update(await response.body()).digest("hex") });
    }
    await fit();
    if (config.kind === "video") {
        const withPreviews = await previewsReady(["completed-image", ...targets]);
        report.video_previews = [];
        for (const id of ["completed-image", ...targets]) {
            const preview = withPreviews.nodes.find(node => node.id === id).metadata.videoPreview;
            const response = await owner.request.get(`${origin}/api/v1/canvas-runtime/resources/${preview.storageKey.slice(9)}/file`);
            assert.ok(response.ok());
            report.video_previews.push({ id, ...preview, sha256: createHash("sha256").update(await response.body()).digest("hex") });
        }
        assert.equal(report.resource_posts, 3);
        report.resource_posts_after_completion = report.resource_posts;
    }
    await imagesReady(targets);
    const completedMenu = await menu();
    if (config.mode === "action_board") {
        assert.notEqual(await completedMenu.getAttribute("aria-disabled"), "true");
        await completedMenu.click();
        await page.locator(".ant-message").getByText("动作拆分板已存在", { exact: true }).waitFor({ state: "attached" });
        assert.equal(await confirmation().count(), 0);
        assert.equal(report.submissions.length, 2);
        assert.equal(latestBatch(await graph()).id, batch.id);
    } else {
        assert.equal(await completedMenu.getAttribute("aria-disabled"), "true");
        await script().getByRole("button", { name: "更多操作", exact: true }).click();
    }
    report.completed_menu_disabled = true;
    await page.screenshot({ path: resolve(output, "storyboard-media-completed.png") });
    await fresh();
    await imagesReady(targets);
    assert.equal(latestBatch(await graph()).id, batch.id);
    assert.equal(latestBatch(await graph()).status, "completed");
    assert.equal(report.submissions.length, 2);
    if (config.kind === "video") {
        const restoredPreviews = await previewsReady(["completed-image", ...targets]);
        for (const preview of report.video_previews) assert.equal(restoredPreviews.nodes.find(node => node.id === preview.id).metadata.videoPreview.storageKey, preview.storageKey);
        assert.equal(report.resource_posts, report.resource_posts_after_completion);
    }
    report.final_cookie_only_recovery = true;
    await page.screenshot({ path: resolve(output, "storyboard-media-completed-restored.png") });

    report.step = "成员读取共享作品、私人任务和批次隔离";
    const shared = await graph(member);
    assert.equal(scriptData(shared).metadata.generationBatches, undefined);
    assert.deepEqual(scriptData(shared).metadata.storyboard.rows.map(row => row.id), rowIds);
    for (const node of report.output_nodes) {
        const publicNode = shared.nodes.find(item => item.id === node.id);
        assert.equal(publicNode.metadata.storageKey, node.storage_key);
        for (const key of ["prompt", "composerContent", "model", "taskId"]) assert.equal(publicNode.metadata[key], undefined);
    }
    await open(member);
    await fit();
    await imagesReady(targets);
    report.member_shared_works_private_batch_hidden = true;
    await page.screenshot({ path: resolve(output, "storyboard-media-member.png") });
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    assert.equal(report.resource_posts, config.kind === "video" ? 3 : 0);
    assert.equal(report.submissions.length, 2);
    report.step = "complete";
} catch (error) {
    report.error = error.stack ?? String(error);
    if (page && !page.isClosed()) await page.screenshot({ path: resolve(output, "failed.png") }).catch(() => {});
    process.exitCode = 1;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
    process.stdout.write(JSON.stringify(report) + "\n");
}
