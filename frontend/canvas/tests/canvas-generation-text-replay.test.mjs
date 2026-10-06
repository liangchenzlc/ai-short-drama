import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import { consumeTaskTextStream, createTaskTextStreamParser } from "../src/services/api/task-text-stream.ts";

const source = readFileSync(new URL("../src/services/api/task-center.ts", import.meta.url), "utf8");
const start = source.indexOf("async function waitForGenerationTaskTextEvents(");
const end = source.indexOf("function notifyCanvasTaskCreated", start);
assert.ok(start >= 0 && end > start);
const javascript = ts.transpileModule(source.slice(start, end), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const scope = { userScope: "101", epoch: 1 };
function stream(events) {
    return new Response(events.map(([event, sequence, payload]) => `event: ${event}\n${sequence === undefined ? "" : `id: ${sequence}\n`}data: ${JSON.stringify(payload)}\n\n`).join(""), { headers: { "content-type": "text/event-stream" } });
}
function setup(task, responses) {
    const calls = [];
    let queries = 0;
    const wait = vm.runInNewContext(`${javascript}\nwaitForGenerationTaskTextEvents`, {
        captureUserScope: () => scope, assertUserScope: captured => assert.deepEqual(captured, scope),
        queryGenerationTask: async () => ++queries === 1 ? task : { ...task, status: "succeeded" },
        apiBaseURL: "/api/v1/canvas-runtime", consumeTaskTextStream, createTaskTextStreamParser,
        fetch: async (url, config) => { calls.push({ url, config }); return responses.shift(); },
        generationErrorMessage: value => value, isUserScopeAbandonedError: () => false,
        Date, TextDecoder, DOMException, window: { setTimeout, clearTimeout },
    });
    return { wait, calls, queried: () => queries };
}

test("刷新草稿与textDraftSequence同时初始化，已存delta不重复拼接", async () => {
    const task = { id: "task", type: "canvas_text", status: "running", textDraft: "已保存", textDraftSequence: 2 };
    const runtime = setup({ ...task, status: "succeeded" }, [stream([["delta", 1, { sequence: 1, content: "已" }], ["delta", 3, { sequence: 3, content: "内容" }], ["terminal", undefined, { finalText: "已保存内容" }]])]);
    const deltas = [], updates = [];
    // initialTask skips the first detail query; return the final state here.
    const completed = await runtime.wait("task", { initialTask: task, onTextDelta: value => deltas.push(value), onTaskUpdate: value => updates.push(value), timeoutMs: 1000 });
    assert.equal(completed.status, "succeeded");
    assert.deepEqual(deltas, ["已保存", "已保存内容"]);
    assert.match(runtime.calls[0].url, /after=2$/);
    assert.equal(runtime.calls[0].config.headers["X-Canvas-Actor"], scope.userScope);
    assert.equal(updates.find(value => value.textDraftSequence === 3)?.textDraft, "已保存内容");
});

test("先读取detail也用其draft游标，旧无游标草稿从头重放得到同一全文", async () => {
    const task = { id: "task", type: "canvas_text", status: "running", textDraft: "旧", textDraftSequence: 1 };
    const runtime = setup(task, [stream([["delta", 2, { sequence: 2, content: "稿" }], ["terminal", undefined, { finalText: "旧稿" }]])]);
    const deltas = [];
    assert.equal((await runtime.wait("task", { onTextDelta: value => deltas.push(value), timeoutMs: 1000 })).status, "succeeded");
    assert.deepEqual(deltas, ["旧", "旧稿"]);
    assert.match(runtime.calls[0].url, /after=1$/);
    const legacy = setup({ ...task, textDraftSequence: undefined }, [stream([["delta", 1, { sequence: 1, content: "旧" }], ["delta", 2, { sequence: 2, content: "稿" }], ["terminal", undefined, { finalText: "旧稿" }]])]);
    const replay = [];
    await legacy.wait("task", { onTextDelta: value => replay.push(value), timeoutMs: 1000 });
    assert.deepEqual(replay, ["旧", "旧稿"]);
    assert.equal(legacy.calls[0].url.includes("after="), false);
});
