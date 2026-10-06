import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import { canvasGenerationOperationBase, canvasTaskAdmissionIdentity, createCanvasTaskAdmission } from "../src/services/host-generation-admission.ts";

const source = readFileSync(new URL("../src/services/canvas-generation-consumer.ts", import.meta.url), "utf8");
const start = source.indexOf("export async function persistCanvasGenerationSource(");
const end = source.indexOf("function reconcileCanvasGenerationLiveProject", start);
assert.ok(start >= 0 && end > start);
const javascript = ts.transpileModule(source.slice(start, end).replace("export async function", "async function"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const scope = { userScope: "101", epoch: 1 };
const input = { projectId: "canvas-key", type: "canvas_text", prompt: "内容", logicalModelId: "999", input: { mode: "text", metadata: { nodeId: "target", sourceNodeId: "source", clientOperationId: "stable-operation" } } };

function setup(persist = async () => undefined) {
    let state = { nodes: [{ id: "source", metadata: { content: "最新来源" } }, { id: "target", metadata: { taskId: "old-task", taskClientOperationId: "old-operation" } }], connections: [{ id: "connection", fromNodeId: "source", toNodeId: "target" }], chatSessions: [], activeChatId: null };
    const adapter = { read: () => state, write: value => { state = value; } };
    const adapters = new Map([[`${scope.userScope}\0canvas-key`, adapter]]);
    const saveSource = vm.runInNewContext(`${javascript}\npersistCanvasGenerationSource`, {
        assertUserScope: captured => assert.deepEqual(captured, scope),
        canvasTaskAdmissionIdentity,
        canvasGenerationOperationBase,
        canvasGenerationLiveAdapterKey: (user, project) => `${user}\0${project}`,
        canvasGenerationLiveAdapters: adapters,
        CanvasStaleScopeError: class extends Error {},
        persistCanvasDocument: persist,
    });
    return { saveSource, adapters, read: () => state, write: adapter.write };
}

test("实际生成保存边界冻结最新来源、目标和连线，只更新本次私人操作关联", async () => {
    const calls = [];
    const runtime = setup(async (...args) => { calls.push(args); });
    await runtime.saveSource(input, scope);
    assert.equal(calls.length, 1);
    const [id, patch, captured] = calls[0];
    assert.equal(id, "canvas-key");
    assert.equal(captured, scope);
    assert.deepEqual(Object.keys(patch).sort(), ["connections", "nodes"]);
    assert.equal(patch.nodes[0].metadata.content, "最新来源");
    assert.equal(patch.nodes[1].metadata.taskId, undefined);
    assert.equal(patch.nodes[1].metadata.taskClientOperationId, "stable-operation");
    assert.equal(patch.nodes[1].metadata.status, "loading");
    assert.equal(patch.connections[0].toNodeId, "target");
});

test("缺失最新目标或切换画布均不能保存和POST旧来源", async () => {
    let posts = 0;
    const runtime = setup();
    runtime.write({ ...runtime.read(), nodes: [{ id: "source" }] });
    await assert.rejects(runtime.saveSource(input, scope), /来源或目标节点/);
    const changed = setup(async () => { changed.adapters.clear(); });
    const admission = createCanvasTaskAdmission({
        store: () => ({ getItem: () => null, setItem: () => undefined, removeItem: () => undefined }),
        assertCurrent: () => undefined, saveSource: changed.saveSource,
        post: async () => { posts++; return { id: "task" }; }, isUncertain: () => false,
    });
    await assert.rejects(admission.submit(input, scope));
    assert.equal(posts, 0);
});

test("服务端409阻止任务准入且保留可恢复的来源草稿", async () => {
    let posts = 0;
    const conflict = Object.assign(new Error("conflict"), { status: 409 });
    const runtime = setup(async () => { throw conflict; });
    const admission = createCanvasTaskAdmission({
        store: () => ({ getItem: () => null, setItem: () => undefined, removeItem: () => undefined }),
        assertCurrent: () => undefined, saveSource: runtime.saveSource,
        post: async () => { posts++; return { id: "task" }; }, isUncertain: () => false,
    });
    await assert.rejects(admission.submit(input, scope), error => error === conflict);
    assert.equal(posts, 0);
    assert.equal(runtime.read().nodes[0].metadata.content, "最新来源");
    assert.equal(runtime.read().nodes[1].metadata.taskClientOperationId, "stable-operation");
});

test("Config来源保存批次baseID，child仍保留独立任务操作且不混入source taskId", async () => {
    const runtime = setup();
    runtime.write({ ...runtime.read(), nodes: [{ id: "source", type: "config", metadata: {} }, { id: "target", metadata: {} }] });
    await runtime.saveSource({ ...input, input: { ...input.input, metadata: { ...input.input.metadata, clientOperationId: "batch-operation:2", batchIndex: 2, batchCount: 3 } } }, scope);
    assert.equal(runtime.read().nodes[0].metadata.taskClientOperationId, "batch-operation");
    assert.equal(runtime.read().nodes[0].metadata.taskId, undefined);
    assert.equal(runtime.read().nodes[1].metadata.taskClientOperationId, "batch-operation:2");
    assert.equal(runtime.read().nodes[1].metadata.generatedFromNodeId, "source");
});
