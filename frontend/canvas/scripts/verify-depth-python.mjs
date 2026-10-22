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
const documentPath = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}/my-document`;
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = { step: "start", width: config.width, reopen: config.reopen, drag_surface: config.dragSurface || "body", title_drags: [], task_posts: 0, resource_posts: 0, page_errors: [], http_errors: [] };
assert.ok(["body", "title"].includes(report.drag_surface));
const server = await createServer({ cacheDir: resolve(".runtime/vite-depth"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let owner;
let page;
const source = () => page.locator('[data-node-id="media-video"]');
const child = document => document.nodes.find(node => node.metadata?.depthSourceNodeId === "media-video");
async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function eventually(readValue, predicate, timeout = 60000) {
    const deadline = Date.now() + timeout;
    let value;
    while (Date.now() < deadline) {
        value = await readValue();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`深度状态未达到预期：${JSON.stringify(value)}`);
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
async function graph(context = owner, predicate = () => true) {
    return eventually(async () => (await read(await context.request.get(`${origin}${documentPath}`))).source_document, predicate);
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
async function open(context) {
    page = await context.newPage();
    page.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    page.on("request", request => {
        const path = new URL(request.url()).pathname;
        if (request.method() === "POST" && path === "/api/v1/canvas-runtime/depth-captures") report.task_posts++;
        if (request.method() === "POST" && path === "/api/v1/canvas-runtime/resources") report.resource_posts++;
    });
    page.on("response", response => {
        if (response.status() >= 400) report.http_errors.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    await source().waitFor({ state: "visible" });
    await fit();
}
async function save() {
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}
async function editPending(title) {
    let before = child(await graph());
    const card = page.locator(`[data-node-id="${before.id}"]`);
    await fit();
    await card.click({ position: { x: 12, y: 36 } });
    const bounds = await card.boundingBox();
    assert.ok(bounds && bounds.width > 40 && bounds.height > 16);
    let landing = { x: bounds.x + 20, y: bounds.y + 8 };
    let titleDrag;
    if (report.drag_surface === "title") {
        await save();
        const synchronized = await eventually(async () => {
            const saved = child(await graph());
            const painted = await card.evaluate((element, position) => {
                const expected = document.createElement("div").style;
                expected.transform = `translate(${position.x}px, ${position.y}px)`;
                return { transform: element.style.transform, expected_transform: expected.transform,
                    width: Number.parseFloat(element.style.width), height: Number.parseFloat(element.style.height) };
            }, saved.position);
            return { saved, painted };
        }, ({ saved, painted }) => painted.transform === painted.expected_transform
            && saved.width === painted.width && saved.height === painted.height);
        before = synchronized.saved;
        const icon = card.locator(".canvas-node-external-header > div > svg");
        assert.equal(await icon.count(), 1);
        landing = await icon.evaluate(element => {
            const box = element.getBoundingClientRect();
            const describe = (x, y) => {
                const hit = document.elementFromPoint(x, y);
                return { x, y, width: box.width, height: box.height, node_id: hit?.closest("[data-node-id]")?.getAttribute("data-node-id"), button: hit?.closest("button")?.getAttribute("aria-label") ?? null, tag: hit?.tagName,
                    cursor: hit ? getComputedStyle(hit).cursor : null, header: Boolean(hit?.closest(".canvas-node-external-header")), icon: hit === element || element.contains(hit) };
            };
            const center = describe(box.x + box.width / 2, box.y + box.height / 2);
            const candidates = [center, ...[0.25, 0.125, 0.375, 0.625, 0.875].flatMap(y => [0.5, 0.25, 0.75].map(x => describe(box.x + box.width * x, box.y + box.height * y)))];
            const exposed = candidates.find(hit => hit.icon && hit.header && hit.button === null);
            return { ...exposed, center_hit: center, candidates };
        });
        assert.equal(landing.node_id, before.id);
        assert.equal(landing.button, null);
        assert.equal(landing.icon, true, "必须命中原可见标题SVG，不得使用被缩放手柄覆盖的中心");
        assert.ok(landing.width > 0 && landing.height > 0);
        await page.evaluate(() => {
            window.__depthTitlePointerEvents = [];
            for (const type of ["pointerdown", "mousedown"]) {
                document.addEventListener(type, event => {
                    const target = event.target;
                    window.__depthTitlePointerEvents.push({ type, x: event.clientX, y: event.clientY, tag: target?.tagName,
                        node_id: target?.closest?.("[data-node-id]")?.getAttribute("data-node-id") ?? null,
                        button: target?.closest?.("button")?.getAttribute("aria-label") ?? null,
                        icon: Boolean(target?.closest?.(".canvas-node-external-header > div > svg")) });
                }, { capture: true, once: true });
            }
        });
        titleDrag = { before: before.position, landing };
        report.title_drags.push(titleDrag);
    }
    await page.mouse.move(landing.x, landing.y);
    await page.mouse.down();
    await page.mouse.move(landing.x + 45, landing.y + 65, { steps: 16 });
    await page.mouse.up();
    if (titleDrag) titleDrag.events = await page.evaluate(() => window.__depthTitlePointerEvents);
    const moved = child(await graph(owner, document => Math.hypot(child(document).position.x - before.position.x, child(document).position.y - before.position.y) > 20));
    if (titleDrag) {
        titleDrag.after = moved.position;
        assert.equal(titleDrag.events.length, 2);
        for (const event of titleDrag.events) {
            assert.equal(event.node_id, before.id);
            assert.equal(event.button, null);
            assert.equal(event.icon, true);
        }
        assert.equal(moved.metadata.status, "loading");
        assert.equal(moved.metadata.taskId, before.metadata.taskId);
    }
    await card.getByRole("button", { name: /^编辑节点名称：/ }).click();
    const name = card.getByRole("textbox", { name: "节点名称", exact: true });
    await name.fill(title);
    await name.press("Enter");
    await save();
    const edited = child(await graph(owner, document => child(document)?.title === title));
    assert.equal(edited.metadata.status, "loading");
    return { title: edited.title, position: edited.position };
}
try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined });
    owner = await context(config.username);
    await open(owner);
    await source().click({ position: { x: 12, y: 36 } });
    await source().hover();
    await page.getByRole("toolbar", { name: "节点快捷工具", exact: true }).waitFor({ state: "visible" });
    await page.getByRole("button", { name: "视频处理", exact: true }).click();
    await writeFile(resolve(output, "depth-menu.yml"), await page.locator("body").ariaSnapshot());
    await page.getByRole("menuitem", { name: "深度动作捕捉", exact: true }).click();
    report.step = "submitted";
    const pending = await graph(owner, document => child(document)?.metadata?.taskId && child(document).metadata.status === "loading");
    const taskId = child(pending).metadata.taskId;
    report.task_id = taskId;
    assert.equal(report.task_posts, 1);
    await save();
    await fit();
    await page.screenshot({ path: resolve(output, "depth-loading.png"), fullPage: true });
    if (config.reopen) {
        report.step = "原窗口运行中编辑";
        report.before_reopen_edits = await editPending("关闭前的新标题");
        const cookies = await owner.cookies();
        await owner.close();
        owner = await context(undefined, cookies);
        await open(owner);
        report.step = "fresh-running";
        const restored = await graph(owner, document => child(document)?.metadata?.taskId === taskId);
        assert.equal(child(restored).metadata.status, "loading");
        assert.equal(child(restored).title, report.before_reopen_edits.title);
        assert.deepEqual(child(restored).position, report.before_reopen_edits.position);
        report.cookie_only_recovery = true;
    }
    report.step = "原界面拖拽与改名后等待同一任务";
    report.live_edits = await editPending("处理中保留的新标题");
    const task = await read(await owner.request.get(`${origin}/api/v1/canvas-runtime/tasks/${taskId}`));
    assert.ok(["queued", "running"].includes(task.status));
    await writeFile(resolve(config.releasePath), "released by cookie-only browser\n");
    const complete = await graph(owner, document => child(document)?.metadata?.status === "success" && child(document).metadata.storageKey?.startsWith("resource:"));
    const node = child(complete);
    assert.equal(node.metadata.taskId, taskId);
    assert.equal(node.title, report.live_edits.title);
    assert.deepEqual(node.position, report.live_edits.position);
    assert.ok(complete.connections.some(edge => edge.fromNodeId === "media-video" && edge.toNodeId === node.id));
    await graph(owner, document => child(document)?.metadata?.videoPreview?.storageKey?.startsWith("resource:"));
    await save();
    const resourceId = node.metadata.storageKey.slice("resource:".length);
    const response = await owner.request.get(`${origin}/api/v1/canvas-runtime/resources/${resourceId}/file`);
    assert.ok(response.ok());
    const bytes = await response.body();
    await writeFile(resolve(output, "depth.mp4"), bytes);
    report.resource_id = resourceId;
    report.checksum = createHash("sha256").update(bytes).digest("hex");
    await fit();
    const card = page.locator(`[data-node-id="${node.id}"]`);
    await card.getByRole("button", { name: /^播放 / }).first().click();
    await eventually(() => card.locator("video").first().evaluate(element => element.videoWidth === 1920 && element.readyState >= 2), Boolean);
    await page.screenshot({ path: resolve(output, "depth-restored.png"), fullPage: true });
    const member = await context(config.memberUsername);
    assert.equal((await member.request.get(`${origin}/api/v1/canvas-runtime/tasks/${taskId}`)).status(), 404);
    assert.equal((await member.request.get(`${origin}${documentPath}`)).status(), 404);
    assert.equal((await member.request.get(`${origin}/api/v1/canvas-runtime/resources/${resourceId}/file`)).status(), 404);
    assert.equal(report.task_posts, 1);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    report.live_edits_preserved = true;
    report.step = "complete";
    process.stdout.write(JSON.stringify(report));
} catch (error) {
    report.error = error.stack ?? String(error);
    if (page && !page.isClosed()) {
        await writeFile(resolve(output, "failure.yml"), await page.locator("body").ariaSnapshot()).catch(() => {});
        await page.screenshot({ path: resolve(output, "failure.png"), fullPage: true }).catch(() => {});
    }
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
