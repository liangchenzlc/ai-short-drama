import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4195";
const output = resolve(".runtime/beefapi-python-browser");
const connectionPath = "/api/v1/canvas-runtime/beefapi/connection";
const modelPath = "/api/v1/canvas-runtime/workspace/model-config";
const report = { builtin_available: false, pending_authorization_opened: false, popup_opener_isolated: false, source_cancel_completed: false, real_enterprise_approval: false, scheduler_connected: false, managed_catalog_saved: false, fresh_browser_connected: false, wallet_actually_opened: false, other_account_isolated: false, secrets_not_exposed: false, source_disconnect_completed: false, page_errors: [], failed_routes: [] };
Object.assign(report, { source_managed_header_saved: false, source_managed_model_test_completed: false, source_tasks_showed_managed_result: false, managed_model_test_private: false, model_test_submissions: 0 });
await mkdir(output, { recursive: true });
const server = await createServer({ define: { __CANVAS_BEEFAPI_TEST_ORIGIN__: JSON.stringify(config.enterpriseOrigin) }, server: { host: "127.0.0.1", port: 4195, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
const responses = [];
let modelTestId;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
function observe(context) {
    context.on("page", current => current.on("pageerror", error => report.page_errors.push(error.stack || error.message)));
    context.on("response", response => {
        const path = new URL(response.url()).pathname;
        if (path.startsWith("/api/")) {
            if (response.status() >= 400) report.failed_routes.push({ path, status: response.status() });
            if (path.startsWith("/api/v1/canvas-runtime/") && response.headers()["content-type"]?.includes("application/json")) {
                responses.push(response.text().then(text => ({ path, text }), error => ({ path, error: error.message })));
            }
        }
    });
    context.on("request", request => {
        if (request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/canvas-runtime/model-tests") report.model_test_submissions++;
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
function builtin(current) {
    return current.locator('.settings-channel[aria-labelledby="channel-beefapi-title"]');
}
async function settings(context) {
    const current = await context.newPage();
    await current.goto(`${origin}/canvas-app/settings`);
    await builtin(current).getByRole("heading", { name: "BeefAPI", exact: true }).waitFor();
    return current;
}
async function authPopup(current, button) {
    await current.waitForFunction(name => [...document.querySelectorAll('.settings-channel[aria-labelledby="channel-beefapi-title"] button')].some(button => button.textContent.replace(/\s/g, "") === name.replace(/\s/g, "") && !button.disabled && !button.classList.contains("ant-btn-loading")), button);
    const [popup, accepted] = await Promise.all([
        current.waitForEvent("popup"),
        current.waitForResponse(response => new URL(response.url()).pathname === `${connectionPath}/start` && response.ok()),
        builtin(current).getByRole("button", { name: button, exact: true }).click(),
    ]);
    const summary = await read(accepted);
    assert.equal(summary.state, "pending");
    assert.ok(summary.userCode && summary.verificationUri);
    await popup.waitForURL(`${config.enterpriseOrigin}/desktop-auth?user_code=${summary.userCode}`);
    await popup.getByRole("heading", { name: `企业授权 ${summary.userCode}`, exact: true }).waitFor();
    assert.equal(await popup.evaluate(() => window.opener), null);
    await builtin(current).getByText(`请在浏览器中确认 ${summary.userCode}`, { exact: true }).waitFor();
    report.pending_authorization_opened = report.popup_opener_isolated = true;
    return popup;
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const author = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    observe(author);
    await login(author, config.author, config.actorId);
    page = await settings(author);
    await builtin(page).getByRole("button", { name: "连接 BeefAPI", exact: true }).waitFor();
    report.builtin_available = true;
    const cancelledPopup = await authPopup(page, "连接 BeefAPI");
    const [cancelledAck] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === `${connectionPath}/cancel` && response.ok()),
        builtin(page).getByRole("button", { name: /^取\s*消$/ }).click(),
    ]);
    assert.equal((await read(cancelledAck)).state, "cancelled");
    await builtin(page).getByText("已取消本次连接", { exact: true }).waitFor();
    report.source_cancel_completed = true;
    await cancelledPopup.close();
    const approvedPopup = await authPopup(page, "重新连接");
    await approvedPopup.getByRole("button", { name: "确认授权", exact: true }).click();
    await approvedPopup.getByRole("heading", { name: "企业授权已确认", exact: true }).waitFor();
    report.real_enterprise_approval = true;
    await builtin(page).getByText("已连接 受控企业账号", { exact: true }).waitFor();
    await builtin(page).getByText("已保存 2 个模型", { exact: true }).waitFor();
    const connected = await read(await author.request.get(`${origin}${connectionPath}`));
    assert.equal(connected.state, "connected");
    assert.equal(connected.account.id, "9007199254740995");
    assert.equal(connected.tokenId, "9007199254740997");
    report.scheduler_connected = true;
    const catalog = await read(await author.request.get(`${origin}${modelPath}`));
    const managed = catalog.channels.find(item => item.id === "beefapi");
    assert.equal(managed.credentialRef, "beefapi-enterprise");
    assert.ok(managed.hasApiKey && !managed.apiKey);
    assert.deepEqual(managed.models, ["gpt-6-astra", "gpt-image-1"]);
    assert.ok(managed.modelProfiles.every(profile => /^[1-9]\d+$/.test(profile.logicalModelId)));
    assert.match(catalog.preferences.assistantModel, /^beefapi::/);
    report.managed_catalog_saved = true;
    await approvedPopup.close();
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies: await author.cookies(), origins: [] } });
    observe(fresh);
    page = await settings(fresh);
    await builtin(page).getByText("已连接 受控企业账号", { exact: true }).waitFor();
    await builtin(page).getByText("已保存 2 个模型", { exact: true }).waitFor();
    report.fresh_browser_connected = true;
    if (config.runModelTest) {
        await builtin(page).getByRole("button", { name: "编辑", exact: true }).click();
        const channelEditor = page.locator(".model-editor-modal").getByRole("dialog");
        const [headerAdded] = await Promise.all([
            page.waitForResponse(response => new URL(response.url()).pathname === modelPath && response.request().method() === "PUT" && response.ok()),
            channelEditor.getByRole("button", { name: "添加 User-Agent", exact: true }).click(),
        ]);
        await read(headerAdded);
        const [headerSaved] = await Promise.all([
            page.waitForResponse(response => new URL(response.url()).pathname === modelPath && response.request().method() === "PUT" && response.ok()),
            channelEditor.getByLabel("请求头 1 值", { exact: true }).fill("synthetic-managed-browser-header"),
        ]);
        const savedManaged = (await read(headerSaved)).channels.find(item => item.id === "beefapi");
        assert.deepEqual(savedManaged.headers, [{ name: "User-Agent", value: "" }]);
        report.source_managed_header_saved = true;
        const textRow = channelEditor.locator('div[title="gpt-6-astra"]').locator("..").locator("..");
        await textRow.getByRole("button", { name: "配置使用", exact: true }).click();
        const modelEditor = page.getByRole("dialog").filter({ hasText: "编辑模型使用配置" });
        await modelEditor.getByRole("button", { name: "生成测试", exact: true }).click();
        const [testAccepted] = await Promise.all([
            page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/model-tests" && response.request().method() === "POST" && response.ok()),
            page.locator(".ant-popconfirm").getByRole("button", { name: "开始生成", exact: true }).click(),
        ]);
        modelTestId = (await read(testAccepted)).id;
        await modelEditor.locator(".ant-alert-success").getByText("已收到文本响应", { exact: true }).waitFor();
        report.source_managed_model_test_completed = true;
        await modelEditor.getByRole("button", { name: /^完\s*成$/ }).click();
        await modelEditor.waitFor({ state: "hidden" });
        await channelEditor.getByRole("button", { name: /^完\s*成$/ }).click();
        await channelEditor.waitFor({ state: "hidden" });
        const actual = await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/tasks/${modelTestId}`));
        assert.equal(actual.status, "succeeded");
        assert.equal(JSON.parse(actual.resultJson).text, "OK from managed enterprise HTTP");
        assert.equal("projectId" in actual, false);
        await page.goto(`${origin}/canvas-app/tasks`);
        await page.getByRole("heading", { name: "创作历史", exact: true }).waitFor();
        const row = page.locator(".task-record-row").filter({ has: page.getByRole("button", { name: "Reply with OK.", exact: true }) }).filter({ hasText: "已完成" });
        await row.locator(".task-record-title").click();
        const detail = page.getByRole("dialog", { name: "任务详情", exact: true });
        await detail.locator("pre").filter({ hasText: "OK from managed enterprise HTTP" }).waitFor();
        report.source_tasks_showed_managed_result = true;
        await detail.getByRole("button", { name: /^(?:Close|关闭)$/ }).click();
        page = await settings(fresh);
        await builtin(page).getByText("已连接 受控企业账号", { exact: true }).waitFor();
    }
    const [wallet] = await Promise.all([
        page.waitForEvent("popup"),
        builtin(page).getByRole("button", { name: "打开企业钱包", exact: true }).click(),
    ]);
    await wallet.waitForURL(`${config.enterpriseOrigin}/console/topup`);
    await wallet.getByRole("heading", { name: "企业钱包", exact: true }).waitFor();
    assert.equal(await wallet.evaluate(() => window.opener), null);
    report.wallet_actually_opened = true;
    await wallet.close();
    const other = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    observe(other);
    await login(other, config.other, config.otherId);
    const otherConnection = await read(await other.request.get(`${origin}${connectionPath}`));
    assert.equal(otherConnection.state, "disconnected");
    assert.equal(otherConnection.hasCredential, false);
    const otherCatalog = await read(await other.request.get(`${origin}${modelPath}`));
    assert.deepEqual(otherCatalog.channels.map(item => item.id), ["beefapi"]);
    assert.deepEqual(otherCatalog.channels[0].models, []);
    assert.ok(!otherCatalog.channels[0].hasApiKey);
    const otherPage = await settings(other);
    await builtin(otherPage).getByRole("button", { name: "连接 BeefAPI", exact: true }).waitFor();
    report.other_account_isolated = true;
    if (modelTestId) {
        for (const path of [`/api/v1/canvas-runtime/model-tests/${modelTestId}`, `/api/v1/canvas-runtime/tasks/${modelTestId}`, `/api/v1/canvas-runtime/tasks/${modelTestId}/logs`]) assert.equal((await other.request.get(`${origin}${path}`)).status(), 404);
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
    const [disconnectedAck] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === `${connectionPath}/disconnect` && response.ok()),
        builtin(page).getByRole("button", { name: "断开连接", exact: true }).click(),
    ]);
    assert.equal((await read(disconnectedAck)).state, "disconnected");
    await builtin(page).getByRole("button", { name: "连接 BeefAPI", exact: true }).waitFor();
    await builtin(page).getByText("已保存 0 个模型", { exact: true }).waitFor();
    const disconnectedCatalog = await read(await fresh.request.get(`${origin}${modelPath}`));
    const cleared = disconnectedCatalog.channels.find(item => item.id === "beefapi");
    assert.deepEqual(cleared.models, []);
    assert.ok(!cleared.hasApiKey && !cleared.credentialRef);
    report.source_disconnect_completed = true;
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.failed_routes, []);
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
