import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

// 原上传、连接点、生成和终态回填；真实本机 Chat/MinIO，不拦截成功请求。
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const resources = "/api/v1/canvas-runtime/resources";
const tasks = "/api/v1/canvas-runtime/tasks";
const output = resolve(".runtime/text-images-python-browser");
const report = { step: "start", page_errors: [], task_post_count: 0, page_text_event_count: 0, failed_routes: [] };
await mkdir(output, { recursive: true });
const server = await createServer({
    server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } },
    logLevel: "silent",
});
await server.listen();
let browser;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}

function observe(target) {
    target.on("pageerror", error => report.page_errors.push(error.message));
    target.on("request", request => {
        const route = new URL(request.url()).pathname;
        if (route === tasks && request.method() === "POST") report.task_post_count++;
        if (route.startsWith(`${tasks}/`) && route.endsWith("/text-events")) report.page_text_event_count++;
    });
    target.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (response.status() >= 400 && route.startsWith("/api/")) report.failed_routes.push({ path: route, status: response.status() });
    });
}

async function login(username) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference" });
    context.setDefaultTimeout(20000);
    context.setDefaultNavigationTimeout(60000);
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return context;
}

async function graph(context, predicate) {
    let current;
    for (let attempt = 0; attempt < 120; attempt++) {
        current = (await read(await context.request.get(`${origin}${path}/my-document`))).source_document;
        if (predicate(current)) return current;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`真实 API 文字画布没有达到预期：${JSON.stringify(current)}`);
}

async function openCanvas(target) {
    await target.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await target.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await target.evaluate(() => document.fonts.ready);
}

