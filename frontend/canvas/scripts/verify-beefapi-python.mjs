import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4195";
const hostRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const output = resolve(".runtime/beefapi-python-browser");
const connectionPath = "/api/v1/canvas-runtime/beefapi/connection";
const modelPath = "/api/v1/ai-model-configs";
const workspacePath = "/api/v1/canvas-runtime/workspace/model-config";
const report = { official_entry_available: false, pending_authorization_opened: false, popup_opener_isolated: false, cancel_completed: false, controlled_enterprise_approval: false, scheduler_connected: false, managed_catalog_saved: false, fresh_browser_connected: false, wallet_actually_opened: false, other_account_isolated: false, secrets_not_exposed: false, disconnect_completed: false, managed_header_saved: false, managed_model_test_completed: false, task_center_showed_managed_result: false, managed_model_test_private: false, model_test_submissions: 0, page_errors: [], failed_routes: [] };
const responses = [];
const expectedFailures = new Set();
const testOrigin = new URL(config.enterpriseOrigin);
assert.equal(testOrigin.protocol, "http:");
assert.ok(["127.0.0.1", "localhost", "[::1]"].includes(testOrigin.hostname));
assert.equal(config.enterpriseOrigin, testOrigin.origin);
process.env.CANVAS_BEEFAPI_TEST_ORIGIN = testOrigin.origin;
await mkdir(output, { recursive: true });
const server = await createServer({ root: hostRoot, configFile: resolve(hostRoot, "vite.config.ts"), mode: "test", server: { host: "127.0.0.1", port: 4195, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
let modelTestId;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
function observe(context) {
    context.on("page", current => current.on("pageerror", error => report.page_errors.push(error.stack || error.message)));
    context.on("response", response => {
        const path = new URL(response.url()).pathname;
        if (path.startsWith("/api/v1/")) {
            if (response.status() >= 400) report.failed_routes.push({ method: response.request().method(), url: response.url(), path, status: response.status() });
            if (response.headers()["content-type"]?.includes("application/json")) responses.push(response.text().then(text => ({ path, text }), error => ({ path, error: error.message })));
        }
    });
    context.on("request", request => {
        if (request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/canvas-runtime/model-tests") report.model_test_submissions++;
    });
    context.setDefaultTimeout(30000); context.setDefaultNavigationTimeout(60000);
}
async function login(context, username, actorId) {
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf"); assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value, "X-Canvas-Actor": actorId });
}
function builtin(current) { return current.locator("details.config-beefapi"); }
async function settings(context) {
    const current = await context.newPage();
    await current.goto(`${origin}/ai_config`);
    await current.getByRole("heading", { name: "AI 配置", exact: true }).waitFor();
    await builtin(current).locator("summary").click();
    await builtin(current).getByRole("button", { name: /^(?:连接 BeefAPI|重新连接)$/ }).waitFor();
    return current;
}
async function authPopup(current, button) {
    const [popup, accepted] = await Promise.all([
        current.waitForEvent("popup"),
        current.waitForResponse(response => new URL(response.url()).pathname === `${connectionPath}/start` && response.ok()),
        builtin(current).getByRole("button", { name: button, exact: true }).click(),
    ]);
    const summary = await read(accepted);
    assert.equal(summary.state, "pending"); assert.ok(summary.userCode && summary.verificationUri);
    await popup.waitForURL(`${config.enterpriseOrigin}/desktop-auth?user_code=${summary.userCode}`);
    await popup.getByRole("heading", { name: `企业授权 ${summary.userCode}`, exact: true }).waitFor();
    assert.equal(await popup.evaluate(() => window.opener), null);
    await builtin(current).getByRole("status").filter({ hasText: summary.userCode }).waitFor();
    report.pending_authorization_opened = report.popup_opener_isolated = true;
    return popup;
}
async function hostModels(context, serviceType) {
    return (await read(await context.request.get(`${origin}${modelPath}?service_type=${serviceType}&offset=0&limit=20`))).items;
}
function textRow(current) { return current.getByRole("row").filter({ has: current.getByText("gpt-6-astra", { exact: true }) }); }
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const author = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    observe(author); await login(author, config.author, config.actorId);
    page = await settings(author);
    await builtin(page).getByRole("button", { name: "连接 BeefAPI", exact: true }).waitFor();
    report.official_entry_available = true;
    const cancelledPopup = await authPopup(page, "连接 BeefAPI");
    const [cancelledAck] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === `${connectionPath}/cancel` && response.ok()),
        builtin(page).getByRole("button", { name: "取消授权", exact: true }).click(),
    ]);
    assert.equal((await read(cancelledAck)).state, "cancelled");
    await builtin(page).getByText("已取消授权", { exact: true }).waitFor();
    report.cancel_completed = true; await cancelledPopup.close();
    const approvedPopup = await authPopup(page, "连接 BeefAPI");
    await approvedPopup.getByRole("button", { name: "确认授权", exact: true }).click();
    await approvedPopup.getByRole("heading", { name: "企业授权已确认", exact: true }).waitFor();
    report.controlled_enterprise_approval = true;
    await builtin(page).getByText("已连接 · 受控企业账号", { exact: true }).waitFor();
    const connected = await read(await author.request.get(`${origin}${connectionPath}`));
    assert.equal(connected.state, "connected"); assert.equal(connected.account.id, "9007199254740995"); assert.equal(connected.tokenId, "9007199254740997");
    report.scheduler_connected = true;
    const catalog = await read(await author.request.get(`${origin}${workspacePath}`));
    assert.deepEqual(catalog.channels, []);
    assert.deepEqual(catalog.models.map(item => item.model_key).sort(), ["gpt-6-astra", "gpt-image-1"].sort());
    assert.ok(catalog.models.every(item => /^[1-9]\d+$/.test(item.id) && item.credential_source === "beefapi" && item.has_api_key));
    assert.match(catalog.preferences.assistantModel, /^beefapi::/);
    const savedText = (await hostModels(author, "text")).find(item => item.model_key === "gpt-6-astra");
    assert.ok(savedText && savedText.credential_source === "beefapi" && savedText.runtime_profile.protocol === "chat-completion");
    const modelId = savedText.id; report.saved_model_id = modelId;
    report.managed_catalog_saved = true; await approvedPopup.close();
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies: await author.cookies(), origins: [] } });
    observe(fresh); page = await settings(fresh);
    await builtin(page).getByText("已连接 · 受控企业账号", { exact: true }).waitFor();
    assert.equal((await hostModels(fresh, "text"))[0].id, modelId);
    report.fresh_browser_connected = true;
    if (config.runModelTest) {
        await textRow(page).getByRole("button", { name: "编辑", exact: true }).click();
        const editor = page.getByRole("dialog", { name: "编辑文本模型", exact: true });
        assert.ok(await editor.getByLabel("API 密钥", { exact: true }).isDisabled());
        await editor.locator("summary").filter({ hasText: /^自定义请求头/ }).click();
        await editor.getByRole("button", { name: "添加请求头", exact: true }).click();
        await editor.getByLabel("请求头 1 名称", { exact: true }).fill("User-Agent");
        await editor.getByLabel("请求头 1 值", { exact: true }).fill("synthetic-managed-browser-header");
        const [headerSaved] = await Promise.all([
            page.waitForResponse(response => new URL(response.url()).pathname === `${modelPath}/${modelId}` && response.request().method() === "PATCH" && response.ok()),
            editor.getByRole("button", { name: "保存配置", exact: true }).click(),
        ]);
        const savedManaged = await read(headerSaved);
        assert.deepEqual(savedManaged.headers, [{ name: "User-Agent", has_value: true }]);
        assert.equal(savedManaged.credential_source, "beefapi"); assert.equal(savedManaged.id, modelId);
        await editor.waitFor({ state: "hidden" }); report.managed_header_saved = true;
        await textRow(page).getByRole("button", { name: /^更多操作：/ }).click();
        await page.getByRole("menuitem", { name: "测试模型", exact: true }).click();
        const testing = page.getByRole("dialog", { name: /^测试模型 · / });
        const [testAccepted] = await Promise.all([
            page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/model-tests" && response.request().method() === "POST" && response.ok()),
            testing.getByRole("button", { name: "开始模型测试", exact: true }).click(),
        ]);
        modelTestId = (await read(testAccepted)).id;
        await testing.getByRole("status").filter({ hasText: "模型已返回真实结果" }).waitFor();
        await testing.locator("pre").filter({ hasText: "OK from managed enterprise HTTP" }).waitFor();
        report.managed_model_test_completed = true;
        await testing.getByRole("button", { name: "关闭观察", exact: true }).click();
        const actual = await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/tasks/${modelTestId}`));
        assert.equal(actual.status, "succeeded"); assert.equal(JSON.parse(actual.resultJson).text, "OK from managed enterprise HTTP"); assert.equal("projectId" in actual, false);
        await page.goto(`${origin}/tasks/text?task=${modelTestId}`);
        await page.getByRole("heading", { name: "任务管理", exact: true }).waitFor();
        await page.getByRole("dialog", { name: "任务详情", exact: true }).locator("pre").filter({ hasText: "OK from managed enterprise HTTP" }).waitFor();
        report.task_center_showed_managed_result = true;
        page = await settings(fresh);
        await builtin(page).getByText("已连接 · 受控企业账号", { exact: true }).waitFor();
    }
    const [wallet] = await Promise.all([
        page.waitForEvent("popup"),
        builtin(page).getByRole("button", { name: "打开钱包", exact: true }).click(),
    ]);
    await wallet.waitForURL(`${config.enterpriseOrigin}/console/topup`);
    await wallet.getByRole("heading", { name: "企业钱包", exact: true }).waitFor();
    assert.equal(await wallet.evaluate(() => window.opener), null);
    report.wallet_actually_opened = true; await wallet.close();
    const other = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    observe(other); await login(other, config.other, config.otherId);
    const otherConnection = await read(await other.request.get(`${origin}${connectionPath}`));
    assert.equal(otherConnection.state, "disconnected"); assert.equal(otherConnection.hasCredential, false);
    assert.deepEqual((await read(await other.request.get(`${origin}${workspacePath}`))).models, []);
    assert.deepEqual(await hostModels(other, "text"), []); assert.deepEqual(await hostModels(other, "image"), []);
    const otherPage = await settings(other);
    await builtin(otherPage).getByRole("button", { name: "连接 BeefAPI", exact: true }).waitFor();
    report.other_account_isolated = true;
    if (modelTestId) {
        for (const path of [`/api/v1/canvas-runtime/model-tests/${modelTestId}`, `/api/v1/canvas-runtime/tasks/${modelTestId}`, `/api/v1/canvas-runtime/tasks/${modelTestId}/logs`, `/api/v1/generations/${modelTestId}`, `/api/v1/generations/${modelTestId}/records`]) {
            expectedFailures.add(`GET ${origin}${path}:404`); assert.equal((await other.request.get(`${origin}${path}`)).status(), 404);
        }
        assert.deepEqual(await read(await other.request.get(`${origin}/api/v1/canvas-runtime/tasks`)), []);
        report.managed_model_test_private = true;
    }
    const browserContent = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, text: document.body.innerText }));
    const publicHTTP = [JSON.stringify(connected), JSON.stringify(catalog), browserContent];
    const capturedResponses = await Promise.all(responses);
    assert.deepEqual(capturedResponses.filter(response => response.error), []);
    publicHTTP.push(...capturedResponses.map(response => response.text));
    for (const secret of ["synthetic-managed-browser-api-key", "synthetic-managed-browser-header", "synthetic-device-secret-", "deviceCode", "device_code"]) assert.ok(!publicHTTP.some(text => text.includes(secret)));
    report.secrets_not_exposed = true;
    await builtin(page).getByRole("button", { name: "断开连接", exact: true }).click();
    const [disconnectedAck] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === `${connectionPath}/disconnect` && response.ok()),
        page.getByRole("dialog", { name: "断开官方连接", exact: true }).getByRole("button", { name: "断开连接", exact: true }).click(),
    ]);
    assert.equal((await read(disconnectedAck)).state, "disconnected");
    await builtin(page).getByRole("button", { name: "连接 BeefAPI", exact: true }).waitFor();
    assert.deepEqual((await read(await fresh.request.get(`${origin}${workspacePath}`))).models, []);
    assert.deepEqual(await hostModels(fresh, "text"), []); assert.deepEqual(await hostModels(fresh, "image"), []);
    report.disconnect_completed = true;
    assert.deepEqual(report.page_errors, []);
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
