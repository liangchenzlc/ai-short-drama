import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

// 原绘图搜索入口、真实笔画与精确版本引用；供应商为受控本机 HTTP。
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const resources = "/api/v1/canvas-runtime/resources";
const tasks = "/api/v1/canvas-runtime/tasks";
const output = resolve(config.outputDirectory);
const report = { step: "start", protocol: config.protocol, page_errors: [], failed_routes: [], task_post_count: 0, resource_post_count: 0, initial_drawing_missing: [], resource_downloads: [] };
const digest = value => createHash("sha256").update(value).digest("hex");
await mkdir(output, { recursive: true });
const server = await createServer({ server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
let drawingPath;
let drawingSaved = false;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}

function observe(target) {
    target.on("pageerror", error => report.page_errors.push(error.message));
    target.on("request", request => {
        const route = new URL(request.url()).pathname;
        if (request.method() === "POST" && route === tasks) report.task_post_count++;
        if (request.method() === "POST" && route === resources) report.resource_post_count++;
    });
    target.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (response.ok() && route.startsWith(`${resources}/`) && route.endsWith("/file")) report.resource_downloads.push(route);
        if (response.status() >= 400 && route.startsWith("/api/")) {
            if (!drawingSaved && response.status() === 404 && route.startsWith(`/api/v1/canvas-runtime/canvas-projects/${config.sourceKey}/drawings/`) && response.request().method() === "GET") report.initial_drawing_missing.push(route);
            else report.failed_routes.push({ path: route, status: response.status() });
        }
    });
}

async function login(username, cookies) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference", ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    context.setDefaultTimeout(20000);
    context.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return context;
}

async function graph(context, predicate) {
    let current;
    for (let attempt = 0; attempt < 150; attempt++) {
        current = (await read(await context.request.get(`${origin}${path}/my-document`))).source_document;
        if (predicate(current)) return current;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`真实绘图画布没有达到预期：${JSON.stringify(current)}`);
}

async function openCanvas(target) {
    await target.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await target.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await target.evaluate(() => document.fonts.ready);
}

async function openDrawing(target, id) {
    await target.locator(`[data-node-id="${id}"]`).click({ button: "right" });
    await target.locator("[data-canvas-context-menu]").getByRole("button", { name: "打开绘图", exact: true }).click();
    await target.getByRole("button", { name: "保存绘图", exact: true }).waitFor({ timeout: 60000 });
    await target.waitForFunction(() => [...document.querySelectorAll("button")].some(button => button.textContent === "保存绘图" && !button.disabled), undefined, { timeout: 60000 });
}

