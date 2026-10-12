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
const report = { step: "start", width: config.width, operation: config.operation, page_errors: [], http_errors: [], resource_posts: 0, task_posts: 0 };
const server = await createServer({ cacheDir: resolve(".runtime/vite-conversion"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let owner;
let page;
const target = () => page.locator('[data-node-id="conversion-target"]');
const converted = document => document.nodes.find(node => node.id === "conversion-target");
const label = config.operation === "grayscale" ? "灰度图" : "Canny 边缘";
async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function eventually(readValue, predicate) {
    let value;
    for (let attempt = 0; attempt < 300; attempt++) {
        value = await readValue();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`转换未达到预期：${JSON.stringify(value)}`);
}
async function graph(predicate = () => true) {
    return eventually(async () => (await read(await owner.request.get(origin + path))).source_document, predicate);
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
    const focus = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focus.isVisible()) await focus.getByRole("button", { name: "退出专注模式", exact: true }).press("Control+0");
    else await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await page.waitForFunction(async () => {
        const viewport = document.querySelector("[data-canvas-viewport]");
        let previous, stable = 0;
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
async function checkResult() {
    const image = target().getByRole("img", { name: label + "结果", exact: true });
    await image.waitFor({ state: "visible" });
    await eventually(() => image.evaluate(element => element.complete && element.naturalWidth === 37 && element.naturalHeight === 19), Boolean);
    assert.ok((await image.getAttribute("src")).includes(report.resource_id));
}
try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    owner = await context(config.username);
    await open(owner);
    assert.equal(await target().getByRole("button", { name: "开始转换", exact: true }).isDisabled(), true);
    const source = page.locator('[data-node-id="conversion-source"]');
    await source.click({ position: { x: 20, y: 12 } });
    await source.hover();
    const rail = await source.locator('[data-canvas-connection-rail="right"]').boundingBox();
    const box = await target().boundingBox();
    assert.ok(rail && box);
    await page.mouse.move(rail.x + rail.width / 2, rail.y + rail.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + 1, box.y + box.height / 2, { steps: 18 });
    await page.mouse.up();
    await graph(document => document.connections.length === 1);
    await target().getByRole("img", { name: "转换输入", exact: true }).waitFor({ state: "visible" });
    await target().locator("#conversion-target-operation").click();
    await page.getByRole("listbox", { name: "转换方式", exact: true }).getByRole("option", { name: new RegExp(`^${label}`) }).click();
    report.step = "converting";
    await target().getByRole("button", { name: "开始转换", exact: true }).click();
    const document = await graph(document => converted(document)?.metadata?.mediaConversion?.status === "completed");
    report.resource_id = converted(document).metadata.storageKey.slice("resource:".length);
    assert.equal(converted(document).metadata.mediaConversion.resultStorageKey, `resource:${report.resource_id}`);
    await checkResult();
    const bytes = await (await owner.request.get(`${origin}/api/v1/canvas-runtime/resources/${report.resource_id}/file`)).body();
    report.checksum = createHash("sha256").update(bytes).digest("hex");
    await target().getByRole("button", { name: "原素材", exact: true }).click();
    await target().getByRole("img", { name: "转换输入", exact: true }).waitFor({ state: "visible" });
    await target().getByRole("button", { name: "结果", exact: true }).click();
    await checkResult();
    await save();
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(undefined, cookies);
    await open(owner);
    await checkResult();
    assert.equal(converted(await graph()).metadata.storageKey, `resource:${report.resource_id}`);
    await page.screenshot({ path: resolve(output, "conversion-restored.png"), fullPage: true });
    report.cookie_only_recovery = true;
    await save();
    const member = await context(config.memberUsername);
    await open(member);
    await checkResult();
    await page.screenshot({ path: resolve(output, "conversion-member.png"), fullPage: true });
    await member.close();
    report.member_result_seen = true;
    assert.equal(report.resource_posts, 1);
    assert.equal(report.task_posts, 0);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    report.step = "complete";
    process.stdout.write(JSON.stringify(report));
} catch (error) {
    report.error = error.stack ?? String(error);
    if (page) {
        await page.screenshot({ path: resolve(output, "failure.png"), fullPage: true }).catch(() => {});
        await writeFile(resolve(output, "failure.html"), await page.content()).catch(() => {});
    }
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
