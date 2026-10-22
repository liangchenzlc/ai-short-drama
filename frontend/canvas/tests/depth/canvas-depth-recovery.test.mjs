import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { webcrypto } from "node:crypto";
import ts from "typescript";

function moduleSource(file, names) {
    const parsed = ts.createSourceFile(file, readFileSync(new URL(file, import.meta.url), "utf8"), ts.ScriptTarget.ES2022, true);
    return parsed.statements.filter(statement => !ts.isImportDeclaration(statement) && (!names || names.includes(statement.name?.text)))
        .map(statement => statement.getText(parsed).replace(/^export /, "")).join("\n");
}
const guards = moduleSource("../../src/lib/user-scope-guard.ts", ["UserScopeAbandonedError", "captureUserScope", "userScopeMatches", "assertUserScope", "isUserScopeAbandonedError"]);
const executor = moduleSource("../../src/lib/plugins/builtin/editor/local-executor-session.ts");
const recovery = moduleSource("../../src/lib/canvas/canvas-depth-recover.ts");
const javascript = ts.transpileModule(`${guards}\n${executor}\n${recovery}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

// 执行真实账号守卫、操作身份、观察和恢复；仅替换HTTP、资源保存及图状态边界。
function setup() {
    let epoch = 1;
    let liveProject = "canvas-one";
    const node = { id: "depth-result", type: "video", title: "深度参考", position: { x: 10, y: 20 }, width: 320, height: 180, metadata: { status: "loading", depthSourceNodeId: "source", taskId: "301", taskClientOperationId: "depth-one", taskClientOperationInput: '["depth_capture","canvas-one","201"]' } };
    const nodes = { current: [structuredClone(node)] };
    const calls = { observe: [], resources: [], persist: [], create: [] };
    const task = { id: "301", status: "succeeded", clientOperationId: "depth-one", stage: "completed", progress: 100, resultJson: JSON.stringify({ resourceId: "401", width: 640, height: 360, durationMs: 1000 }) };
    const boundary = {
        observe: async (_id, options) => { options.onTaskUpdate(task); return task; },
        resource: async () => ({ id: "401", mimeType: "video/mp4", width: 640, height: 360, durationMs: 1000, size: 100 }),
        persist: async () => {},
    };
    const context = vm.createContext({
        Error, DOMException, AbortController, crypto: webcrypto,
        getActiveUserScope: () => "101",
        getActiveUserScopeEpoch: () => epoch,
        VIDEO_NODE_MAX_SIZE: { width: 640, height: 360 },
        fitNodeSize: (width, height) => ({ width, height }),
        mediaResultMetadata: (_kind, metadata) => metadata,
        generationErrorMessage: error => error.message,
        resourceFileUrl: id => `/api/v1/canvas-runtime/resources/${id}/file`,
        resourceStorageKey: id => `resource:${id}`,
        getResource: async (...args) => { calls.resources.push(args); return boundary.resource(...args); },
        waitForGenerationTask: async (...args) => { calls.observe.push(args); return boundary.observe(...args); },
        createDepthCaptureTask: async (...args) => { calls.create.push(args); return { ...task, status: "queued" }; },
    });
    vm.runInContext(`${javascript}\nObject.assign(this, { beginLocalExecutorSession, nextLocalExecutorClientOperationId, localExecutorIntentAfterError, recoverOwnedDepthCaptureNode, recoverOwnedDepthCaptureNodes, runOwnedDepthCapture });`, context);
    const controller = new AbortController();
    const session = context.beginLocalExecutorSession("canvas-one", { controller, getLiveProjectId: () => liveProject });
    return {
        context, node, nodes, calls, boundary, task, controller, session,
        edit: update => { nodes.current = nodes.current.map(update); },
        switchAccount: () => { epoch++; },
        switchProject: () => { liveProject = "canvas-two"; },
        recover: () => context.recoverOwnedDepthCaptureNode({
            node, session,
            getLiveNode: id => nodes.current.find(item => item.id === id),
            setNodes: update => { nodes.current = update(nodes.current); },
            persist: async (...args) => { calls.persist.push(args); return boundary.persist(...args); },
        }),
    };
}

function hold(state, name) {
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    const boundary = state.boundary[name];
    state.boundary[name] = async (...args) => { await pending; return boundary(...args); };
    return release;
}

async function until(predicate) {
    for (let attempt = 0; attempt < 50; attempt++) {
        if (predicate()) return;
        await new Promise(setImmediate);
    }
    assert.fail("实际恢复没有到达受控HTTP边界");
}

test("深度恢复观察原ID并以捕获scope和signal保存永久资源，不重新提交", async () => {
    const state = setup();
    await state.recover();
    assert.equal(state.calls.observe[0][0], "301");
    assert.equal(state.calls.resources[0][0], "401");
    assert.equal(state.calls.create.length, 0);
    assert.equal(state.calls.persist.length, 1);
    assert.equal(state.calls.persist[0][2].expectedScope, state.session.expectedScope);
    assert.equal(state.calls.persist[0][2].signal, state.controller.signal);
    const result = state.nodes.current[0];
    assert.equal(result.metadata.status, "success");
    assert.equal(result.metadata.storageKey, "resource:401");
    assert.equal(result.metadata.taskId, "301");
    assert.equal(result.metadata.taskClientOperationId, "depth-one");
});

test("深度恢复资源读取期间拖拽和改名保留当前位置、标题与编辑字段", async () => {
    const state = setup();
    const release = hold(state, "resource");
    const pending = state.recover();
    await until(() => state.calls.resources.length === 1);
    state.edit(node => ({ ...node, title: "新的标题", position: { x: 500, y: 800 }, metadata: { ...node.metadata, prompt: "新的说明", userLabel: "保留" } }));
    release();
    await pending;
    const result = state.nodes.current[0];
    assert.equal(result.title, "新的标题");
    assert.deepEqual(result.position, { x: 500, y: 800 });
    assert.equal(result.metadata.prompt, "新的说明");
    assert.equal(result.metadata.userLabel, "保留");
    assert.equal(state.calls.persist[0][0][0].title, "新的标题");
});

for (const field of ["taskId", "taskClientOperationId", "taskClientOperationInput", "depthSourceNodeId"]) {
    test(`观察期间${field}重绑后旧状态与终态不能覆盖新任务`, async () => {
        const state = setup();
        const release = hold(state, "observe");
        const pending = state.recover();
        await until(() => state.calls.observe.length === 1);
        state.edit(node => ({ ...node, metadata: { ...node.metadata, [field]: "replacement", taskStatus: "queued", taskStage: "新的任务", taskProgress: 0 } }));
        const before = JSON.stringify(state.nodes.current);
        release();
        await pending;
        assert.equal(JSON.stringify(state.nodes.current), before);
        assert.equal(state.calls.persist.length, 0);
        assert.equal(state.calls.resources.length, 0);
        assert.equal(state.calls.create.length, 0);
    });
}

for (const change of ["delete", "adopt_other_result", "task_rebound"]) {
    test(`资源响应迟到时${change}停止旧结果保存`, async () => {
        const state = setup();
        const release = hold(state, "resource");
        const pending = state.recover();
        await until(() => state.calls.resources.length === 1);
        if (change === "delete") state.nodes.current = [];
        else state.edit(node => ({ ...node, metadata: { ...node.metadata, ...(change === "adopt_other_result" ? { status: "success", storageKey: "resource:402", content: "/other.mp4" } : { taskId: "302", taskStatus: "queued" }) } }));
        const before = JSON.stringify(state.nodes.current);
        release();
        await pending;
        assert.equal(JSON.stringify(state.nodes.current), before);
        assert.equal(state.calls.persist.length, 0);
    });
}

for (const phase of ["observe", "resource"]) {
    for (const change of ["account_epoch", "project", "unmounted"]) {
        test(`深度${phase}期间${change}只停止观察、不提交或取消原任务`, async () => {
            const state = setup();
            const release = hold(state, phase);
            const pending = state.recover();
            await until(() => state.calls[phase === "observe" ? "observe" : "resources"].length === 1);
            const before = JSON.stringify(state.nodes.current);
            if (change === "account_epoch") state.switchAccount();
            else if (change === "project") state.switchProject();
            else state.controller.abort();
            release();
            await pending;
            assert.equal(JSON.stringify(state.nodes.current), before);
            assert.equal(state.calls.persist.length, 0);
            assert.equal(state.calls.create.length, 0);
        });
    }
}

test("旧任务观察失败晚到不能把重新绑定的任务标记为失败", async () => {
    const state = setup();
    state.boundary.observe = async () => { throw new Error("旧任务查询失败"); };
    const release = hold(state, "observe");
    const pending = state.recover();
    await until(() => state.calls.observe.length === 1);
    state.edit(node => ({ ...node, metadata: { ...node.metadata, taskId: "302", taskStatus: "queued", taskStage: "新任务" } }));
    const before = JSON.stringify(state.nodes.current);
    release();
    await pending;
    assert.equal(JSON.stringify(state.nodes.current), before);
    assert.equal(state.calls.persist.length, 0);
});

test("资源读取和保存失败保留原任务身份并就近反馈，不重发任务", async () => {
    for (const phase of ["resource", "persist"]) {
        const state = setup();
        state.boundary[phase] = async () => { throw new Error(`${phase}失败`); };
        await state.recover();
        assert.equal(state.nodes.current[0].metadata.taskId, "301");
        assert.equal(state.nodes.current[0].metadata.errorDetails, `${phase}失败`);
        assert.equal(state.nodes.current[0].metadata.status, "error");
        assert.equal(state.calls.create.length, 0);
    }
});

test("深度未知提交保持原键与冻结输入；明确拒绝或新内容使用新键", () => {
    const state = setup();
    const intent = { clientOperationId: "original-key", frozenInputKey: "original-input" };
    for (const status of [undefined, 408, 409, 429, 503]) {
        const error = Object.assign(new Error("提交未确认"), { status });
        const next = state.context.localExecutorIntentAfterError(intent, error);
        assert.equal(state.context.nextLocalExecutorClientOperationId(next, intent.frozenInputKey), intent.clientOperationId);
        assert.notEqual(state.context.nextLocalExecutorClientOperationId(next, "changed-input"), intent.clientOperationId);
    }
    const rejected = state.context.localExecutorIntentAfterError(intent, Object.assign(new Error("明确拒绝"), { status: 422 }));
    assert.equal(rejected.terminal, true);
    assert.notEqual(state.context.nextLocalExecutorClientOperationId(rejected, intent.frozenInputKey), intent.clientOperationId);
});

test("无回执的已保存pending节点保留稳定操作身份并反馈，不自动重新提交", async () => {
    const state = setup();
    delete state.node.metadata.taskId;
    delete state.nodes.current[0].metadata.taskId;
    await state.recover();
    assert.equal(state.nodes.current[0].metadata.status, "error");
    assert.equal(state.nodes.current[0].metadata.taskClientOperationId, "depth-one");
    assert.equal(state.calls.create.length, 0);
    assert.equal(state.calls.observe.length, 0);
});

for (const change of ["account_epoch", "project", "unmounted"]) {
    test(`无任务回执的恢复开始前${change}不改写旧节点`, async () => {
        const state = setup();
        delete state.node.metadata.taskId;
        delete state.nodes.current[0].metadata.taskId;
        const before = JSON.stringify(state.nodes.current);
        if (change === "account_epoch") state.switchAccount();
        else if (change === "project") state.switchProject();
        else state.controller.abort();
        await state.recover();
        assert.equal(JSON.stringify(state.nodes.current), before);
        assert.equal(state.calls.observe.length, 0);
        assert.equal(state.calls.create.length, 0);
    });
}

test("已中止的父画布信号不创建任何深度恢复观察器", async () => {
    const state = setup();
    const parent = new AbortController();
    parent.abort();
    const observers = new Set();
    state.context.recoverOwnedDepthCaptureNodes({
        nodes: state.nodes.current, signal: parent.signal,
        expectedScope: state.session.expectedScope, projectId: "canvas-one",
        getLiveProjectId: () => "canvas-one", observers,
        getLiveNode: id => state.nodes.current.find(item => item.id === id),
        setNodes: update => { state.nodes.current = update(state.nodes.current); },
        persist: async () => { assert.fail("已卸载画布不能保存深度结果"); },
    });
    await new Promise(setImmediate);
    assert.equal(observers.size, 0);
    assert.equal(state.calls.observe.length, 0);
    assert.equal(state.calls.create.length, 0);
    assert.equal(state.nodes.current[0].metadata.status, "loading");
});
