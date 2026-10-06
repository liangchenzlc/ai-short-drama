import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";

const bundled = await build({
    stdin: { contents: 'export * from "./src/services/host-beefapi-browser.ts"; export * from "./src/services/api/beefapi-connection.ts";', resolveDir: fileURLToPath(new URL("..", import.meta.url)) },
    tsconfig: fileURLToPath(new URL("../tsconfig.json", import.meta.url)), bundle: true, write: false, format: "esm", platform: "node", target: "node22",
    define: { "import.meta.env.DEV": "true", __CANVAS_BEEFAPI_TEST_ORIGIN__: JSON.stringify("http://127.0.0.1:49231") },
    plugins: [{ name: "external-boundaries", setup(builder) {
        builder.onResolve({ filter: /^@\/(?:services\/api\/request|lib\/user-scope-guard)$/ }, args => ({ path: args.path, namespace: "controlled" }));
        builder.onLoad({ filter: /.*/, namespace: "controlled" }, args => ({ loader: "js", contents: args.path.endsWith("request") ? 'export const http = { post: (...args) => globalThis.beefBrowserBoundary.post(...args) };' : 'export const captureUserScope = () => globalThis.beefBrowserBoundary.epoch; export const assertUserScope = epoch => { if (epoch !== globalThis.beefBrowserBoundary.epoch) throw new Error("账号已切换"); };' }));
    } }],
});
const { trustedBeefAPIOrigin, validateBeefAPIBrowserURL, openBeefAPIBrowser, startBeefAPIConnection, openBeefAPIWallet } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);
const enterprise = "https://enterprise.beefapi.com";
const loopback = "http://127.0.0.1:49231";
const defer = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
function popup(events = []) {
    return { opener: {}, closed: false, close() { this.closed = true; events.push("close"); }, location: { replace(url) { events.push(url); } } };
}
function browserBoundary(post) {
    const events = [];
    const page = popup(events);
    globalThis.beefBrowserBoundary = { epoch: 1, post };
    globalThis.window = { open(url, target) { events.push([url, target]); return page; } };
    return { events, page };
}

test("生产始终信任固定企业origin，开发测试仅接受显式精确loopback", () => {
    assert.equal(trustedBeefAPIOrigin(false, loopback), enterprise);
    assert.equal(trustedBeefAPIOrigin(true), enterprise);
    assert.equal(trustedBeefAPIOrigin(true, loopback), loopback);
    for (const origin of ["https://attacker.example", `${loopback}/`, `${loopback}?x=1`, "http://127.0.0.1.attacker.example"]) assert.throws(() => trustedBeefAPIOrigin(true, origin));
});

test("企业授权允许来源既有路径及查询，钱包只能精确充值路径", () => {
    assert.equal(validateBeefAPIBrowserURL(`${enterprise}/desktop-auth?user_code=A`, "authorization"), `${enterprise}/desktop-auth?user_code=A`);
    assert.equal(validateBeefAPIBrowserURL(`${enterprise}/desktop-auth/approve`, "authorization"), `${enterprise}/desktop-auth/approve`);
    assert.equal(validateBeefAPIBrowserURL(`${enterprise}/console/topup`, "wallet"), `${enterprise}/console/topup`);
});

test("服务器自报origin不能放宽可信地址，拒绝跳转、认证信息与规范化路径", () => {
    for (const value of [undefined, "https://attacker.example/desktop-auth", `${enterprise}/desktop-authentication`, `${enterprise}/console/topup`, `${enterprise}/desktop-auth#`, `${enterprise}/desktop-auth/../desktop-auth`, `${enterprise}/desktop-auth/%2e%2e/desktop-auth`, `${enterprise}\\desktop-auth`, "https://user:password@enterprise.beefapi.com/desktop-auth"]) assert.throws(() => validateBeefAPIBrowserURL(value, "authorization"));
    for (const value of [`${enterprise}/console/topup?`, `${enterprise}/console/topup?next=x`, `${enterprise}/console/topup#`, `${enterprise}/console/topup/`]) assert.throws(() => validateBeefAPIBrowserURL(value, "wallet"));
});

