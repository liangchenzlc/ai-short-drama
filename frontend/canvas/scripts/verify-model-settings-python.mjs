import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4194";
const output = resolve(".runtime/model-settings-python-browser");
const modelPath = "/api/v1/canvas-runtime/workspace/model-config";
const secrets = ["synthetic-browser-api-key", "synthetic-browser-secret-key", "synthetic-browser-header-key"];
const report = { source_service_created: false, logical_id_acknowledged: false, default_choice_saved: false, fresh_browser_restored: false, credentials_redacted: false, browser_cache_has_no_secrets: false, source_service_updated: false, account_catalog_isolated: false, conflict_kept_draft: false, source_service_deleted: false, zero_generation_submissions: true, source_model_test_completed: false, source_model_test_failure_displayed: false, uncertain_model_test_ack_replayed: false, source_task_center_showed_actual_result: false, private_model_tests_hidden: false, canvas_task_submissions: 0, model_test_submissions: 0, page_errors: [], failed_routes: [] };
const modelTestAdmissions = [];
report.source_builtin_available = false;
const privateModelTestPaths = new Set();
await mkdir(output, { recursive: true });
const server = await createServer({ server: { host: "127.0.0.1", port: 4194, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
function observe(context) {
    context.on("page", current => current.on("pageerror", error => report.page_errors.push(error.stack || error.message)));
    context.on("request", request => {
        if (request.method() !== "POST") return;
        const path = new URL(request.url()).pathname;
        if (path === "/api/v1/canvas-runtime/tasks") { report.canvas_task_submissions++; report.zero_generation_submissions = false; }
        if (path === "/api/v1/canvas-runtime/model-tests") { report.model_test_submissions++; report.zero_generation_submissions = false; modelTestAdmissions.push({ body: request.postData(), key: request.headers()["idempotency-key"] }); }
    });
    context.on("response", response => {
        const path = new URL(response.url()).pathname;
        if (response.status() >= 400 && path.startsWith("/api/")) report.failed_routes.push({ path, status: response.status() });
    });
    context.setDefaultTimeout(30000);
    context.setDefaultNavigationTimeout(60000);
}
async function login(context, username, actorId) {
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value, "X-Canvas-Actor": actorId });
}
const contextOptions = cookies => ({ viewport: { width: 1440, height: 900 }, ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
async function openSettings(context) {
    const current = await context.newPage();
    await current.goto(`${origin}/canvas-app/settings`);
    await current.locator(".settings-pane-header-actions").getByRole("button", { name: "添加模型服务", exact: true }).waitFor({ state: "visible" });
    await current.locator('.settings-channel[aria-labelledby="channel-beefapi-title"]').getByRole("button", { name: "连接 BeefAPI", exact: true }).waitFor({ state: "visible" });
    report.source_builtin_available = true;
    return current;
}
async function savedAfter(current, action) {
    const ack = current.waitForResponse(response => new URL(response.url()).pathname === modelPath && response.request().method() === "PUT" && response.ok());
    await action();
    return read(await ack);
}
function assertRedacted(saved) {
    const text = JSON.stringify(saved);
    for (const secret of secrets) assert.ok(!text.includes(secret));
    const item = personalChannel(saved);
    assert.equal(item.apiKey, "");
    assert.equal(item.secretKey, "");
    assert.equal(item.headers[0].value, "");
    assert.ok(item.hasApiKey && item.hasSecretKey);
}
function personalChannel(saved) {
    const personal = saved.channels.filter(item => item.id !== "beefapi");
    assert.equal(personal.length, 1);
    const builtin = saved.channels.find(item => item.id === "beefapi");
    assert.ok(builtin?.pinned && builtin.name === "BeefAPI");
    assert.deepEqual(builtin.models, []);
    return personal[0];
}
function personalSection(current, channelId) {
    return current.locator(`.settings-channel[aria-labelledby="channel-${channelId}-title"]`);
}
async function renameService(current, name, channelId) {
    await personalSection(current, channelId).getByRole("button", { name: "编辑", exact: true }).click();
    const editor = current.getByRole("dialog");
    await editor.getByRole("button", { name: "连接信息", exact: true }).click();
    await editor.getByLabel("连接名称", { exact: true }).fill(name);
    await editor.getByRole("button", { name: "手动添加模型", exact: true }).click();
    return editor;
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const author = await browser.newContext(contextOptions());
    observe(author);
    await login(author, config.author, config.actorId);
    page = await openSettings(author);
    await page.locator(".settings-pane-header-actions").getByRole("button", { name: "添加模型服务", exact: true }).click();
    const editor = page.getByRole("dialog");
    await editor.getByLabel("连接名称", { exact: true }).fill("原版模型服务");
    await editor.getByLabel("API Key", { exact: true }).fill(secrets[0]);
    await editor.getByLabel("服务地址", { exact: true }).fill(config.providerURL || "https://controlled-model-catalog.example.test/v1");
    await editor.getByRole("button", { name: "高级设置", exact: true }).click();
    await editor.getByLabel("Secret Key", { exact: true }).fill(secrets[1]);
    await editor.getByRole("button", { name: "添加请求头", exact: true }).click();
    await editor.getByLabel("请求头 1 名称", { exact: true }).fill("X-Provider-Key");
    await editor.getByLabel("请求头 1 值", { exact: true }).fill(secrets[2]);
    await editor.getByRole("button", { name: "手动添加模型", exact: true }).click();
    await editor.getByLabel("模型 ID", { exact: true }).fill("gpt-settings-fixture");
    await editor.getByRole("button", { name: /^添\s*加$/ }).click();
    const created = await savedAfter(page, () => editor.getByRole("button", { name: "保存并使用", exact: true }).click());
    await editor.waitFor({ state: "hidden" });
    const createdPersonal = personalChannel(created);
    assert.equal(createdPersonal.name, "原版模型服务");
    report.source_service_created = true;
    const channelId = createdPersonal.id;
    const modelId = createdPersonal.modelProfiles[0].logicalModelId;
    assert.match(modelId, /^[1-9]\d{15,}$/);
    report.logical_id_acknowledged = true;
    assertRedacted(created);
    report.credentials_redacted = true;
    const selected = await savedAfter(page, () => page.getByRole("radiogroup", { name: "默认文本模型", exact: true }).getByRole("radio").click());
    assert.equal(selected.preferences.textModel, `${channelId}::gpt-settings-fixture`);
    report.default_choice_saved = true;
    const cache = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
    for (const secret of secrets) assert.ok(!cache.includes(secret));
    report.browser_cache_has_no_secrets = true;
    const fresh = await browser.newContext(contextOptions(await author.cookies()));
    observe(fresh);
    page = await openSettings(fresh);
    const restored = await read(await fresh.request.get(`${origin}${modelPath}`));
    assert.equal(personalChannel(restored).modelProfiles[0].logicalModelId, modelId);
    assert.equal(restored.preferences.textModel, `${channelId}::gpt-settings-fixture`);
    assertRedacted(restored);
    assert.equal(await page.getByRole("radiogroup", { name: "默认文本模型", exact: true }).getByRole("radio").getAttribute("aria-checked"), "true");
    report.fresh_browser_restored = true;
    const renamed = await renameService(page, "已更新模型服务", channelId);
    const updated = await savedAfter(page, () => renamed.getByRole("button", { name: "保存并使用", exact: true }).click());
    await renamed.waitFor({ state: "hidden" });
    assert.equal(personalChannel(updated).name, "已更新模型服务");
    assert.equal(personalChannel(updated).modelProfiles[0].logicalModelId, modelId);
    assertRedacted(updated);
    report.source_service_updated = true;
    const other = await browser.newContext(contextOptions());
    observe(other);
    await login(other, config.other, config.otherId);
    const privateCatalog = await read(await other.request.get(`${origin}${modelPath}`));
    assert.deepEqual(privateCatalog.channels.map(item => item.id), ["beefapi"]);
    assert.deepEqual(privateCatalog.channels[0].models, []);
    assert.deepEqual(privateCatalog.models, []);
    report.account_catalog_isolated = true;
    if (config.runModelTest) {
        await personalSection(page, channelId).getByRole("button", { name: "编辑", exact: true }).click();
        const serviceEditor = page.getByRole("dialog");
        await serviceEditor.getByRole("button", { name: "高级模型设置与测试", exact: true }).click();
        await serviceEditor.getByRole("button", { name: "配置使用", exact: true }).click();
        const modelEditor = page.locator(".model-editor-modal").getByRole("dialog");
        await modelEditor.getByRole("button", { name: "生成测试", exact: true }).click();
        const acceptedResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/model-tests" && response.request().method() === "POST" && response.ok());
        await page.locator(".ant-popconfirm").getByRole("button", { name: "开始生成", exact: true }).click();
        const accepted = await read(await acceptedResponse);
        await modelEditor.locator(".ant-alert-success").getByText("已收到文本响应", { exact: true }).waitFor({ state: "visible" });
        report.source_model_test_completed = true;
        assert.equal(report.model_test_submissions, 2);
        assert.deepEqual(modelTestAdmissions[0], modelTestAdmissions[1]);
        assert.ok(modelTestAdmissions[0].key);
        report.uncertain_model_test_ack_replayed = true;
        await modelEditor.getByRole("button", { name: "生成测试", exact: true }).click();
        const failedAcceptedResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/model-tests" && response.request().method() === "POST" && response.ok());
        await page.locator(".ant-popconfirm").getByRole("button", { name: "开始生成", exact: true }).click();
        const failedAccepted = await read(await failedAcceptedResponse);
        assert.notEqual(failedAccepted.id, accepted.id);
        await modelEditor.locator(".ant-alert-error").waitFor({ state: "visible" });
        assert.ok((await modelEditor.locator(".ant-alert-error").textContent()).trim());
        assert.equal(await modelEditor.locator(".ant-alert-success").count(), 0);
        report.source_model_test_failure_displayed = true;
        await modelEditor.getByRole("button", { name: /^完\s*成$/ }).click();
        await modelEditor.waitFor({ state: "hidden" });
        await serviceEditor.getByRole("button", { name: "Close", exact: true }).click();
        await serviceEditor.waitFor({ state: "hidden" });
        const actual = await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/tasks/${accepted.id}`));
        assert.equal(actual.status, "succeeded");
        assert.equal(JSON.parse(actual.resultJson).text, "OK from source model test HTTP");
        assert.equal("projectId" in actual, false);
        const failedActual = await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/tasks/${failedAccepted.id}`));
        assert.equal(failedActual.status, "failed");
        assert.equal("resultJson" in failedActual, false);
        for (const id of [accepted.id, failedAccepted.id]) {
            for (const path of [`/api/v1/canvas-runtime/model-tests/${id}`, `/api/v1/canvas-runtime/tasks/${id}`, `/api/v1/canvas-runtime/tasks/${id}/logs`]) {
                privateModelTestPaths.add(path);
                assert.equal((await other.request.get(`${origin}${path}`)).status(), 404);
            }
        }
        assert.deepEqual(await read(await other.request.get(`${origin}/api/v1/canvas-runtime/tasks`)), []);
        report.private_model_tests_hidden = true;
        await page.goto(`${origin}/canvas-app/tasks`);
        await page.getByRole("heading", { name: "创作历史", exact: true }).waitFor({ state: "visible" });
        const row = page.locator(".task-record-row").filter({ has: page.getByRole("button", { name: "Reply with OK.", exact: true }) }).filter({ hasText: "已完成" });
        await row.locator(".task-record-title").click();
        const detail = page.getByRole("dialog", { name: "任务详情", exact: true });
        await detail.locator("pre").filter({ hasText: "OK from source model test HTTP" }).waitFor({ state: "visible" });
        report.source_task_center_showed_actual_result = true;
        await detail.getByRole("button", { name: /^(?:Close|关闭)$/ }).click();
        page = await openSettings(fresh);
    }
    const stale = await browser.newContext(contextOptions(await author.cookies()));
    observe(stale);
    const stalePage = await openSettings(stale);
    const currentEditor = await renameService(page, "当前窗口已保存服务", channelId);
    await savedAfter(page, () => currentEditor.getByRole("button", { name: "保存并使用", exact: true }).click());
    await currentEditor.waitFor({ state: "hidden" });
    const staleEditor = await renameService(stalePage, "冲突窗口草稿", channelId);
    const conflictResponse = stalePage.waitForResponse(response => new URL(response.url()).pathname === modelPath && response.request().method() === "PUT" && response.status() === 409);
    await staleEditor.getByRole("button", { name: "保存并使用", exact: true }).click();
    await conflictResponse;
    await staleEditor.locator(".ant-alert-error").waitFor({ state: "visible" });
    assert.ok(await staleEditor.isVisible());
    await staleEditor.getByRole("button", { name: "连接信息", exact: true }).click();
    assert.equal(await staleEditor.getByLabel("连接名称", { exact: true }).inputValue(), "冲突窗口草稿");
    assert.equal(personalChannel(await read(await fresh.request.get(`${origin}${modelPath}`))).name, "当前窗口已保存服务");
    report.conflict_kept_draft = true;
    await stalePage.close();
    const deletedAck = page.waitForResponse(response => new URL(response.url()).pathname === modelPath && response.request().method() === "PUT" && response.ok());
    await page.getByRole("button", { name: "删除渠道 当前窗口已保存服务", exact: true }).click();
    await page.locator(".ant-popconfirm").getByRole("button", { name: /^删\s*除$/ }).click();
    const deleted = await read(await deletedAck);
    assert.deepEqual(deleted.channels.map(item => item.id), ["beefapi"]);
    assert.deepEqual(deleted.channels[0].models, []);
    report.source_service_deleted = true;
    assert.equal(report.canvas_task_submissions, 0);
    assert.equal(report.model_test_submissions, config.runModelTest ? 3 : 0);
    assert.equal(report.zero_generation_submissions, !config.runModelTest);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.failed_routes.filter(route => !(route.path === modelPath && route.status === 409) && !(route.path === "/api/v1/canvas-runtime/model-tests" && route.status === 503 && config.runModelTest) && !(privateModelTestPaths.has(route.path) && route.status === 404)), []);
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
