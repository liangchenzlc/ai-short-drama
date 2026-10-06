import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4194";
const hostRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const output = resolve(".runtime/model-settings-python-browser");
const modelPath = "/api/v1/ai-model-configs";
const workspacePath = "/api/v1/canvas-runtime/workspace/model-config";
const provider = "受控模型供应商";
const secrets = ["synthetic-browser-api-key", "synthetic-browser-secret-key", "synthetic-browser-header-key"];
const report = { host_model_created: false, logical_id_acknowledged: false, default_choice_saved: false, fresh_browser_restored: false, credentials_redacted: false, browser_cache_has_no_secrets: false, host_model_updated: false, account_catalog_isolated: false, conflict_kept_draft: false, host_model_deleted: false, official_entry_available: false, zero_generation_submissions: true, model_test_completed: false, model_test_failure_displayed: false, uncertain_model_test_ack_replayed: false, task_center_showed_actual_result: false, private_model_tests_hidden: false, canvas_task_submissions: 0, model_test_submissions: 0, page_errors: [], failed_routes: [] };
const modelTestAdmissions = [];
const expectedFailures = new Set();
await mkdir(output, { recursive: true });
process.env.CANVAS_BEEFAPI_TEST_ORIGIN = "";
const server = await createServer({ root: hostRoot, configFile: resolve(hostRoot, "vite.config.ts"), mode: "test", server: { host: "127.0.0.1", port: 4194, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
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
        if (response.status() >= 400 && path.startsWith("/api/")) report.failed_routes.push({ method: response.request().method(), url: response.url(), path, status: response.status() });
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
    await current.goto(`${origin}/ai_config`);
    await current.getByRole("heading", { name: "AI 配置", exact: true }).waitFor();
    await current.getByRole("button", { name: "添加文本模型", exact: true }).waitFor();
    await current.locator("summary").filter({ hasText: /^BeefAPI 官方连接/ }).click();
    await current.getByRole("button", { name: "连接 BeefAPI", exact: true }).waitFor();
    report.official_entry_available = true;
    return current;
}
async function acknowledged(current, path, method, action) {
    const ack = current.waitForResponse(response => new URL(response.url()).pathname === path && response.request().method() === method && response.ok());
    await action();
    return read(await ack);
}
function assertRedacted(saved) {
    for (const secret of secrets) assert.ok(!JSON.stringify(saved).includes(secret));
    assert.ok(saved.has_api_key && saved.has_secret_key);
    assert.deepEqual(saved.headers, [{ name: "X-Provider-Key", has_value: true }]);
    for (const field of ["apikey", "secret_key", "runtime_credentials_cipher"]) assert.equal(field in saved, false);
    assert.equal(saved.runtime_profile.protocol, "chat-completion");
    assert.deepEqual(saved.runtime_profile.default_options, { temperature: 0 });
}
function modelRow(current) {
    return current.getByRole("row").filter({ has: current.getByText(provider, { exact: true }) });
}
async function editModel(current, name) {
    await modelRow(current).getByRole("button", { name: "编辑", exact: true }).click();
    const editor = current.getByRole("dialog", { name: "编辑文本模型", exact: true });
    if (name) await editor.getByLabel("配置名称", { exact: true }).fill(name);
    return editor;
}
async function openModelTest(current) {
    await modelRow(current).getByRole("button", { name: /^更多操作：/ }).click();
    await current.getByRole("menuitem", { name: "测试模型", exact: true }).click();
    return current.getByRole("dialog", { name: /^测试模型 · / });
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const author = await browser.newContext(contextOptions());
    observe(author); await login(author, config.author, config.actorId);
    page = await openSettings(author);
    await page.getByRole("button", { name: "添加文本模型", exact: true }).click();
    const editor = page.getByRole("dialog", { name: "添加文本模型", exact: true });
    await editor.getByLabel("配置名称", { exact: true }).fill("宿主模型服务");
    await editor.getByLabel("厂商名称", { exact: true }).fill(provider);
    await editor.getByLabel("API 密钥", { exact: true }).fill(secrets[0]);
    await editor.getByLabel("Secret Key（第二密钥）", { exact: true }).fill(secrets[1]);
    await editor.getByLabel("服务地址（Base URL）", { exact: true }).fill(config.providerURL || "https://controlled-model-catalog.example.test/v1");
    await editor.locator("summary").filter({ hasText: /^自定义请求头/ }).click();
    await editor.getByRole("button", { name: "添加请求头", exact: true }).click();
    await editor.getByLabel("请求头 1 名称", { exact: true }).fill("X-Provider-Key");
    await editor.getByLabel("请求头 1 值", { exact: true }).fill(secrets[2]);
    await editor.getByRole("combobox", { name: "模型标识", exact: true }).fill("gpt-settings-fixture");
    await editor.locator("summary").filter({ hasText: /^高级配置/ }).click();
    await editor.getByRole("checkbox", { name: "使用指定协议与模型参数", exact: true }).check();
    await editor.getByLabel("请求协议", { exact: true }).fill("chat-completion");
    await editor.getByLabel(/^默认参数 JSON/).fill('{"temperature":0}');
    const created = await acknowledged(page, modelPath, "POST", () => editor.getByRole("button", { name: "保存配置", exact: true }).click());
    await editor.waitFor({ state: "hidden" });
    const modelId = created.id;
    assert.match(modelId, /^[1-9]\d{15,}$/);
    report.saved_model_id = modelId;
    report.host_model_created = report.logical_id_acknowledged = true;
    assertRedacted(created); report.credentials_redacted = true;
    const projected = await read(await author.request.get(`${origin}${workspacePath}`));
    assert.deepEqual(projected.channels, []);
    assert.ok(projected.models.some(item => item.id === modelId && item.name === created.name));
    await modelRow(page).getByRole("button", { name: /^更多操作：/ }).click();
    const selected = await acknowledged(page, `${modelPath}/${modelId}/default`, "PUT", () => page.getByRole("menuitem", { name: "设为默认", exact: true }).click());
    assert.equal(selected.id, modelId); assert.equal(selected.is_default, 1);
    report.default_choice_saved = true;
    const cache = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
    for (const secret of secrets) assert.ok(!cache.includes(secret));
    report.browser_cache_has_no_secrets = true;
    const fresh = await browser.newContext(contextOptions(await author.cookies()));
    observe(fresh); page = await openSettings(fresh);
    const restored = await read(await fresh.request.get(`${origin}${modelPath}/${modelId}`));
    assert.equal(restored.id, modelId); assert.equal(restored.is_default, 1); assertRedacted(restored);
    await modelRow(page).getByText("默认模型", { exact: true }).waitFor();
    report.fresh_browser_restored = true;
    const renamed = await editModel(page, "已更新宿主模型");
    const updated = await acknowledged(page, `${modelPath}/${modelId}`, "PATCH", () => renamed.getByRole("button", { name: "保存配置", exact: true }).click());
    await renamed.waitFor({ state: "hidden" });
    assert.equal(updated.name, "已更新宿主模型"); assert.equal(updated.id, modelId); assertRedacted(updated);
    report.host_model_updated = true;
    const other = await browser.newContext(contextOptions());
    observe(other); await login(other, config.other, config.otherId);
    assert.deepEqual((await read(await other.request.get(`${origin}${modelPath}?service_type=text&offset=0&limit=20`))).items, []);
    assert.deepEqual((await read(await other.request.get(`${origin}${workspacePath}`))).models, []);
    expectedFailures.add(`GET ${origin}${modelPath}/${modelId}:404`);
    assert.equal((await other.request.get(`${origin}${modelPath}/${modelId}`)).status(), 404);
    report.account_catalog_isolated = true;
    if (config.runModelTest) {
        let testing = await openModelTest(page);
        expectedFailures.add(`POST ${origin}/api/v1/canvas-runtime/model-tests:503`);
        const lostAck = page.waitForResponse(response => response.url() === `${origin}/api/v1/canvas-runtime/model-tests` && response.request().method() === "POST" && response.status() === 503);
        await testing.getByRole("button", { name: "开始模型测试", exact: true }).click();
        await lostAck;
        await testing.getByText("受理结果尚未确认；核对时会复用同一请求身份，不会新建测试。", { exact: true }).waitFor();
        const accepted = await acknowledged(page, "/api/v1/canvas-runtime/model-tests", "POST", () => testing.getByRole("button", { name: "核对原测试", exact: true }).click());
        await testing.getByRole("status").filter({ hasText: "模型已返回真实结果" }).waitFor();
        await testing.locator("pre").filter({ hasText: "OK from source model test HTTP" }).waitFor();
        report.model_test_completed = true;
        assert.equal(report.model_test_submissions, 2);
        assert.deepEqual(modelTestAdmissions[0], modelTestAdmissions[1]); assert.ok(modelTestAdmissions[0].key);
        report.uncertain_model_test_ack_replayed = true;
        await testing.getByRole("button", { name: "关闭观察", exact: true }).click();
        testing = await openModelTest(page);
        const failed = await acknowledged(page, "/api/v1/canvas-runtime/model-tests", "POST", () => testing.getByRole("button", { name: "开始模型测试", exact: true }).click());
        assert.notEqual(failed.id, accepted.id);
        await testing.getByRole("status").filter({ hasText: "测试失败" }).waitFor();
        assert.ok((await testing.getByRole("alert").textContent()).trim());
        report.model_test_failure_displayed = true;
        await testing.getByRole("button", { name: "关闭观察", exact: true }).click();
        const actual = await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/tasks/${accepted.id}`));
        assert.equal(actual.status, "succeeded"); assert.equal(JSON.parse(actual.resultJson).text, "OK from source model test HTTP");
        assert.equal("projectId" in actual, false);
        const failedActual = await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/tasks/${failed.id}`));
        assert.equal(failedActual.status, "failed"); assert.equal("resultJson" in failedActual, false);
        for (const id of [accepted.id, failed.id]) {
            for (const path of [`/api/v1/canvas-runtime/model-tests/${id}`, `/api/v1/canvas-runtime/tasks/${id}`, `/api/v1/canvas-runtime/tasks/${id}/logs`, `/api/v1/generations/${id}`, `/api/v1/generations/${id}/records`]) {
                expectedFailures.add(`GET ${origin}${path}:404`);
                assert.equal((await other.request.get(`${origin}${path}`)).status(), 404);
            }
        }
        assert.deepEqual(await read(await other.request.get(`${origin}/api/v1/canvas-runtime/tasks`)), []);
        report.private_model_tests_hidden = true;
        await page.goto(`${origin}/tasks/text?task=${accepted.id}`);
        await page.getByRole("heading", { name: "任务管理", exact: true }).waitFor();
        const detail = page.getByRole("dialog", { name: "任务详情", exact: true });
        await detail.locator("pre").filter({ hasText: "OK from source model test HTTP" }).waitFor();
        report.task_center_showed_actual_result = true;
        page = await openSettings(fresh);
    }
    const stale = await browser.newContext(contextOptions(await author.cookies()));
    observe(stale);
    const stalePage = await openSettings(stale);
    const staleEditor = await editModel(stalePage, "冲突窗口草稿");
    const currentEditor = await editModel(page, "当前窗口已保存模型");
    await acknowledged(page, `${modelPath}/${modelId}`, "PATCH", () => currentEditor.getByRole("button", { name: "保存配置", exact: true }).click());
    await currentEditor.waitFor({ state: "hidden" });
    expectedFailures.add(`PATCH ${origin}${modelPath}/${modelId}:409`);
    const conflict = stalePage.waitForResponse(response => new URL(response.url()).pathname === `${modelPath}/${modelId}` && response.request().method() === "PATCH" && response.status() === 409);
    await staleEditor.getByRole("button", { name: "保存配置", exact: true }).click(); await conflict;
    await staleEditor.getByRole("alert").waitFor();
    assert.equal(await staleEditor.getByLabel("配置名称", { exact: true }).inputValue(), "冲突窗口草稿");
    assert.ok(await staleEditor.getByRole("button", { name: "保存配置", exact: true }).isDisabled());
    const finalSaved = await read(await fresh.request.get(`${origin}${modelPath}/${modelId}`));
    assert.equal(finalSaved.name, "当前窗口已保存模型"); assertRedacted(finalSaved);
    report.conflict_kept_draft = true;
    await stalePage.close();
    await modelRow(page).getByRole("button", { name: /^更多操作：/ }).click();
    await page.getByRole("menuitem", { name: "删除配置", exact: true }).click();
    const deletion = page.waitForResponse(response => new URL(response.url()).pathname === `${modelPath}/${modelId}` && response.request().method() === "DELETE" && response.ok());
    await page.getByRole("dialog", { name: "删除配置", exact: true }).getByRole("button", { name: "确认删除", exact: true }).click();
    await deletion;
    expectedFailures.add(`GET ${origin}${modelPath}/${modelId}:404`);
    assert.equal((await fresh.request.get(`${origin}${modelPath}/${modelId}`)).status(), 404);
    assert.deepEqual((await read(await fresh.request.get(`${origin}${workspacePath}`))).models, []);
    assert.deepEqual((await read(await fresh.request.get(`${origin}${modelPath}?service_type=text&offset=0&limit=20`))).items, []);
    report.host_model_deleted = true;
    assert.equal(report.canvas_task_submissions, 0); assert.equal(report.model_test_submissions, config.runModelTest ? 3 : 0);
    assert.equal(report.zero_generation_submissions, !config.runModelTest); assert.deepEqual(report.page_errors, []);
    report.failed_routes = report.failed_routes.filter(route => !expectedFailures.has(`${route.method} ${route.url}:${route.status}`));
    assert.deepEqual(report.failed_routes, []);
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close(); await server.close();
}
process.stdout.write(JSON.stringify(report));