async function firstDraft(context, taskId) {
    let current;
    for (let attempt = 0; attempt < 70; attempt++) {
        current = await read(await context.request.get(`${origin}${tasks}/${taskId}/text-deltas`));
        if (current.textDraft === config.first) return current;
        await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.fail(`真实供应商第一段未持久化：${JSON.stringify(current)}`);
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await login(config.username);
    const member = await login(config.memberUsername);
    const outsider = await login(config.outsiderUsername);
    page = await owner.newPage();
    observe(page);
    await openCanvas(page);
    report.step = "原上传 input 创建两张图片";
    const originals = config.images.map(value => Buffer.from(value, "base64"));
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles(originals.map((buffer, index) => ({
        name: index === 0 ? "red-reference.png" : "blue-reference.png", mimeType: "image/png", buffer,
    })));
    const uploaded = await graph(owner, document => document.nodes.length === 2 && document.nodes.every(node => node.metadata?.assetId && node.metadata.storageKey?.startsWith("resource:")));
    report.images = [];
    for (const [index, title] of ["red-reference", "blue-reference"].entries()) {
        const source = uploaded.nodes.find(node => node.title.includes(title));
        assert.ok(source, `原上传应保留文件标题 ${title}`);
        const resourceId = source.metadata.storageKey.slice("resource:".length);
        const response = await owner.request.get(`${origin}${resources}/${resourceId}/file`);
        assert.ok(response.ok());
        assert.deepEqual(await response.body(), originals[index]);
        report.images.push({ node_id: source.id, resource_id: resourceId, asset_id: source.metadata.assetId, sha256: createHash("sha256").update(originals[index]).digest("hex") });
    }
    const [red, blue] = report.images.map(item => uploaded.nodes.find(node => node.id === item.node_id));

    report.step = "原输出连接点创建文字";
    await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    const redNode = page.locator(`[data-node-id="${red.id}"]`);
    await redNode.click();
    await redNode.hover();
    await redNode.locator('[data-canvas-connection-rail="right"]').click();
    const create = page.locator("[data-connection-create-menu]");
    await create.waitFor({ state: "visible" });
    await create.getByRole("button", { name: "文本", exact: true }).click();
    const connected = await graph(owner, document => document.nodes.length === 3 && document.connections.length === 1);
    const text = connected.nodes.find(node => node.type === "text");
    assert.ok(text);
    assert.deepEqual(connected.connections.map(edge => [edge.fromNodeId, edge.toNodeId]), [[red.id, text.id]]);
    assert.equal(text.position.x, red.position.x + red.width + 96);
    assert.ok(!text.metadata?.content);
    report.text_node_id = text.id;
    report.original_connection_created_text = true;

    report.step = "第二张图片经原连接点拖拽入文字";
    await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    const blueNode = page.locator(`[data-node-id="${blue.id}"]`);
    const textNode = page.locator(`[data-node-id="${text.id}"]`);
    await blueNode.click();
    await blueNode.hover();
    const from = await blueNode.locator('[data-canvas-connection-rail="right"]').boundingBox();
    const target = await textNode.boundingBox();
    assert.ok(from && target);
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(target.x, target.y + target.height / 2, { steps: 18 });
    await page.mouse.up();
    const linked = await graph(owner, document => document.connections.length === 2);
    assert.deepEqual(linked.connections.map(edge => [edge.fromNodeId, edge.toNodeId]), [[red.id, text.id], [blue.id, text.id]]);
    report.original_image_order_linked = true;

    report.step = "原生成按钮提交真实 Chat 图片任务";
    await textNode.click({ position: { x: 20, y: 12 } });
    const prompt = page.getByRole("textbox", { name: "文本提示词", exact: true });
    await prompt.waitFor({ state: "visible" });
    await prompt.fill(config.prompt);
    const [admitted] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === tasks && response.request().method() === "POST"),
        page.getByRole("button", { name: "生成", exact: true }).click(),
    ]);
    const task = await read(admitted);
    report.task_id = task.id;
    assert.equal(task.clientContext.nodeId, text.id);
    const declared = JSON.parse(task.inputJson);
    assert.equal(declared.prompt, config.prompt);
    assert.equal(declared.config.systemPrompt, config.systemPrompt);
    assert.deepEqual(declared.textOptions, { stream: true, thinking: false });
    assert.deepEqual(declared.referenceImages.map(item => item.storageKey), report.images.map(item => `resource:${item.resource_id}`));
    const admittedGraph = await graph(owner, document => document.nodes.some(node => node.id === text.id && node.metadata.taskId === task.id));
    assert.equal(admittedGraph.nodes.find(node => node.id === text.id).metadata.prompt, config.prompt);
    report.source_saved_before_admission = true;

    report.step = "供应商第一段已落库而原节点仍保持加载态";
    const draft = await firstDraft(owner, task.id);
    assert.equal(draft.status, "running");
    assert.equal(draft.complete, false);
    assert.deepEqual(draft.deltas.map(item => [item.sequence, item.content]), [[1, config.first]]);
    const inProgress = await read(await owner.request.get(`${origin}${path}/my-document`));
    const loading = inProgress.source_document.nodes.find(node => node.id === text.id);
    assert.equal(loading.metadata.status, "loading");
    assert.ok(!loading.metadata.content);
    assert.ok(!(await textNode.innerText()).includes(config.first));
    assert.equal(report.page_text_event_count, 0, "源普通文字节点不请求实时正文 SSE");
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}/text-deltas`)).status(), 404);
    report.first_draft = { text: draft.textDraft, sequences: draft.deltas.map(item => item.sequence) };
    report.private_delta_kept_original_loading = true;
    const binding = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind" && response.request().method() === "POST" && response.ok());
    await writeFile(config.releasePath, "Browser observed the actual private provider delta and the original loading node.\n");

    report.step = "原消费者终态直接回填";
    const bound = await read(await binding);
    assert.equal(bound.result.taskId, task.id);
    assert.equal(bound.result.node.id, text.id);
    assert.equal(bound.result.node.metadata.content, config.first + config.second);
    await textNode.getByText(config.first + config.second, { exact: true }).waitFor({ state: "visible" });
    const final = await graph(owner, document => document.nodes.find(node => node.id === text.id)?.metadata.content === config.first + config.second);
    assert.equal(final.nodes.length, 3);
    assert.equal(final.connections.length, 2);
    assert.deepEqual(final.nodes.filter(node => node.type === "image"), uploaded.nodes);
    report.original_images_unchanged = true;
    report.original_consumer_bound_same_node = true;

    report.step = "全新 context 恢复正文与 MinIO 原图";
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies: await owner.cookies(), origins: [] } });
    fresh.setDefaultTimeout(20000);
    fresh.setDefaultNavigationTimeout(60000);
    const restored = await fresh.newPage();
    observe(restored);
    const downloaded = new Set();
    restored.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (response.ok() && route.endsWith("/file")) downloaded.add(route);
    });
    await openCanvas(restored);
    await restored.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await restored.locator(`[data-node-id="${text.id}"]`).getByText(config.first + config.second, { exact: true }).waitFor({ state: "visible" });
    await restored.waitForFunction(items => items.every(item => {
        const image = document.querySelector(`[data-node-id="${item.node_id}"] img`);
        return image instanceof HTMLImageElement && image.complete && image.naturalWidth === 37 && image.naturalHeight === 19;
    }), report.images);
    for (const image of report.images) assert.ok(downloaded.has(`${resources}/${image.resource_id}/file`));
    await fresh.close();
    report.fresh_context_restored = true;

    report.step = "共享最终正文与私人任务权限";
    const shared = await read(await member.request.get(`${origin}${path}/my-document`));
    assert.equal(shared.source_document.nodes.find(node => node.id === text.id).metadata.content, config.first + config.second);
    assert.equal((await member.request.get(`${origin}${tasks}/${task.id}`)).status(), 404);
    assert.equal((await outsider.request.get(`${origin}${path}/my-document`)).status(), 404);
    for (const [index, image] of report.images.entries()) {
        assert.deepEqual(await (await member.request.get(`${origin}${resources}/${image.resource_id}/file`)).body(), originals[index]);
        assert.equal((await member.request.get(`${origin}/api/v1/canvas-runtime/assets/${image.asset_id}`)).status(), 404);
        assert.equal((await outsider.request.get(`${origin}${resources}/${image.resource_id}/file`)).status(), 404);
    }
    report.permissions_checked = true;
    assert.equal(report.task_post_count, 1);
    assert.equal(report.page_text_event_count, 0);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.failed_routes, []);
    report.step = "complete";
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    if (page) await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