test("原按钮同步预开空页、切断opener，再等待HTTP并导航真实授权页", async () => {
    const accepted = defer();
    const env = browserBoundary(async path => { env.events.push(path); return accepted.promise; });
    const operation = startBeefAPIConnection();
    assert.deepEqual(env.events, [["about:blank", "_blank"], "/beefapi/connection/start"]);
    assert.equal(env.page.opener, null);
    accepted.resolve({ state: "pending", enterpriseOrigin: "https://ignored.example", verificationUri: `${loopback}/desktop-auth?user_code=A` });
    assert.equal((await operation).state, "pending");
    assert.equal(env.events.at(-1), `${loopback}/desktop-auth?user_code=A`);
});

test("原重连在disconnect之前预开页面，不丢失用户手势", async () => {
    const disconnected = defer();
    const env = browserBoundary(async path => { env.events.push(path); return path.endsWith("disconnect") ? disconnected.promise : { state: "pending", verificationUri: `${loopback}/desktop-auth` }; });
    const operation = startBeefAPIConnection(undefined, { disconnectFirst: true });
    assert.deepEqual(env.events, [["about:blank", "_blank"], "/beefapi/connection/disconnect"]);
    disconnected.resolve({ state: "disconnected" });
    await operation;
    assert.deepEqual(env.events.slice(2), ["/beefapi/connection/start", `${loopback}/desktop-auth`]);
});

test("弹窗被阻止时不提交设备授权或钱包请求", async () => {
    let requests = 0;
    browserBoundary(async () => { requests++; });
    globalThis.window.open = () => null;
    await assert.rejects(startBeefAPIConnection(), /阻止/);
    await assert.rejects(openBeefAPIWallet(), /阻止/);
    assert.equal(requests, 0);
});

test("HTTP失败、无效URL和账号变化关闭预开页面并明确失败", async () => {
    for (const reason of ["http", "url", "scope"]) {
        const env = browserBoundary(async () => {
            if (reason === "http") throw new Error("HTTP 503");
            if (reason === "scope") globalThis.beefBrowserBoundary.epoch++;
            return { state: "pending", verificationUri: reason === "url" ? "https://attacker.example/desktop-auth" : `${loopback}/desktop-auth` };
        });
        await assert.rejects(startBeefAPIConnection(), reason === "http" ? /503/ : reason === "scope" ? /账号已切换/ : /不受信任/);
        assert.equal(env.page.closed, true);
        assert.equal(env.events.at(-1), "close");
    }
});

test("用户关闭预开页面不能返回钱包opened成功", async () => {
    const env = browserBoundary(async () => { env.page.closed = true; return { walletUrl: `${loopback}/console/topup` }; });
    await assert.rejects(openBeefAPIWallet(), /已关闭/);
});

test("钱包实际导航后才返回源opened回执", async () => {
    const env = browserBoundary(async () => ({ walletUrl: `${loopback}/console/topup`, enterpriseOrigin: loopback }));
    assert.deepEqual(await openBeefAPIWallet(), { opened: true });
    assert.equal(env.events.at(-1), `${loopback}/console/topup`);
});

test("已有连接无需授权页面时关闭空页并保留真实状态", async () => {
    const page = popup();
    const result = await openBeefAPIBrowser(async () => ({ state: "connected" }), { kind: "authorization", assertActive() {}, open: () => page, url: result => result.verificationUri, shouldOpen: result => result.state === "pending" });
    assert.equal(result.state, "connected");
    assert.equal(page.closed, true);
});

test("等待重连disconnect时换账号或取消，不提交第二个start", async () => {
    for (const abandon of ["scope", "abort"]) {
        const disconnected = defer();
        const controller = new AbortController();
        const env = browserBoundary(async path => { env.events.push(path); return disconnected.promise; });
        const operation = startBeefAPIConnection(controller.signal, { disconnectFirst: true });
        if (abandon === "scope") globalThis.beefBrowserBoundary.epoch++;
        else controller.abort();
        disconnected.resolve({ state: "disconnected" });
        await assert.rejects(operation, abandon === "scope" ? /账号已切换/ : { name: "AbortError" });
        assert.equal(env.events.includes("/beefapi/connection/start"), false);
        assert.equal(env.page.closed, true);
    }
});
