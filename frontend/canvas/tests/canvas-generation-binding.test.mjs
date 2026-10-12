import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { setImmediate } from "node:timers/promises";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

// 执行实际消费者与仓库串行边界；仅替换 HTTP、存储和账号边界。
function definitions(file, names) {
    const source = readFileSync(new URL(file, import.meta.url), "utf8");
    const parsed = ts.createSourceFile(file, source, ts.ScriptTarget.ES2022, true);
    return parsed.statements.filter(statement => names.includes(statement.name?.text))
        .map(statement => statement.getText(parsed).replace(/^export /, "")).join("\n");
}
const code = ts.transpileModule([
    definitions("../src/services/host-generation-admission.ts", ["isCanvasEditorToolInput", "isCanvasEditorToolTask"]),
    definitions("../src/services/local-workspace-repository.ts", ["withCanvasProjection", "withCanvasGenerationBinding"]),
    definitions("../src/services/canvas-generation-consumer.ts", ["CanvasBindFlushError", "CanvasBindProjectionAdoptionError", "bindBackendCanvasGenerationResult", "assertBindDispatchScope", "canonicalCanvasFromReceipt"]),
].join("\n"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;

function deferred() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

function setup() {
    const scope = { userScope: "101", epoch: 1 };
    let active = scope;
    let serverRevision = "9007199254740993";
    let confirmedRevision = serverRevision;
    let project = { id: "canvas-key", revision: confirmedRevision, nodes: [{ id: "target", metadata: { content: "", status: "loading", taskId: "task" } }], connections: [] };
    const events = [];
    const entered = deferred();
    const released = deferred();
    class ApiError extends Error {}
    class Abandoned extends Error {}
    class Paused extends Error {}
    const assertCurrent = captured => {
        if (active !== captured) throw new Abandoned("账号已切换");
    };
    async function commit() {
        assertCurrent(scope);
        events.push({ kind: "commit", expected: confirmedRevision, current: serverRevision });
        if (confirmedRevision !== serverRevision) throw Object.assign(new ApiError("canvas_revision_conflict"), { status: 409 });
    }
    async function adopt(canonical) {
        assertCurrent(scope);
        events.push({ kind: "adopt", revision: canonical.revision });
        confirmedRevision = canonical.revision;
        project = canonical;
        return canonical;
    }
    const globals = {
        canvasProjectionLocks: new Map(),
        saveKey: (user, id) => `${user}\0${id}`,
        assertDispatchGuard: assertCurrent,
        commitLiveCanvasDocumentUnlocked: commit,
        adoptServerConfirmedGenerationPatchUnlocked: adopt,
        assertUserScope: assertCurrent,
        captureUserScope: () => active,
        userScopeMatches: (left, right = active) => left === right,
        isUserScopeAbandonedError: error => error instanceof Abandoned,
        UserScopeAbandonedError: Abandoned,
        CanvasStaleScopeError: Abandoned,
        CanvasBackendSubmitPausedError: Paused,
        isCanvasRevisionConflict: error => error instanceof ApiError && error.status === 409,
        useCanvasStore: { getState: () => ({ projects: [project] }) },
        useAssetStore: { setState: () => undefined },
        attachNodeEffectKey: (task, node, slot) => `attach-node:${task}:${node}:${slot}`,
        isCanvasRevision: value => typeof value === "string" && /^[1-9]\d*$/.test(value),
        DOMException,
    };
    const context = vm.createContext(globals);
    vm.runInContext(`${code}\nthis.bind = bindBackendCanvasGenerationResult; this.projectLock = withCanvasProjection;`, context);
    context.persistCanvasDocument = () => context.projectLock(scope.userScope, project.id, commit);
    const input = {
        canvasId: project.id, nodeId: "target", task: { id: "task", status: "succeeded", outputs: [] },
        isCurrent: () => true, nodesRef: { current: project.nodes }, setNodes: () => undefined,
        runtime: {
            captureScope: () => scope, liveScope: () => active,
            hydrateOutputs: async () => undefined,
            adoptConfirmedProjection: adopt,
            bindOutput: async () => {
                assertCurrent(scope);
                serverRevision = String(BigInt(serverRevision) + 1n);
                events.push({ kind: "bind", revision: serverRevision });
                entered.resolve();
                await released.promise;
                return { result: { bindingStatus: "bound", revision: serverRevision, canvas: { ...project, revision: serverRevision, nodes: [{ id: "target", metadata: { content: "最终正文", status: "success", taskId: "task" } }] } } };
            },
        },
    };
    return { input, events, entered, released, bind: () => context.bind(input), save: () => context.projectLock(scope.userScope, project.id, commit), switchAccount: () => { active = { userScope: "202", epoch: 2 }; }, confirmed: () => confirmedRevision };
}

test("编辑器工具在任何保存、归档或绑定副作用之前拒绝普通节点绑定", async () => {
    const environment = setup();
    environment.input.task.clientContext = { source: "prompt-optimizer" };
    await assert.rejects(environment.bind(), /编辑器工具结果不能绑定/);
    assert.deepEqual(environment.events, []);
});

test("终态绑定的真实写入与回执采纳期间，自动保存等待并使用采纳后的大版本", async () => {
    const runtime = setup();
    const binding = runtime.bind();
    await runtime.entered.promise;
    const save = runtime.save();
    // 注册拒绝观察，避免红批在断言前产生未处理 Promise。
    void save.catch(() => undefined);
    try {
        await setImmediate();
        assert.equal(runtime.events.filter(event => event.kind === "commit").length, 1, "绑定回执尚未采纳，自动保存不能进入旧版本提交");
    } finally {
        runtime.released.resolve();
        await Promise.allSettled([binding, save]);
    }
    await binding;
    await save;
    assert.deepEqual(runtime.events.map(event => event.kind), ["commit", "bind", "adopt", "commit"]);
    assert.deepEqual(runtime.events.at(-1), { kind: "commit", expected: "9007199254740994", current: "9007199254740994" });
});

test("账号切换期间的旧绑定回执不能采纳，串行区间释放后新保存仍能结束", async () => {
    const runtime = setup();
    const binding = runtime.bind();
    void binding.catch(() => undefined);
    await runtime.entered.promise;
    runtime.switchAccount();
    runtime.released.resolve();
    await assert.rejects(binding);
    assert.equal(runtime.confirmed(), "9007199254740993");
    assert.equal(runtime.events.some(event => event.kind === "adopt"), false);
    await assert.rejects(runtime.save(), /账号已切换/);
});

test("绑定 HTTP 失败释放串行区间，后续保存能完成且不采纳失败回执", async () => {
    const runtime = setup();
    runtime.input.runtime.bindOutput = async () => { throw new Error("controlled bind HTTP failure"); };
    await assert.rejects(runtime.bind(), /controlled bind HTTP failure/);
    await runtime.save();
    assert.deepEqual(runtime.events.map(event => event.kind), ["commit", "commit"]);
    assert.equal(runtime.confirmed(), "9007199254740993");
});

test("来源保存 I/O 失败阻止绑定，后续保存不受遗留锁影响", async () => {
    const runtime = setup();
    let posts = 0;
    runtime.input.runtime.persistDocument = async () => { throw new Error("controlled save I/O failure"); };
    runtime.input.runtime.bindOutput = async () => { posts++; assert.fail("来源未保存不能绑定"); };
    await assert.rejects(runtime.bind(), error => error.name === "CanvasBindFlushError");
    await runtime.save();
    assert.equal(posts, 0);
    assert.deepEqual(runtime.events.map(event => event.kind), ["commit"]);
});

test("已排队消费者进入串行区间前重新校验账号，旧账号不能发出第二次绑定", async () => {
    const runtime = setup();
    const first = runtime.bind();
    void first.catch(() => undefined);
    await runtime.entered.promise;
    const second = runtime.bind();
    void second.catch(() => undefined);
    await setImmediate();
    runtime.switchAccount();
    runtime.released.resolve();
    await assert.rejects(first);
    await assert.rejects(second);
    assert.equal(runtime.events.filter(event => event.kind === "bind").length, 1);
    assert.equal(runtime.events.some(event => event.kind === "adopt"), false);
});

test("表情结果先走局部合成消费者，不向普通绑定入口提交供应商整图", async () => {
    const environment = setup();
    environment.input.nodesRef.current[0].metadata.emotionEdit = { sourceNodeId: "source" };
    let received;
    environment.input.runtime.consumeEmotionResult = async input => { received = input; };
    environment.input.runtime.bindOutput = async () => assert.fail("表情结果不能直接绑定供应商原图");
    await environment.bind();
    assert.equal(received.task, environment.input.task);
    assert.equal(received.canvasId, "canvas-key");
    assert.equal(received.nodeId, "target");
    assert.equal(received.expectedScope.userScope, "101");
    assert.deepEqual(environment.events, []);
});
