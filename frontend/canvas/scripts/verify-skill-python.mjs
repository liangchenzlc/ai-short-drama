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
const output = resolve(config.outputDirectory);
const report = { step: "start", width: config.width, page_errors: [], http_errors: [], submissions: 0, uploads: 0 };
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(".runtime/vite-skill-browser"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function authorize(context) {
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    context.setDefaultTimeout(20000);
    context.setDefaultNavigationTimeout(60000);
    context.on("response", response => {
        const pathname = new URL(response.url()).pathname;
        if (response.status() >= 400) report.http_errors.push({ path: pathname, status: response.status() });
    });
    context.on("request", request => {
        const pathname = new URL(request.url()).pathname;
        if (request.method() === "POST" && pathname === "/api/v1/canvas-runtime/tasks") report.submissions++;
        if (request.method() === "POST" && pathname === "/api/v1/canvas-runtime/resources") report.uploads++;
    });
}
async function login(username) {
    const context = await browser.newContext({ viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference" });
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    await authorize(context);
    return context;
}
async function fresh(context) {
    const cookies = (await context.storageState()).cookies;
    await context.close();
    const next = await browser.newContext({ storageState: { cookies, origins: [] }, viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference" });
    await authorize(next);
    return next;
}
async function graph(context) {
    return (await read(await context.request.get(`${origin}${path}/my-document`))).source_document;
}
async function open(context, own) {
    page = await context.newPage();
    page.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    const node = page.locator('[data-node-id="skill-reference"]');
    await node.waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    const template = own ? config.privateMetadata.skillSnapshot.template : "未配置技能模板";
    await node.getByText(template, { exact: true }).waitFor({ state: "visible" });
    if (!own) {
        const text = await page.locator("body").innerText();
        for (const value of [config.privateMetadata.content, config.privateMetadata.previewContent, config.privateMetadata.skillSnapshot.name, config.privateMetadata.skillSnapshot.template]) assert.ok(!text.includes(value));
    }
    return node;
}
async function drag(context, own) {
    const before = await graph(context);
    const original = before.nodes.find(node => node.id === "skill-reference").position;
    const node = page.locator('[data-node-id="skill-reference"]');
    const box = await node.boundingBox();
    assert.ok(box);
    await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === `${path}/commits` && response.ok()),
        (async () => {
            await page.mouse.move(box.x + 20, box.y + 8);
            await page.mouse.down();
            await page.mouse.move(box.x + 68, box.y + 32, { steps: 12 });
            await page.mouse.up();
        })(),
    ]);
    const saved = await graph(context);
    const skill = saved.nodes.find(node => node.id === "skill-reference");
    assert.notDeepEqual(skill.position, original);
    assert.deepEqual(skill.metadata, own ? config.privateMetadata : {});
    return skill.position;
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    let owner = await login(config.username);
    report.step = "owner-drag";
    await open(owner, true);
    const ownerPosition = await drag(owner, true);
    report.owner_drag_saved = true;
    report.step = "owner-restore";
    owner = await fresh(owner);
    await open(owner, true);
    assert.deepEqual((await graph(owner)).nodes[0].position, ownerPosition);
    report.owner_cookie_only_restored = true;
    await page.screenshot({ path: resolve(output, "skill-owner-restored.png") });
    report.final_position = ownerPosition;
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    assert.equal(report.submissions, 0);
    assert.equal(report.uploads, 0);
    report.step = "complete";
} catch (error) {
    report.error = error.stack ?? String(error);
    if (page && !page.isClosed()) {
        report.body = await page.locator("body").innerText().catch(() => "");
        await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    }
    process.exitCode = 1;
} finally {
    await browser?.close();
    await server.close();
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    process.stdout.write(JSON.stringify(report) + "\n");
}