async function saveCanvas(target) {
    const live = await target.locator("[data-canvas-viewport]").evaluate(viewport => ({ x: parseFloat(viewport.style.getPropertyValue("--canvas-live-x")), y: parseFloat(viewport.style.getPropertyValue("--canvas-live-y")), k: parseFloat(viewport.style.getPropertyValue("--canvas-live-scale")) }));
    await graph(target.context(), document => Object.entries(live).every(([key, value]) => document.viewport[key] === value));
    await target.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await target.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
    await target.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await login(config.username);
    const member = await login(config.memberUsername);
    const outsider = await login(config.outsideUsername);
    page = await owner.newPage();
    observe(page);
    await openCanvas(page);
    report.step = "原右键菜单搜索创建绘图";
    await page.locator("[data-canvas-viewport]").click({ button: "right", position: { x: 420, y: 350 } });
    await page.locator("[data-canvas-context-menu]").getByRole("button", { name: "添加节点", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "绘图", exact: true }).count(), 0, "源默认列表隐藏高级绘图入口");
    await page.getByRole("button", { name: "搜索节点", exact: true }).click();
    await page.getByRole("textbox", { name: "搜索节点", exact: true }).fill("绘图");
    await page.getByRole("button", { name: "绘图", exact: true }).click();
    const created = await graph(owner, document => document.nodes.length === 1 && document.nodes[0].type === "drawing");
    const drawing = created.nodes[0];
    assert.equal(drawing.metadata.drawingRevision ?? "0", "0");
    assert.equal(drawing.metadata.drawingShapeCount ?? 0, 0);
    drawingPath = `/api/v1/canvas-runtime/canvas-projects/${config.sourceKey}/drawings/${drawing.metadata.drawingId}`;
    assert.ok(report.initial_drawing_missing.every(route => route === drawingPath));
    report.drawing_node_id = drawing.id;
    report.drawing_id = drawing.metadata.drawingId;
    report.original_search_created_drawing = true;

    report.step = "空绘图经原连接点创建文字并拒绝生成";
    const drawingNode = page.locator(`[data-node-id="${drawing.id}"]`);
    await drawingNode.hover();
    await drawingNode.locator('[data-canvas-connection-rail="right"]').click();
    await page.locator("[data-connection-create-menu]").getByRole("button", { name: "文本", exact: true }).click();
    const connected = await graph(owner, document => document.nodes.length === 2 && document.connections.length === 1);
    const text = connected.nodes.find(node => node.type === "text");
    assert.ok(text);
    assert.equal(text.position.x, drawing.position.x + drawing.width + 96);
    report.text_node_id = text.id;
    const textNode = page.locator(`[data-node-id="${text.id}"]`);
    await textNode.click({ position: { x: 20, y: 12 } });
    await page.getByRole("textbox", { name: "文本提示词", exact: true }).fill(config.prompt);
    await page.getByRole("button", { name: "生成", exact: true }).click();
    await page.locator(".ant-message").getByText(/绘图.*为空，请先完成绘图再生成/).waitFor({ state: "attached" });
    assert.equal(report.task_post_count, 0);
    assert.equal(report.resource_post_count, 0);
    report.empty_drawing_rejected = true;

    report.step = "原 Excalidraw 真实笔画保存及关闭回执";
    await openDrawing(page, drawing.id);
    const pen = page.getByRole("radio", { name: "自由书写", exact: true });
    await pen.locator("..").click();
    assert.ok(await pen.isChecked());
    const surface = await page.locator(".excalidraw__canvas.interactive").boundingBox();
    assert.ok(surface);
    await page.mouse.move(surface.x + surface.width * 0.45, surface.y + surface.height * 0.5);
    await page.mouse.down();
    await page.mouse.move(surface.x + surface.width * 0.56, surface.y + surface.height * 0.58, { steps: 12 });
    await page.mouse.up();
    const savedResponse = page.waitForResponse(response => new URL(response.url()).pathname === drawingPath && response.request().method() === "PUT");
    await page.getByRole("button", { name: "保存绘图", exact: true }).click();
    const saved = (await read(await savedResponse)).drawing;
    drawingSaved = true;
    assert.equal(saved.shapeCount, 1);
    await page.getByRole("button", { name: "保存绘图", exact: true }).waitFor({ state: "visible" });
    const closedResponse = page.waitForResponse(response => new URL(response.url()).pathname === drawingPath && response.request().method() === "PUT");
    await page.getByRole("button", { name: "关闭绘图编辑器", exact: true }).click();
    const current = (await read(await closedResponse)).drawing;
    await page.locator(".canvas-drawing-editor-modal").waitFor({ state: "hidden" });
    await graph(owner, document => document.nodes.find(node => node.id === drawing.id)?.metadata.drawingRevision === current.revision);
    assert.equal(current.shapeCount, 1);
    assert.equal(current.render.background, "white");
    assert.equal(current.render.storageKey, `resource:${current.render.resourceId}`);
    assert.ok(current.previewResourceId && current.render.width > 0 && current.render.height > 0);
    const bytes = await (await owner.request.get(`${origin}${resources}/${current.render.resourceId}/file`)).body();
    report.drawing = { revision: current.revision, render_resource_id: current.render.resourceId, preview_resource_id: current.previewResourceId, render_sha256: digest(bytes), snapshot_sha256: digest(JSON.stringify(current.snapshot)), width: current.render.width, height: current.render.height };
    report.pointer_drawing_saved = true;
    const uploadsBeforeGeneration = report.resource_post_count;

    report.step = "文字生成引用绘图当前永久版本";
    await textNode.click({ position: { x: 20, y: 12 } });
    await page.getByRole("textbox", { name: "文本提示词", exact: true }).fill(config.prompt);
    const admission = page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST");
    await page.getByRole("button", { name: "生成", exact: true }).click();
    const task = await read(await admission);
    report.task_id = task.id;
    const declared = JSON.parse(task.inputJson);
    assert.equal(declared.prompt, config.prompt);
    assert.deepEqual(declared.referenceImages.map(image => image.storageKey), [`resource:${current.render.resourceId}`]);
    assert.equal(report.resource_post_count, uploadsBeforeGeneration);
    const admitted = await graph(owner, document => document.nodes.find(node => node.id === text.id)?.metadata.taskId === task.id);
    assert.equal(admitted.nodes.length, 2);
    report.exact_drawing_reference_reused = true;

    report.step = "绘图文字私人增量保持原 loading";
    let draft;
    for (let attempt = 0; attempt < 100; attempt++) {
        draft = await read(await owner.request.get(`${origin}${tasks}/${task.id}/text-deltas`));
        if (draft.textDraft === config.first) break;
        await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(draft.textDraft, config.first);
    assert.equal(draft.status, "running");
    assert.deepEqual(draft.deltas.map(delta => [delta.sequence, delta.content]), [[1, config.first]]);
    const loading = (await read(await owner.request.get(`${origin}${path}/my-document`))).source_document.nodes.find(node => node.id === text.id);
    assert.equal(loading.metadata.status, "loading");
    assert.ok(!loading.metadata.content);
    assert.ok(!(await textNode.innerText()).includes(config.first));
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}/text-deltas`)).status(), 404);
    report.private_delta_kept_loading = true;
    await page.screenshot({ path: resolve(output, "loading.png") });
    const binding = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && response.request().method() === "POST" && response.ok());
    await writeFile(config.releasePath, "Original drawing text loading state observed.\n");
    const bound = await read(await binding);
    assert.equal(bound.result.node.id, text.id);
    assert.equal(bound.result.node.metadata.content, config.first + config.second);
    const final = await graph(owner, document => document.nodes.find(node => node.id === text.id)?.metadata.content === config.first + config.second);
    assert.equal(final.nodes.length, 2);
    assert.equal(final.connections.length, 1);
    assert.deepEqual((await read(await owner.request.get(`${origin}${drawingPath}`))).drawing, current);
    report.original_consumer_bound_text = true;

    report.step = "关闭原窗口后全新 context 恢复绘图正文";
    await saveCanvas(page);
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    const fresh = await login(undefined, cookies);
    page = await fresh.newPage();
    observe(page);
    await openCanvas(page);
    await page.locator(`[data-node-id="${text.id}"]`).getByText(config.first + config.second, { exact: true }).waitFor({ state: "visible" });
    const preview = page.locator(`[data-node-id="${drawing.id}"]`).getByAltText("绘图预览", { exact: true });
    await preview.waitFor({ state: "visible" });
    await preview.evaluate(image => image.decode());
    assert.ok(await preview.evaluate(image => image.naturalWidth > 0 && image.naturalHeight > 0));
    assert.ok(report.resource_downloads.includes(`${resources}/${current.previewResourceId}/file`));
    await openDrawing(page, drawing.id);
    const restored = await page.evaluate(async ({ key, id }) => {
        const { loadCanvasDrawing } = await import("/canvas-app/src/lib/canvas/canvas-drawing-storage.ts");
        const saved = await loadCanvasDrawing(key, id);
        return { snapshot: saved.snapshot, revision: saved.revision, origin: saved.origin, shapeCount: saved.shapeCount };
    }, { key: config.sourceKey, id: drawing.metadata.drawingId });
    assert.deepEqual(restored.snapshot, current.snapshot);
    assert.equal(restored.revision, current.revision);
    assert.equal(restored.origin, "canonical");
    assert.equal(restored.shapeCount, 1);
    await page.screenshot({ path: resolve(output, "restored.png") });
    page = undefined;
    report.fresh_context_restored = true;

    report.step = "共享绘图正文及私人任务权限";
    assert.equal((await member.request.get(`${origin}${drawingPath}`)).status(), 404);
    assert.equal((await member.request.get(`${origin}${path}/my-document`)).status(), 404);
    const shared = (await read(await fresh.request.get(`${origin}${path}`))).source_document;
    assert.equal(shared.nodes.find(node => node.id === text.id).metadata.content, config.first + config.second);
    assert.equal((await member.request.get(`${origin}${resources}/${current.render.resourceId}/file`)).status(), 404);
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
    assert.equal((await outsider.request.get(`${origin}${drawingPath}`)).status(), 404);
    assert.equal((await outsider.request.get(`${origin}${resources}/${current.render.resourceId}/file`)).status(), 404);
    report.permissions_checked = true;
    assert.equal(report.task_post_count, 1);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.failed_routes, []);
    report.step = "complete";
} catch (error) {
    report.failure_stack = error.stack;
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    if (page) await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
