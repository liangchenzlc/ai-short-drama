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
const resources = "/api/v1/canvas-runtime/resources";
const output = resolve(config.outputDirectory);
const report = { step: "start", page_errors: [], failed_routes: [], resource_posts: 0 };
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
        if (new URL(request.url()).pathname === resources && request.method() === "POST") report.resource_posts++;
    });
    target.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (route.startsWith("/api/") && response.status() >= 400) report.failed_routes.push({ path: route, status: response.status() });
    });
}

async function login(username) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference" });
    context.setDefaultTimeout(20000);
    context.setDefaultNavigationTimeout(60000);
    await read(await context.request.post(`${origin}/api/v1/auth/login`, {
        headers: { Origin: origin }, data: { username, password: config.password },
    }));
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
    assert.fail(`标注画布没有达到预期状态：${JSON.stringify(current)}`);
}

async function openCanvas(target) {
    await target.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await target.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await target.evaluate(() => document.fonts.ready);
    await target.getByRole("button", { name: "适合屏幕", exact: true }).click();
}

async function openAnnotation(sourceId) {
    const node = page.locator(`[data-node-id="${sourceId}"]`);
    await node.click();
    await node.hover();
    await page.getByRole("button", { name: "图片工具", exact: true }).click();
    await page.getByRole("menuitem", { name: "标注", exact: true }).click();
    const editor = node.locator('[data-image-annotation-inline="true"]');
    await editor.waitFor({ state: "visible" });
    const canvas = editor.getByLabel("图片标注画布", { exact: true });
    await page.waitForFunction(id => {
        const canvas = document.querySelector(`[data-node-id="${id}"] [data-image-annotation-inline] canvas`);
        return canvas?.width === 320 && canvas?.height === 200;
    }, sourceId);
    return { editor, canvas };
}

async function point(canvas, x, y) {
    const box = await canvas.boundingBox();
    assert.ok(box);
    return { x: box.x + x / 320 * box.width, y: box.y + y / 200 * box.height };
}

async function stroke(canvas, start, end) {
    const from = await point(canvas, ...start);
    const to = await point(canvas, ...end);
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(to.x, to.y, { steps: 12 });
    await page.mouse.up();
}

