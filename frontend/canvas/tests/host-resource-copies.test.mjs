import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";

const moduleUrl = source => `data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(source)).toString("base64")}`;
// Keep the production scope guard and copy adapter; control only identity and transport.
const identityUrl = moduleUrl(`
    export const state = { scope: "member", epoch: 1 };
    export const getActiveUserScope = () => state.scope;
    export const getActiveUserScopeEpoch = () => state.epoch;
`);
const identity = await import(identityUrl);
const guardSource = await readFile(new URL("../src/lib/user-scope-guard.ts", import.meta.url), "utf8");
const guardUrl = moduleUrl(guardSource.replace('"@/lib/user-scope"', JSON.stringify(identityUrl)));
const transportUrl = moduleUrl(`export const http = { post: async () => { throw new Error("unprepared transport"); } };`);
const { http } = await import(transportUrl);
const adapterSource = await readFile(new URL("../src/services/api/host-resource-copies.ts", import.meta.url), "utf8");
const { copyResourceToCanvas } = await import(moduleUrl(adapterSource
    .replace('"@/lib/user-scope-guard"', JSON.stringify(guardUrl))
    .replace('"@/services/api/request"', JSON.stringify(transportUrl))));
const input = { source_resource_id: "18446744073709551615", canvas_key: "captured-canvas" };

test("复制的未知响应由调用方用原请求恢复，保持超大 ID 与稳定键", async () => {
    const calls = [];
    const failure = Object.assign(new Error("response lost"), { status: 503 });
    const resource = { id: "18446744073709551614" };
    http.post = async (...args) => {
        calls.push(args);
        if (calls.length === 1) throw failure;
        return { resource };
    };
    const options = { idempotencyKey: "copy-once" };
    await assert.rejects(copyResourceToCanvas(input, options), error => error === failure);
    assert.equal(calls.length, 1);
    assert.equal(await copyResourceToCanvas(input, options), resource);
    assert.deepEqual(calls[0], calls[1]);
    assert.equal(calls[0][0], "/resources/copies");
    assert.deepEqual(calls[0][1], input);
    assert.equal(calls[0][2].headers["X-Idempotency-Key"], "copy-once");
    assert.equal(calls[0][2].timeout, 0);
});

test("复制期间切换账号再切回，旧结果仍不能回填；旧 scope 不能发新请求", async () => {
    const expectedScope = { userScope: identity.state.scope, epoch: identity.state.epoch };
    let finish;
    let calls = 0;
    http.post = async () => {
        calls += 1;
        await new Promise(resolve => { finish = resolve; });
        return { resource: { id: "123" } };
    };
    const pending = copyResourceToCanvas(input, { idempotencyKey: "account-test", expectedScope });
    identity.state.epoch += 2;
    finish();
    await assert.rejects(pending, { name: "UserScopeAbandonedError" });
    await assert.rejects(copyResourceToCanvas(input, { idempotencyKey: "account-test", expectedScope }), { name: "UserScopeAbandonedError" });
    assert.equal(calls, 1);
});

test("复制转交取消信号并保留取消错误，缺少请求键时不访问后端", async () => {
    const controller = new AbortController();
    const cancelled = new DOMException("cancelled", "AbortError");
    let calls = 0;
    http.post = async (_url, _input, options) => {
        calls += 1;
        assert.equal(options.signal, controller.signal);
        throw cancelled;
    };
    await assert.rejects(copyResourceToCanvas(input, { idempotencyKey: "cancel-test", signal: controller.signal }), error => error === cancelled);
    await assert.rejects(copyResourceToCanvas(input, { idempotencyKey: "  " }), /稳定请求标识/);
    assert.equal(calls, 1);
});
