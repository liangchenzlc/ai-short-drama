import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../../src/services/approved-tool-execution.ts", import.meta.url), "utf8");
const javascript = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;

function runtime() {
    const captured = { userScope: "101", epoch: 1 };
    let active = captured;
    const calls = [];
    let saved;
    const identity = { kind: "art-critique", nodeId: "critique", sourceNodeId: "image", sourceFingerprint: "v1-12345678", model: "host-999::vision" };
    const options = { clientKey: "art-critique:analysis-one", projectId: "canvas-one", identity, signal: new AbortController().signal, onRun: id => calls.push(["receipt", id]) };
    const api = {
        create: async input => {
            calls.push(["create", input]);
            saved = { run: { id: "9007199254740999", canvasId: input.canvasId, state: input.state, executionEpoch: "90071992547409930" }, submissions: [] };
            return saved;
        },
        get: async () => { calls.push(["get"]); return saved; },
        claim: async (_id, input) => {
            calls.push(["claim", input]);
            return { ...saved.run, executionEpoch: String(BigInt(input.expectedEpoch) + 1n) };
        },
        prepare: async (_id, input) => {
            calls.push(["prepare", input]);
            return { id: input.itemKey, approvedAt: "2026-10-08T00:00:00Z", taskId: "accepted-task" };
        },
        approve: async () => { throw new Error("已受理阶段不应重新批准"); },
        execute: async (_id, input) => { calls.push(["execute", input]); return { id: "accepted-task" }; },
        release: async () => { calls.push(["release"]); return { released: true }; },
    };
    const prepare = input => ({ projectId: input.projectId, logicalModelId: "999", input: { metadata: { ...input.metadata, clientOperationId: input.clientOperationId || "fresh-random-operation" } } });
    const saveSource = async (input, scope) => { assert.equal(scope, captured); calls.push(["save", input]); };
    const modules = {
        "nanoid": { nanoid: () => "window-one" },
        "./api/creation-runs": { creationRuns: api },
        "./api/generation-task": { prepareBackendToolGenerationTask: prepare, parseBackendGenerationResult: task => JSON.parse(task.resultJson), runBackendToolGenerationTask: () => { throw new Error("不能绕过批准直接提交"); } },
        "./api/task-center": { waitForGenerationTask: async () => ({ resultJson: '{"text":"","toolCalls":[]}' }) },
        "@/services/canvas-generation-consumer": { persistCanvasGenerationSource: saveSource },
        "@/lib/user-scope": { getActiveUserScope: () => active.userScope },
        "@/lib/user-scope-guard": { captureUserScope: () => captured, assertUserScope: scope => { if (scope !== active) throw new DOMException("账号epoch已变化", "AbortError"); } },
    };
    const context = { exports: {}, require: name => { assert.ok(modules[name], name); return modules[name]; }, Error, AbortController, DOMException, setInterval: () => 1, clearInterval: () => {} };
    vm.runInNewContext(javascript, context);
    const request = stage => ({ projectId: "canvas-one", prompt: "审美分析", config: {}, metadata: { source: "art-critique", nodeId: "critique", runId: "analysis-one", stage } });
    return { Class: context.exports.ApprovedToolExecution, options, api, calls, request, captured, switchEpoch: () => { active = { ...captured, epoch: 2 }; } };
}

test("批准边界先保存图、冻结本人模型，再持久运行；版本不转为Number", async () => {
    const state = runtime();
    const execution = new state.Class(state.options);
    await execution.execute(state.request("review_art_color"));
    assert.equal(state.calls[0][0], "save");
    const create = state.calls.find(([kind]) => kind === "create")[1];
    assert.equal(create.canvasId, "canvas-one");
    assert.equal(create.state.logicalModelId, "999");
    assert.equal(state.calls.find(([kind]) => kind === "claim")[1].expectedEpoch, "90071992547409930");
    assert.equal(state.calls.find(([kind]) => kind === "prepare")[1].executionEpoch, "90071992547409931");
    execution.dispose();
});

test("五个并行阶段共用一次租约初始化，各阶段键在重新打开后保持相同", async () => {
    const state = runtime();
    const stages = ["analyze_art_scene", "review_art_composition", "review_art_color", "review_art_lighting", "review_art_structure"];
    const first = new state.Class(state.options);
    await Promise.all(stages.map(stage => first.execute(state.request(stage))));
    assert.equal(state.calls.filter(([kind]) => kind === "create").length, 1);
    assert.equal(state.calls.filter(([kind]) => kind === "claim").length, 1);
    const operations = state.calls.filter(([kind]) => kind === "prepare").map(([, input]) => input.request.input.metadata.clientOperationId);
    assert.equal(new Set(operations).size, stages.length);
    assert.deepEqual(operations, stages.map(stage => `canvas-tool:9007199254740999:${stage}`));
    first.dispose();
    const resumed = new state.Class({ ...state.options, runId: "9007199254740999" });
    await resumed.execute(state.request(stages[0]));
    assert.equal(state.calls.filter(([kind]) => kind === "prepare").at(-1)[1].request.input.metadata.clientOperationId, operations[0]);
    assert.equal(state.calls.filter(([kind]) => kind === "create").length, 1);
    resumed.dispose();
});

test("来源保存失败阻止创建运行和阶段准入", async () => {
    const state = runtime();
    const execution = new state.Class(state.options, state.api, undefined, async () => { throw new Error("保存版本冲突"); });
    await assert.rejects(execution.execute(state.request("review_art_color")), /保存版本冲突/);
    assert.equal(state.calls.filter(([kind]) => kind === "create" || kind === "execute").length, 0);
    execution.dispose();
});

test("同一账号ID的新epoch不能接收旧分析响应", async () => {
    const state = runtime();
    state.api.create = async input => {
        state.switchEpoch();
        return { run: { id: "run-one", state: input.state }, submissions: [] };
    };
    const execution = new state.Class(state.options);
    await assert.rejects(execution.execute(state.request("review_art_color")), /账号epoch已变化/);
    assert.equal(state.calls.filter(([kind]) => kind === "claim" || kind === "execute").length, 0);
    execution.dispose();
});