async function pixels(canvas) {
    return canvas.evaluate(element => element.toDataURL("image/png"));
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await login(config.username);
    const member = await login(config.memberUsername);
    const outsider = await login(config.outsiderUsername);
    page = await owner.newPage();
    observe(page);
    await openCanvas(page);
    report.step = "原上传与取消标注";
    const original = Buffer.from(config.image, "base64");
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({
        name: "annotation-original.png", mimeType: "image/png", buffer: original,
    });
    const uploaded = await graph(owner, document => document.nodes.length === 1 && document.nodes[0].metadata?.assetId);
    const source = uploaded.nodes[0];
    report.source = { node_id: source.id, resource_id: source.metadata.storageKey.slice("resource:".length), asset_id: source.metadata.assetId };
    await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    let { editor, canvas } = await openAnnotation(source.id);
    assert.equal(await editor.getByLabel("标注颜色").inputValue(), "#ef4444");
    assert.equal(await editor.getByLabel("线条粗细").inputValue(), "18");
    assert.equal(await editor.getByRole("button", { name: "保存标注", exact: true }).isDisabled(), true);
    assert.equal(await editor.getByRole("button", { name: "撤销", exact: true }).isDisabled(), true);
    await stroke(canvas, [20, 40], [100, 40]);
    await editor.getByRole("button", { name: "撤销", exact: true }).waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    await editor.waitFor({ state: "hidden" });
    assert.deepEqual((await read(await owner.request.get(`${origin}${path}/my-document`))).source_document.nodes, uploaded.nodes);
    assert.equal(report.resource_posts, 1);
    report.cancel_kept_original = true;

    report.step = "画笔矩形文字与撤销重做";
    ({ editor, canvas } = await openAnnotation(source.id));
    await stroke(canvas, [20, 40], [100, 40]);
    const brush = await pixels(canvas);
    await editor.getByRole("button", { name: "矩形", exact: true }).click();
    await editor.getByLabel("标注颜色").fill("#22c55e");
    const width = editor.getByLabel("线条粗细");
    await width.press("Home");
    for (let index = 0; index < 7; index++) await width.press("ArrowRight");
    assert.equal(await width.inputValue(), "10");
    await stroke(canvas, [250, 90], [150, 40]);
    const rectangle = await pixels(canvas);
    assert.notEqual(rectangle, brush);
    await editor.getByRole("button", { name: "文字", exact: true }).click();
    await editor.getByLabel("标注颜色").fill("#3b82f6");
    assert.equal(await editor.getByLabel("文字字号").inputValue(), "42");
    const textPoint = await point(canvas, 40, 125);
    await page.mouse.click(textPoint.x, textPoint.y);
    await editor.getByRole("textbox", { name: "标注文字", exact: true }).fill("颜色说明");
    await editor.getByRole("textbox", { name: "标注文字", exact: true }).press("Enter");
    await editor.getByRole("textbox", { name: "标注文字", exact: true }).waitFor({ state: "hidden" });
    const annotated = await pixels(canvas);
    assert.notEqual(annotated, rectangle);
    await editor.getByRole("button", { name: "撤销", exact: true }).click();
    assert.equal(await pixels(canvas), rectangle);
    await editor.getByRole("button", { name: "撤销", exact: true }).click();
    assert.equal(await pixels(canvas), brush);
    await editor.getByRole("button", { name: "重做", exact: true }).click();
    assert.equal(await pixels(canvas), rectangle);
    await editor.getByRole("button", { name: "重做", exact: true }).click();
    assert.equal(await pixels(canvas), annotated);
    assert.equal(await editor.getByRole("button", { name: "重做", exact: true }).isDisabled(), true);
    report.tools_and_history_checked = true;
    await page.screenshot({ path: resolve(output, "annotation-before-save.png") });

    report.step = "保存派生节点与原图不变";
    await editor.getByRole("button", { name: "保存标注", exact: true }).click();
    await editor.waitFor({ state: "hidden" });
    const final = await graph(owner, document => document.nodes.length === 2 && document.nodes.every(node => node.metadata?.assetId));
    const child = final.nodes.find(node => node.id !== source.id);
    assert.equal(child.title, `${source.title} · 标注`);
    assert.equal(child.position.x, source.position.x + source.width + 96);
    assert.equal(child.position.y, source.position.y);
    assert.equal(child.metadata.generatedFromNodeId, source.id);
    assert.equal(child.metadata.resultOrigin, "derived");
    assert.deepEqual(final.nodes.find(node => node.id === source.id), source);
    assert.deepEqual(final.connections.map(edge => [edge.fromNodeId, edge.toNodeId]), [[source.id, child.id]]);
    report.child = { node_id: child.id, resource_id: child.metadata.storageKey.slice("resource:".length), asset_id: child.metadata.assetId };
    const image = await owner.request.get(`${origin}${resources}/${report.child.resource_id}/file`);
    assert.ok(image.ok());
    report.child.sha256 = createHash("sha256").update(await image.body()).digest("hex");
    assert.deepEqual(await (await owner.request.get(`${origin}${resources}/${report.source.resource_id}/file`)).body(), original);
    assert.equal(report.resource_posts, 2);
    report.derived_graph_saved = true;

    report.step = "全新context读取与三方权限";
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference", storageState: { cookies: await owner.cookies(), origins: [] } });
    fresh.setDefaultTimeout(20000);
    fresh.setDefaultNavigationTimeout(60000);
    const restored = await fresh.newPage();
    observe(restored);
    const downloads = new Set();
    restored.on("response", response => {
        if (response.ok()) downloads.add(new URL(response.url()).pathname);
    });
    await openCanvas(restored);
    await restored.waitForFunction(ids => ids.every(id => {
        const image = document.querySelector(`[data-node-id="${id}"] img`);
        return image instanceof HTMLImageElement && image.complete && image.naturalWidth === 320 && image.naturalHeight === 200;
    }), [source.id, child.id]);
    for (const item of [report.source, report.child]) {
        assert.ok(downloads.has(`${resources}/${item.resource_id}/file`));
        const originalRead = await owner.request.get(`${origin}${resources}/${item.resource_id}/file`);
        const sharedRead = await member.request.get(`${origin}${resources}/${item.resource_id}/file`);
        assert.ok(originalRead.ok());
        assert.equal(sharedRead.status(), 404);
        assert.equal((await member.request.get(`${origin}/api/v1/canvas-runtime/assets/${item.asset_id}`)).status(), 404);
        assert.equal((await outsider.request.get(`${origin}${resources}/${item.resource_id}/file`)).status(), 404);
    }
    assert.equal((await member.request.get(`${origin}${path}/my-document`)).status(), 404);
    assert.equal((await outsider.request.get(`${origin}${path}/my-document`)).status(), 404);
    await restored.screenshot({ path: resolve(output, "annotation-restored.png") });
    await fresh.close();
    report.fresh_context_and_permissions_checked = true;
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
