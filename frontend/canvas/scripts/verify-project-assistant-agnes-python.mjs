import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const port = config.browserPort ?? 4192;
assert.ok(Number.isInteger(port) && port >= 1024 && port <= 65535);
const origin = "http://127.0.0.1:" + port;
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = { scope: "shared assistant original UI with real Agnes", surface: config.surface, input_mode: config.inputMode, protocol: config.protocol, width: config.width, step: "start", message_posts: 0, stop_posts: 0, run_ids: [], page_errors: [], http_errors: [] };
const server = await createServer({ ...(config.surface === "standard" ? { root: resolve("..") } : {}), ...(port !== 4192 ? { cacheDir: resolve(output, "node_modules/.vite") } : {}), server: { host: "127.0.0.1", port, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
let owner;
let currentUrl = config.surface === "standard" ? origin + "/projects/" + config.projectId + "/source" : origin + "/canvas-app/canvas/" + config.sourceKey;

async function read(response) {
    assert.ok(response.ok(), "HTTP " + response.status() + " " + new URL(response.url()).pathname);
    return response.json();
}

async function context(cookies) {
    const current = await browser.newContext({ viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference", permissions: ["clipboard-read", "clipboard-write"], ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
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
    const close = page.getByRole("button", { name: "关闭助手", exact: true });
    if (!await close.isVisible()) {
        const toggle = page.getByRole("button", { name: "AI 创作助手", exact: true });
        if (await toggle.isVisible()) {
            const expanded = await toggle.getAttribute("aria-expanded");
            report.entry_states = [...(report.entry_states || []), { expanded, query: new URL(page.url()).searchParams.get("assistant") }];
            if (expanded !== "true") await toggle.click();
        }
        else await page.locator("[data-canvas-editor]").press("Control+j");
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
    assert.fail("真实助手运行未在时限内完成");
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

async function inspectAttachment() {
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
    report.attachment_views = [...(report.attachment_views || []), value];
}

async function send(index) {
    const input = page.getByRole("textbox", { name: "给助手的消息", exact: true });
    await input.fill(config.prompts[index]);
    const accepted = page.waitForResponse(response => /^\/api\/v1\/assistant\/conversations\/\d+\/messages$/.test(new URL(response.url()).pathname) && response.request().method() === "POST");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    const response = await accepted;
    const result = await read(response);
    assert.equal(response.request().postDataJSON().model_config_id, config.modelId);
    if (index === 0) {
        assert.equal(response.request().postDataJSON().context.kind, config.contextKind);
        assert.equal(response.request().postDataJSON().context.id, config.sourceKey);
        if (config.inputMode === "image-skill") {
            assert.deepEqual(response.request().postDataJSON().attachment_ids, [report.attachment_id]);
            assert.deepEqual(response.request().postDataJSON().skills, [{ id: report.skill_id, content_version: "1" }]);
        }
    } else assert.equal(response.request().postDataJSON().context, null);
    assert.equal(result.run.mode, "discuss");
    if (report.conversation_id) assert.equal(result.run.conversation_id, report.conversation_id);
    else report.conversation_id = result.run.conversation_id;
    report.run_ids.push(result.run.id);
    await eventually(async () => input.inputValue(), value => value === "", 30000);
    return result.run;
}

async function finished(run, word, index) {
    const result = await eventually(async () => read(await owner.request.get(origin + "/api/v1/assistant/runs/" + run.id)), result => ["succeeded", "failed", "cancelled"].includes(result.status));
    report.last_run_status = result.status;
    assert.equal(result.status, "succeeded", JSON.stringify(result.error));
    const messages = await read(await owner.request.get(origin + "/api/v1/assistant/conversations/" + report.conversation_id + "/messages"));
    assert.equal(messages.items.length, (index + 1) * 2);
    const reply = messages.items.filter(message => message.role === "assistant").at(-1);
    assert.ok(reply.content.includes(word));
    const visible = page.locator('.ai-assistant-turn[data-message-id="' + reply.id + '"]').locator(".ai-assistant-reply");
    await visible.waitFor({ state: "visible" });
    assert.ok((await visible.innerText()).includes(word));
    return reply;
}

async function checkpoint(name, limit = 120000) {
    await eventually(async () => {
        try { await readFile(resolve(config.checkpointDirectory, name)); return true; }
        catch (error) { if (error.code === "ENOENT") return false; throw error; }
    }, value => value, limit);
}

async function lifecycle(first) {
    report.step = "actual provider response before stop or executor interruption";
    await checkpoint("supplier-ready");
    const observed = JSON.parse(await readFile(resolve(config.checkpointDirectory, "provider-report.json"), "utf8"));
    assert.equal(observed.provider_headers.status, 200);
    assert.equal(observed.submissions, 1);
    assert.equal(observed.requests.length, 1);
    if (config.lifecycleMode !== "interrupt-after-headers") assert.equal(observed.complete_raw_persisted, true);
    const before = await read(await owner.request.get(origin + "/api/v1/assistant/runs/" + first.id));
    assert.equal(before.status, "running");
    await page.screenshot({ path: resolve(output, "provider-received.png") });
    currentUrl = page.url();
    const receivedCookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(receivedCookies);
    await open();
    const recovered = await read(await owner.request.get(origin + "/api/v1/assistant/runs/" + first.id));
    assert.equal(recovered.id, first.id);
    assert.equal(recovered.status, "running");
    assert.equal(report.message_posts, 1);
    report.cookies_only_running_recovery_after_provider_response = true;
    if (config.lifecycleMode === "stop-after-response") {
        const stopped = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/assistant/runs/" + first.id + "/stop" && response.request().method() === "POST");
        await page.getByRole("button", { name: "停止", exact: true }).click();
        assert.equal((await read(await stopped)).status, "cancelled");
        await page.getByText("这一条已经停下了。", { exact: true }).waitFor({ state: "visible" });
        await writeFile(resolve(config.checkpointDirectory, "allow-finalize"), "Original UI stopped this run; collect its late real receipt.\n");
    } else {
        await writeFile(resolve(config.checkpointDirectory, "interrupt-worker"), "Terminate the task-owned executor after the observed real provider response.\n");
    }
    report.step = "terminal state after stop or natural lease recovery";
    const terminal = await eventually(async () => read(await owner.request.get(origin + "/api/v1/assistant/runs/" + first.id)), result => ["succeeded", "failed", "cancelled"].includes(result.status), 240000);
    const expected = { "stop-after-response": "cancelled", "recover-after-response": "succeeded", "interrupt-after-headers": "failed" }[config.lifecycleMode];
    assert.equal(terminal.status, expected);
    if (config.lifecycleMode === "interrupt-after-headers") assert.equal(terminal.error.code, "agent_acceptance_unknown");
    await checkpoint("executor-finished", 30000);
    report.last_run_status = terminal.status;
    async function inspect() {
        const saved = await read(await owner.request.get(origin + "/api/v1/assistant/runs/" + first.id));
        assert.equal(saved.status, expected);
        const messages = await read(await owner.request.get(origin + "/api/v1/assistant/conversations/" + report.conversation_id + "/messages"));
        if (config.lifecycleMode === "recover-after-response") await finished(first, "红色", 0);
        else if (config.lifecycleMode === "stop-after-response") {
            assert.deepEqual(messages.items.map(message => message.role), ["user"]);
            assert.equal(messages.items[0].content, config.prompts[0]);
            await page.getByText("这一条已经停下了。", { exact: true }).waitFor({ state: "visible" });
            assert.equal(await page.locator(".ai-assistant-turn").count(), 0);
            assert.equal(await page.locator(".ai-assistant-log .ai-assistant-reply").count(), 0);
        } else {
            assert.deepEqual(messages.items.map(message => message.role), ["user", "assistant"]);
            assert.equal(messages.items[0].content, config.prompts[0]);
            const failure = messages.items[1];
            assert.equal(failure.content, config.unknownMessage);
            assert.deepEqual(failure.references, []);
            assert.deepEqual(failure.artifacts, []);
            const visible = page.locator('.ai-assistant-turn[data-message-id="' + failure.id + '"]').locator(".ai-assistant-reply");
            await visible.waitFor({ state: "visible" });
            assert.ok((await visible.innerText()).includes(config.unknownMessage));
            await page.locator(".ai-assistant-failed").waitFor({ state: "visible" });
            assert.equal(await page.locator(".ai-assistant-turn").count(), 1);
            assert.equal(await page.locator(".ai-assistant-log .ai-assistant-reply").count(), 1);
        }
    }
    await inspect();
    await page.screenshot({ path: resolve(output, "terminal.png") });
    currentUrl = page.url();
    const finalCookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(finalCookies);
    await open();
    await inspect();
    const listed = await read(await owner.request.get(origin + "/api/v1/assistant/conversations?project_id=" + config.projectId));
    assert.equal(listed.total, 1);
    assert.equal(listed.items[0].id, report.conversation_id);
    report.fresh_terminal_recovery_same_conversation = true;
    assert.equal(report.message_posts, 1);
    assert.equal(report.stop_posts, config.lifecycleMode === "stop-after-response" ? 1 : 0);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    await page.screenshot({ path: resolve(output, "fresh.png") });
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    owner = await context();
    await open();
    if (config.inputMode === "image-skill") await uploadInputs();
    report.step = "original first message with saved canvas context";
    const first = await send(0);
    await eventually(async () => read(await owner.request.get(origin + "/api/v1/assistant/runs/" + first.id)), result => result.status === "running", 30000);
    await page.getByRole("status").filter({ hasText: "正在回复，可继续发送补充消息" }).waitFor({ state: "visible" });
    assert.equal(await page.locator(".ai-assistant-turn").count(), 0);
    if (config.inputMode === "image-skill") await inspectAttachment();
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
    if (config.inputMode === "image-skill") {
        await inspectAttachment();
        const changed = await read(await owner.request.patch(origin + "/api/v1/agent/skills/" + report.skill_id, { data: { row_version: report.skill_row_version, instructions: config.skillUpdated } }));
        assert.equal(changed.content_version, "2");
        report.skill_version_changed_before_provider_post = true;
    }
    report.cookies_only_running_recovery_before_provider_post = true;
    await writeFile(config.releasePath, "Original running chat restored; allow its real provider POST.\n");
    if (config.lifecycleMode) await lifecycle(first);
    else {
    report.step = "real first reply and history continuation";
    const reply = await finished(first, config.expectedColor, 0);
    assert.ok(reply.content.includes("礼盒"));
    await page.locator('.ai-assistant-turn[data-message-id="' + reply.id + '"]').getByRole("button", { name: "复制回复", exact: true }).click();
    const clipboard = await page.evaluate(() => navigator.clipboard.readText());
    assert.equal(clipboard.replace(/\r\n/g, "\n"), reply.content);
    report.actual_reply_clipboard = true;
    await page.screenshot({ path: resolve(output, "first.png") });
    await page.getByRole("button", { name: "这条消息不带当前作品", exact: true }).click();
    const second = await send(1);
    await finished(second, "礼盒", 1);
    report.history_continuation_without_current_work = true;
    assert.equal(report.message_posts, 2);
    await page.screenshot({ path: resolve(output, "second.png") });
    currentUrl = page.url();
    const finalCookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(finalCookies);
    await open();
    await finished(second, "礼盒", 1);
    assert.equal(await page.locator(".ai-assistant-turn").count(), 2);
    if (config.inputMode === "image-skill") await inspectAttachment();
    const listed = await read(await owner.request.get(origin + "/api/v1/assistant/conversations?project_id=" + config.projectId));
    assert.equal(listed.total, 1);
    assert.equal(listed.items[0].id, report.conversation_id);
    report.fresh_terminal_recovery_same_conversation = true;
    assert.equal(report.message_posts, 2);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    await page.screenshot({ path: resolve(output, "fresh.png") });
    }
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
