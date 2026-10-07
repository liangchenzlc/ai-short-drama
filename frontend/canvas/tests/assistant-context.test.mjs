import assert from "node:assert/strict";
import test from "node:test";
import { prepareCanvasAssistantContext } from "../src/pages/canvas/canvas-assistant-context.ts";

function fixture(overrides = {}) {
    let input = { nodes: [{ id: "current" }], connections: [], chatSessions: [], activeChatId: null, viewport: {} };
    const calls = [];
    const options = {
        canvasId: "canvas-source-key", getInput: () => input,
        selectedNodeIds: ["current", "other-canvas"],
        assertCurrent: () => {},
        flushPreferences: async () => { calls.push("preferences"); },
        preferences: () => ({ dirty: false, status: "saved" }),
        saveCanvas: async () => { calls.push("remote"); return true; },
        flushLocal: async () => { calls.push("local"); },
        hasUnconfirmedEdits: () => false,
        revision: () => "9007199254740999",
        ...overrides,
    };
    return { options, calls, change: () => { input = { ...input, nodes: [{ id: "new-input" }] }; } };
}

test("助手先保存偏好与作品，只引用当前画布节点并保留大版本字符串", async () => {
    const value = fixture();
    assert.deepEqual(await prepareCanvasAssistantContext(value.options), {
        kind: "canvas", id: "canvas-source-key", revision: "9007199254740999", selected: [{ kind: "node", id: "current" }],
    });
    assert.deepEqual(value.calls, ["preferences", "remote"]);
});

test("偏好冲突不发送作品，远端保存失败不返回上下文", async () => {
    const preference = fixture({ preferences: () => ({ dirty: true, status: "error", error: "409 偏好冲突" }) });
    await assert.rejects(prepareCanvasAssistantContext(preference.options), /409 偏好冲突/);
    assert.deepEqual(preference.calls, ["preferences"]);
    const canvas = fixture({ saveCanvas: async () => false });
    await assert.rejects(prepareCanvasAssistantContext(canvas.options), /画布尚未保存/);
});

test("保存期间出现新输入时保留本机草稿，不返回旧上下文", async () => {
    let complete;
    const value = fixture({ saveCanvas: () => new Promise(resolve => { complete = resolve; }) });
    const result = prepareCanvasAssistantContext(value.options);
    await new Promise(resolve => setImmediate(resolve));
    value.change();
    complete(true);
    await assert.rejects(result, /保存期间内容再次变化/);
    assert.deepEqual(value.calls, ["preferences", "local"]);
});

test("保存期间换画布或账号时不能用旧作品继续发送", async () => {
    let current = true;
    const value = fixture({
        assertCurrent: () => { if (!current) throw new Error("当前画布或账号已变化"); },
        saveCanvas: async () => { current = false; return true; },
    });
    await assert.rejects(prepareCanvasAssistantContext(value.options), /当前画布或账号已变化/);
});

test("缺少服务器版本或仍有未确认编辑时不能生成上下文", async () => {
    await assert.rejects(prepareCanvasAssistantContext(fixture({ revision: () => undefined }).options), /服务端版本/);
    await assert.rejects(prepareCanvasAssistantContext(fixture({ hasUnconfirmedEdits: () => true }).options), /保存期间内容再次变化/);
});
