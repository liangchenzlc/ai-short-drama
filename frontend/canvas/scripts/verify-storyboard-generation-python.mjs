import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const tasks = "/api/v1/canvas-runtime/tasks";
const output = resolve(config.outputDirectory);
const report = { step: "start", width: config.width, page_errors: [], http_errors: [], run_posts: 0, execute_posts: 0, task_posts: 0 };
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(".runtime/vite-storyboard-generation"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
let owner;
const script = () => page.locator('[data-node-id="runtime-node"]');
async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function eventually(readValue, predicate) {
    let value;
    for (let attempt = 0; attempt < 200; attempt++) {
        value = await readValue();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`分镜状态未达到预期：${JSON.stringify(value)}`);
}
async function graph(context = owner, predicate = () => true) {
    return eventually(async () => (await read(await context.request.get(`${origin}${path}/my-document`))).source_document, predicate);
}
async function context(username, cookies) {
    const result = await browser.newContext({ viewport: { width: config.width, height: 900 }, ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
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
        if (request.method() !== "POST") return;
        if (route === tasks) report.task_posts++;
        if (route === "/api/v1/canvas-runtime/creation-runs") report.run_posts++;
        if (/\/creation-runs\/\d+\/execute$/.test(route)) report.execute_posts++;
    });
    page.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (response.status() >= 400 && route.startsWith("/api/")) report.http_errors.push({ path: route, status: response.status() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await script().waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    const focus = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focus.isVisible()) await focus.getByRole("button", { name: "退出专注模式", exact: true }).press("Control+0");
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
async function editorScreenshot(name, rows) {
    await script().getByRole("button", { name: "全屏编辑", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: config.title, exact: true });
    await dialog.waitFor({ state: "visible" });
    const descriptions = dialog.getByRole("checkbox", { name: "画面描述", exact: true });
    if (!await descriptions.isChecked()) await descriptions.press("Space");
    assert.ok(await descriptions.isChecked());
    await eventually(() => dialog.locator("tbody tr[data-row-key]").evaluateAll(elements => elements.map(element => element.getAttribute("data-row-key"))), ids => JSON.stringify(ids) === JSON.stringify(rows.map(row => row.id)));
    for (const row of rows) assert.equal(await dialog.locator(`tr[data-row-key="${row.id}"]`).getByPlaceholder("填写画面描述", { exact: true }).inputValue(), row.plotDescription);
    await page.waitForFunction(async () => {
        const editor = document.querySelector('.ant-modal[role="dialog"]');
        if (!editor) return false;
        let previous;
        let stable = 0;
        while (stable < 15) {
            await new Promise(requestAnimationFrame);
            const rect = editor.getBoundingClientRect();
            const style = getComputedStyle(editor);
            const current = JSON.stringify([rect.x, rect.y, rect.width, rect.height, style.opacity, style.transform]);
            const moving = editor.getAnimations({ subtree: true }).some(animation => animation.effect?.getTiming().iterations !== Infinity && ["running", "pending"].includes(animation.playState));
            stable = !moving && current === previous ? stable + 1 : 0;
            previous = current;
        }
        return true;
    });
    await page.screenshot({ path: resolve(output, name), fullPage: true });
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
}
try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined });
    owner = await context(config.username);
    await open(owner);
    report.step = "原输入和Ctrl+Enter生成";
    const selects = script().locator(".ant-select");
    await selects.nth(0).click();
    await page.locator(".ant-select-dropdown:visible").getByText("2 镜", { exact: true }).click();
    await selects.nth(1).click();
    await page.locator(".ant-select-dropdown:visible").getByText("每镜 5 秒", { exact: true }).click();
    const composer = script().getByRole("textbox", { name: "分镜剧情与项目设定", exact: true });
    await composer.fill(config.prompt);
    await composer.press("Control+Enter");
    const running = await graph(owner, document => document.nodes.find(node => node.id === "runtime-node").metadata.taskId);
    const source = running.nodes.find(node => node.id === "runtime-node");
    report.task_id = source.metadata.taskId;
    assert.equal(source.metadata.composerContent, config.prompt);
    assert.equal(source.type, "script");
    assert.deepEqual(source.metadata.storyboard.rows, []);
    assert.equal(source.metadata.status, "loading");
    const partial = await eventually(async () => read(await owner.request.get(`${origin}${tasks}/${report.task_id}`)), task => task.status === "running" && task.textDraft?.length > 0);
    assert.equal(partial.textDraft, config.partial);
    report.source_saved_and_private_loading = true;
    const cookies = await owner.cookies();
    await page.screenshot({ path: resolve(output, "storyboard-generating.png"), fullPage: true });
    await owner.close();
    owner = await context(config.username, cookies);
    await open(owner);
    const restored = await graph(owner, document => document.nodes.find(node => node.id === "runtime-node").metadata.taskId === report.task_id);
    assert.deepEqual(restored.nodes.find(node => node.id === "runtime-node").metadata.storyboard.rows, []);
    assert.equal((await read(await owner.request.get(`${origin}${tasks}/${report.task_id}`))).status, "running");
    report.running_cookie_only_recovery = true;
    await writeFile(config.releasePath, "release", "utf8");
    report.step = "原任务完成与持久回填";
    const completed = await graph(owner, document => { const node = document.nodes.find(node => node.id === "runtime-node"); return node.type === "script" && node.metadata.status === "success" && node.metadata.storyboard.rows.length === 2; });
    const result = completed.nodes.find(node => node.id === "runtime-node");
    report.final_rows = result.metadata.storyboard.rows;
    assert.deepEqual(report.final_rows.map(row => row.id), [1, 2].map(index => `shot:${report.task_id}:${index}`));
    assert.equal(result.title, config.title);
    assert.ok(report.final_rows.every(row => row.durationSeconds === 5 && Array.isArray(row.characters)));
    await editorScreenshot("storyboard-generated.png", report.final_rows);
    const finalCookies = await owner.cookies();
    await owner.close();
    owner = await context(config.username, finalCookies);
    await open(owner);
    const fresh = await graph();
    assert.deepEqual(fresh.nodes.find(node => node.id === "runtime-node").metadata.storyboard.rows, report.final_rows);
    await editorScreenshot("storyboard-generated-restored.png", report.final_rows);
    report.stable_rows_restored = true;
    await owner.close();
    owner = await context(config.memberUsername);
    await open(owner);
    const shared = await graph();
    const metadata = shared.nodes.find(node => node.id === "runtime-node").metadata;
    assert.deepEqual(metadata.storyboard.rows, report.final_rows);
    for (const key of ["taskId", "composerContent", "storyboardShotCount", "storyboardShotDuration"]) assert.equal(metadata[key], undefined);
    await editorScreenshot("storyboard-generated-member.png", report.final_rows);
    report.member_shared_rows_private_task_hidden = true;
    assert.equal(report.run_posts, 1);
    assert.equal(report.execute_posts, 1);
    assert.equal(report.task_posts, 0);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    report.step = "complete";
} catch (error) {
    report.error = error.stack ?? String(error);
    if (page && !page.isClosed()) await page.screenshot({ path: resolve(output, "storyboard-generation-failure.png"), fullPage: true }).catch(() => undefined);
    process.exitCode = 1;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2), "utf8");
    await owner?.close();
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
