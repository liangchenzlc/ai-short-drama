import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const port = config.browserPort ?? 4197;
assert.ok(Number.isInteger(port) && port >= 1024 && port <= 65535);
assert.equal(config.protocol, "openai-response");
const origin = "http://127.0.0.1:" + port;
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = { scope: "real Agnes Responses image rejection final UI", surface: config.surface, protocol: config.protocol, width: config.width, step: "start", message_posts: 0, stop_posts: 0, run_ids: [], page_errors: [], http_errors: [] };
const server = await createServer({ ...(config.surface === "standard" ? { root: resolve("..") } : {}), cacheDir: resolve(output, "node_modules/.vite"), server: { host: "127.0.0.1", port, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
let owner;
let currentUrl = config.surface === "standard" ? origin + "/projects/" + config.projectId + "/episodes/" + config.sourceKey + "/source" : origin + "/canvas-app/canvas/" + config.sourceKey;

async function read(response) {
    assert.ok(response.ok(), "HTTP " + response.status() + " " + new URL(response.url()).pathname);
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

async function open() {
    page = await owner.newPage();
    page.on("pageerror", error => report.page_errors.push(error.message));
    page.on("request", request => {
        if (/^\/api\/v1\/assistant\/conversations\/\d+\/messages$/.test(new URL(request.url()).pathname) && request.method() === "POST") report.message_posts++;
        if (/^\/api\/v1\/assistant\/runs\/\d+\/stop$/.test(new URL(request.url()).pathname) && request.method() === "POST") report.stop_posts++;
    });
    page.on("response", response => {
        const path = new URL(response.url()).pathname;
        if (path.startsWith("/api/") && response.status() >= 400) report.http_errors.push({ path, status: response.status() });
    });
    await page.goto(currentUrl);
    if (config.surface === "standard") await page.locator(".agent-episode-layout").waitFor();
    else await page.locator("[data-canvas-editor] [data-canvas-viewport]").waitFor();
    const focused = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focused.isVisible()) await focused.getByRole("button", { name: "退出专注模式", exact: true }).click();
    if (!await page.getByRole("button", { name: "关闭助手", exact: true }).isVisible()) {
        const toggle = page.getByRole("button", { name: "AI 创作助手", exact: true });
        if (await toggle.isVisible()) {
            if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
        } else await page.locator("[data-canvas-editor]").press("Control+j");
    }
    await page.getByRole("textbox", { name: "给助手的消息", exact: true }).waitFor();
    await page.evaluate(() => document.fonts.ready);
}

async function eventually(readValue, predicate, limit = 180000) {
    const deadline = Date.now() + limit;
    while (Date.now() < deadline) {
        const value = await readValue();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 250));
    }
    assert.fail("真实拒绝后的助手状态未在时限内恢复");
}

async function uploadInputs() {
    await page.getByRole("button", { name: "添加附件", exact: true }).click();
    const chooser = page.waitForEvent("filechooser");
    const uploaded = page.waitForResponse(response => response.url().includes("/attachments/uploads") && response.request().method() === "POST");
    await page.getByRole("menuitem", { name: "添加图片", exact: true }).click();
    await (await chooser).setFiles(config.imagePath);
    const attachment = await read(await uploaded);
    report.attachment_id = attachment.id;
    const image = page.getByLabel("本次消息附件").getByRole("img", { name: "reference.png", exact: true });
    await image.waitFor({ state: "visible" });
    await image.evaluate(image => image.decode());
    assert.ok(await image.evaluate(image => image.naturalWidth > 0 && image.naturalHeight > 0));
    report.composer_attachment_visible = true;
    await page.getByRole("button", { name: "加载 Skill", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "加载 Skill", exact: true });
    await dialog.getByText("我的技能", { exact: true }).click();
    const skillChooser = page.waitForEvent("filechooser");
    const uploadedSkill = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/agent/skills/uploads" && response.request().method() === "POST");
    await dialog.getByRole("button", { name: "上传 Skill", exact: true }).click();
    await (await skillChooser).setFiles(config.skillPath);
    const skill = await read(await uploadedSkill);
    report.skill_id = skill.id;
    report.skill_row_version = skill.row_version;
    await dialog.getByRole("checkbox", { name: "图片解读", exact: true }).check();
    await dialog.getByRole("button", { name: /^完\s*成$/ }).click();
    const loaded = page.getByLabel("已加载技能");
    await loaded.waitFor();
    assert.ok((await loaded.innerText()).includes("图片解读"));
}

async function inspectAttachment(phase) {
    const reference = page.locator(".ai-assistant-user").getByRole("img", { name: "reference.png", exact: true });
    await reference.waitFor({ state: "visible" });
    const value = await reference.evaluate(async image => {
        await image.decode();
        const response = await fetch(image.currentSrc);
        if (!response.ok) throw new Error("真实对话图片读取失败：HTTP " + response.status);
        const digest = await crypto.subtle.digest("SHA-256", await response.arrayBuffer());
        return { width: image.naturalWidth, height: image.naturalHeight, sha256: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("") };
    });
    assert.ok(value.width > 0 && value.height > 0);
    report.attachment_views = [...(report.attachment_views || []), { phase, ...value }];
}

async function send() {
    const composer = page.getByRole("textbox", { name: "给助手的消息", exact: true });
    await composer.fill(config.prompt);
    const accepted = page.waitForResponse(response => /^\/api\/v1\/assistant\/conversations\/\d+\/messages$/.test(new URL(response.url()).pathname) && response.request().method() === "POST");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    const response = await accepted;
    const result = await read(response);
    const body = response.request().postDataJSON();
    assert.equal(body.model_config_id, config.modelId);
    assert.equal(body.context.kind, config.contextKind);
    assert.equal(body.context.id, config.sourceKey);
    assert.deepEqual(body.attachment_ids, [report.attachment_id]);
    assert.deepEqual(body.skills, [{ id: report.skill_id, content_version: "1" }]);
    assert.equal(result.run.mode, "discuss");
    report.conversation_id = result.run.conversation_id;
    report.run_ids.push(result.run.id);
    await eventually(async () => composer.inputValue(), value => value === "", 30000);
    return result.run;
}

async function inspectFailure(run, phase) {
    const saved = await read(await owner.request.get(origin + "/api/v1/assistant/runs/" + run.id));
    assert.equal(saved.id, run.id);
    assert.equal(saved.conversation_id, report.conversation_id);
    assert.equal(saved.status, "failed");
    assert.equal(saved.error.code, "agent_provider_rejected");
    const messages = await read(await owner.request.get(origin + "/api/v1/assistant/conversations/" + report.conversation_id + "/messages"));
    assert.deepEqual(messages.items.map(message => message.role), ["user", "assistant"]);
    assert.equal(messages.items[0].content, config.prompt);
    const failure = messages.items[1];
    assert.equal(failure.content, config.failureMessage);
    assert.deepEqual(failure.references, []);
    assert.deepEqual(failure.artifacts, []);
    const visible = page.locator('.ai-assistant-turn[data-message-id="' + failure.id + '"]').locator(".ai-assistant-reply");
    await visible.waitFor({ state: "visible" });
    assert.ok((await visible.innerText()).includes(config.failureMessage));
    const notice = page.locator(".ai-assistant-failed");
    await notice.waitFor({ state: "visible" });
    assert.equal(await notice.innerText(), saved.error.message || config.failureNotice);
    await page.getByRole("button", { name: "停止", exact: true }).waitFor({ state: "hidden" });
    await page.locator(".ai-assistant-run-state").waitFor({ state: "hidden" });
    await page.locator(".ai-assistant-log").getByText("正在思考…", { exact: true }).waitFor({ state: "hidden" });
    assert.equal(await page.locator(".ai-assistant-turn").count(), 1);
    assert.equal(await page.locator(".ai-assistant-log .ai-assistant-reply").count(), 1);
    assert.equal(await page.getByRole("textbox", { name: "给助手的消息", exact: true }).inputValue(), "");
    const state = await read(await owner.request.get(origin + "/api/v1/assistant/conversations/" + report.conversation_id + "/state"));
    assert.equal(state.active_run, null);
    assert.deepEqual(state.queued_runs, []);
    await inspectAttachment(phase);
    report.failure_views = [...(report.failure_views || []), { phase, run_id: saved.id, message_id: failure.id, status: saved.status, error_code: saved.error.code, notice: await notice.innerText(), original_input_preserved: true, running_controls_hidden: true }];
    assert.equal(report.message_posts, 1);
    assert.equal(report.stop_posts, 0);
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    owner = await context();
    await open();
    await uploadInputs();
    report.step = "original image and Skill message before real POST";
    const first = await send();
    await eventually(async () => read(await owner.request.get(origin + "/api/v1/assistant/runs/" + first.id)), result => result.status === "running", 30000);
    await page.getByRole("status").filter({ hasText: "正在回复，可继续发送补充消息" }).waitFor({ state: "visible" });
    assert.equal(await page.locator(".ai-assistant-turn").count(), 0);
    await inspectAttachment("pending");
    await page.screenshot({ path: resolve(output, "pending.png") });
    currentUrl = page.url();
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(cookies);
    await open();
    assert.equal(report.message_posts, 1);
    const recovered = await read(await owner.request.get(origin + "/api/v1/assistant/runs/" + first.id));
    assert.equal(recovered.id, first.id);
    assert.equal(recovered.status, "running");
    await inspectAttachment("running-fresh");
    const changed = await read(await owner.request.patch(origin + "/api/v1/agent/skills/" + report.skill_id, { data: { row_version: report.skill_row_version, instructions: config.skillUpdated } }));
    assert.equal(changed.content_version, "2");
    report.skill_version_changed_before_provider_post = true;
    report.cookies_only_running_recovery_before_provider_post = true;
    await writeFile(config.releasePath, "Original running chat restored; allow its real provider POST.\n");
    report.step = "wait for real rejection and final visible error";
    const terminal = await eventually(async () => read(await owner.request.get(origin + "/api/v1/assistant/runs/" + first.id)), result => ["succeeded", "failed", "cancelled"].includes(result.status));
    report.last_run_status = terminal.status;
    report.run_error = terminal.error;
    await inspectFailure(first, "terminal");
    await page.screenshot({ path: resolve(output, "terminal.png") });
    currentUrl = page.url();
    const finalCookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(finalCookies);
    await open();
    await inspectFailure(first, "terminal-fresh");
    assert.equal(report.failure_views[0].message_id, report.failure_views[1].message_id);
    const listed = await read(await owner.request.get(origin + "/api/v1/assistant/conversations?project_id=" + config.projectId));
    assert.equal(listed.total, 1);
    assert.equal(listed.items[0].id, report.conversation_id);
    report.fresh_terminal_recovery_same_conversation = true;
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
