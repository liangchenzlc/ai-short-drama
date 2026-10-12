import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4193";
const tasks = "/api/v1/canvas-runtime/tasks";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = { scope: "original canvas UI with real Agnes", scenario: config.scenario, width: config.width, step: "start", task_posts: 0, page_errors: [], http_errors: [] };
const server = await createServer({ server: { host: "127.0.0.1", port: 4193, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}`);
    return response.json();
}

async function context(cookies) {
    const current = await browser.newContext({ viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference", ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    current.setDefaultTimeout(30000);
    current.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await current.request.post(origin + "/api/v1/auth/login", { headers: { Origin: origin }, data: { username: config.username, password: config.password } }));
    const csrf = (await current.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await current.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return current;
}

async function open(current) {
    page = await current.newPage();
    page.on("pageerror", error => report.page_errors.push(error.message));
    page.on("request", request => {
        if (new URL(request.url()).pathname === tasks && request.method() === "POST") report.task_posts++;
    });
    page.on("response", response => {
        const path = new URL(response.url()).pathname;
        if (path.startsWith("/api/") && response.status() >= 400) report.http_errors.push({ path, status: response.status() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-editor] [data-canvas-viewport]").waitFor();
    const close = page.getByRole("button", { name: "关闭助手", exact: true });
    if (await close.isVisible()) await close.click();
    await fit();
    await page.evaluate(() => document.fonts.ready);
    if (config.scenario === "image-reference") {
        const image = page.locator('[data-node-id="agnes-reference"]').locator("img").first();
        await image.waitFor({ state: "visible" });
        const visible = await image.evaluate(async image => {
            await image.decode();
            const response = await fetch(image.currentSrc);
            if (!response.ok) throw new Error(`参考图读取失败：HTTP ${response.status}`);
            const digest = await crypto.subtle.digest("SHA-256", await response.arrayBuffer());
            return { width: image.naturalWidth, height: image.naturalHeight, sha256: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("") };
        });
        assert.equal(visible.sha256, config.reference?.sha256);
        assert.equal(visible.width, config.reference?.width);
        assert.equal(visible.height, config.reference?.height);
        report.reference_views = [...(report.reference_views || []), visible];
    }
}

async function fit() {
    const focused = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focused.isVisible()) await focused.getByRole("button", { name: "退出专注模式", exact: true }).press("Control+0");
    else await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await page.waitForFunction(async () => {
        const viewport = document.querySelector("[data-canvas-viewport]");
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

async function eventually(readValue, predicate, limit = 900000) {
    const deadline = Date.now() + limit;
    let last;
    while (Date.now() < deadline) {
        last = await readValue();
        if (predicate(last)) return last;
        await new Promise(resolve => setTimeout(resolve, 300));
    }
    assert.fail(`真实任务未在时限内完成：${last?.status || "unknown"}`);
}

async function graph(current, predicate = () => true) {
    return eventually(async () => (await read(await current.request.get(origin + path + "/my-document"))).source_document, predicate, 45000);
}

async function verifyMedia(current, nodeId, task) {
    const result = JSON.parse(task.resultJson);
    if (config.kind === "text") {
        report.text = result.text;
        assert.ok(report.text.includes("红色礼盒"));
        await page.locator(`[data-node-id="${nodeId}"]`).getByText(report.text, { exact: true }).waitFor();
        return;
    }
    const media = config.kind === "image" ? result.images[0] : result.video;
    assert.match(media.storageKey, /^resource:[1-9]\d*$/);
    const response = await current.request.get(origin + media.url);
    assert.ok(response.ok());
    report.sha256 = createHash("sha256").update(await response.body()).digest("hex");
    report.storage_key = media.storageKey;
    const node = page.locator(`[data-node-id="${nodeId}"]`);
    if (config.kind === "image") {
        await node.locator("img").first().waitFor();
        const dimensions = await node.locator("img").first().evaluate(async image => {
            await image.decode();
            return { width: image.naturalWidth, height: image.naturalHeight };
        });
        assert.ok(dimensions.width > 0 && dimensions.height > 0);
        report.decoded = dimensions;
    } else {
        await node.getByRole("button", { name: /^播放 / }).first().click();
        const video = node.locator("video").first();
        await video.waitFor();
        await video.evaluate(async media => {
            if (media.readyState >= 1) return;
            await new Promise((resolve, reject) => {
                media.addEventListener("loadedmetadata", resolve, { once: true });
                media.addEventListener("error", () => reject(new Error("真实视频解码失败")), { once: true });
            });
        });
        report.decoded = await video.evaluate(media => ({ width: media.videoWidth, height: media.videoHeight, duration: media.duration }));
        assert.ok(report.decoded.width > 0 && report.decoded.height > 0);
        assert.ok(report.decoded.duration >= 3.8 && report.decoded.duration <= 4.5);
        await page.waitForFunction(nodeId => {
            const node = document.querySelector(`[data-node-id="${nodeId}"]`);
            const media = node?.querySelector("video");
            const indicator = node?.querySelector(".vds-buffering-spinner");
            return media && media.currentTime > 0 && indicator && Number(getComputedStyle(indicator).opacity) === 0;
        }, nodeId);
        report.real_playback = true;
    }
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    let owner = await context();
    await open(owner);
    report.step = "original prompt and generation button";
    if (config.kind === "video") {
        const initial = await graph(owner);
        const parameters = initial.nodes.find(node => node.id === "runtime-node").metadata;
        assert.equal(parameters.generateAudio, "false");
        assert.equal(parameters.watermark, "false");
    }
    await page.locator('[data-node-id="runtime-node"]').click({ position: { x: 20, y: 12 } });
    const label = { text: "文本提示词", image: "图片提示词", video: "视频提示词" }[config.kind];
    await page.getByRole("textbox", { name: label, exact: true }).fill(config.prompt);
    const admitted = page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST");
    await page.getByRole("button", { name: "生成", exact: true }).click();
    const task = await read(await admitted);
    assert.equal(task.model, config.model);
    assert.equal((await admitted).request().postDataJSON().logicalModelId, config.modelId);
    report.task_id = task.id;
    report.node_id = task.clientContext.nodeId;
    const saved = await graph(owner);
    assert.equal(saved.nodes.find(node => node.id === "runtime-node").metadata.prompt, config.prompt);
    assert.equal(JSON.parse(task.inputJson).prompt, config.prompt);
    report.source_saved_before_admission = true;
    const running = await eventually(async () => read(await owner.request.get(origin + tasks + "/" + task.id)), task => task.status === "running", 30000);
    assert.equal(running.id, task.id);
    await page.screenshot({ path: resolve(output, "pending.png") });
    report.step = "cookies-only recovery before real provider submission";
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(cookies);
    await open(owner);
    const restored = await read(await owner.request.get(origin + tasks + "/" + task.id));
    assert.equal(restored.id, task.id);
    assert.equal(restored.status, "running");
    assert.equal(report.task_posts, 1);
    report.cookies_only_running_recovery = true;
    await writeFile(config.releasePath, "Same task restored in a fresh cookies-only context; allow real supplier call.\n");
    report.step = "real provider result and original consumer";
    const ready = await eventually(async () => read(await owner.request.get(origin + tasks + "/" + task.id)), task => ["succeeded", "failed", "cancelled"].includes(task.status));
    report.terminal_status = ready.status;
    if (ready.status !== "succeeded") {
        report.provider_error = { code: ready.errorCode, message: ready.error };
        report.can_retry = ready.canRetry;
        report.can_resume = ready.canResume;
        if (ready.errorCode === "provider_queue_full") {
            assert.equal(ready.canRetry, true);
            assert.equal(ready.canResume, false);
            await graph(owner, document => document.nodes.find(node => node.id === report.node_id)?.metadata.status === "error");
            await fit();
            const failed = page.locator(`[data-node-id="${report.node_id}"]`);
            await failed.locator(".generation-failure-reason").waitFor({ state: "visible" });
            report.visible_queue_rejection = await failed.locator(".generation-failure-reason").innerText();
            assert.match(report.visible_queue_rejection, /队列已满.*未创建供应商任务/);
            await failed.getByRole("button", { name: "重新生成", exact: true }).waitFor({ state: "visible" });
            report.visible_retry = true;
            await page.screenshot({ path: resolve(output, "queue-full.png") });
            await failed.getByRole("button", { name: "查看详情", exact: true }).click();
            await page.getByRole("dialog", { name: "任务详情", exact: true }).getByRole("status").filter({ hasText: "未创建供应商任务" }).waitFor({ state: "visible" });
            report.visible_task_detail = true;
            assert.equal(report.task_posts, 1);
            assert.deepEqual(report.page_errors, []);
            assert.deepEqual(report.http_errors, []);
        }
        assert.fail(`真实供应商未完成成功验收：${ready.errorCode || ready.status}`);
    }
    const bound = await graph(owner, document => document.nodes.find(node => node.id === report.node_id)?.metadata.status === "success");
    assert.equal(bound.nodes.find(node => node.id === report.node_id).metadata.taskId, task.id);
    report.original_consumer_bound = true;
    await fit();
    await verifyMedia(owner, report.node_id, ready);
    await page.screenshot({ path: resolve(output, "generated.png") });
    report.step = "fresh terminal recovery without another post";
    const finalCookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(finalCookies);
    await open(owner);
    await verifyMedia(owner, report.node_id, ready);
    const listed = await read(await owner.request.get(origin + tasks + "?projectId=" + config.sourceKey));
    assert.deepEqual(listed.map(task => task.id), [report.task_id]);
    const fresh = await graph(owner);
    assert.equal(fresh.nodes.find(node => node.id === report.node_id).metadata.taskId, report.task_id);
    if (report.storage_key) assert.equal(fresh.nodes.find(node => node.id === report.node_id).metadata.storageKey, report.storage_key);
    report.fresh_terminal_recovery = true;
    assert.equal(report.task_posts, 1);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    await page.screenshot({ path: resolve(output, "fresh.png") });
    report.step = "complete";
    process.stdout.write(JSON.stringify(report));
} catch (error) {
    report.failure = { type: error.name, message: error.message, stack: error.stack };
    if (page && !page.isClosed()) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => undefined);
    process.stderr.write(JSON.stringify(report) + "\n" + error.stack);
    process.exitCode = 1;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    await browser?.close();
    await server.close();
}
