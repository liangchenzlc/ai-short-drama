import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function source(file) {
    return ts.createSourceFile(file, readFileSync(new URL(file, import.meta.url), "utf8"), ts.ScriptTarget.ES2022, true);
}
const hookFile = source("../src/pages/canvas/use-canvas-media-tools.ts");
const hook = hookFile.statements.find(statement => statement.name?.text === "useCanvasMediaTools");
const callbacks = hook.body.statements.flatMap(statement => statement.declarationList?.declarations || [])
    .filter(item => ["depthCaptureNode", "retryDepthCaptureNode"].includes(item.name?.text));
assert.equal(callbacks.length, 2, "执行实际深度生成与重试回调");
const executorFile = source("../src/lib/plugins/builtin/editor/local-executor-session.ts");
const executor = executorFile.statements.filter(statement => !ts.isImportDeclaration(statement))
    .map(statement => statement.getText(executorFile).replace(/^export /, "")).join("\n");
const javascript = ts.transpileModule(`${executor}\n${callbacks.map(item => `const ${item.getText(hookFile)};`).join("\n")}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

// 实际生成、操作键和会话守卫；只替换HTTP、素材保存、React与图状态。
function setup(mode) {
    const scope = { userScope: "101", epoch: 1 };
    let activeScope = scope;
    const source = { id: "source", type: "video", title: "原视频", position: { x: 10, y: 20 }, width: 320, height: 180, metadata: { storageKey: "resource:201", content: "/source.mp4" } };
    const previous = { id: "depth-result", type: "video", title: "旧结果", position: { x: 440, y: 20 }, width: 320, height: 180, metadata: { status: "error", depthSourceNodeId: "source", taskId: "300", taskClientOperationId: "old-operation", taskClientOperationInput: '["depth_capture","canvas-one","201"]', taskClientOperationTerminal: true } };
    const nodesRef = { current: mode === "create" ? [source] : [source, previous] };
    const projectIdRef = { current: "canvas-one" };
    const depthObserversRef = { current: new Set() };
    const calls = { create: [], observe: [], resource: [], persist: [], messages: [] };
    const task = { id: "301", status: "succeeded", stage: "completed", progress: 100, resultJson: JSON.stringify({ resourceId: "401", width: 640, height: 360, durationMs: 1000 }) };
    const boundary = {
        create: async body => ({ ...task, status: "queued", clientOperationId: body.clientOperationId }),
        observe: async (_id, options) => { options.onTaskUpdate(task); return task; },
        resource: async () => ({ id: "401", mimeType: "video/mp4", size: 100, width: 640, height: 360, durationMs: 1000 }),
        persist: async () => {},
    };
    class Abandoned extends Error {}
    let ids = 0;
    const context = vm.createContext({
        Error, DOMException, AbortController, Set, crypto: webcrypto,
        projectId: "canvas-one", projectIdRef, nodesRef, depthObserversRef,
        useCallback: callback => callback,
        nanoid: () => ids++ ? "depth-edge" : "depth-result",
        NODE_STATUS_LOADING: "loading", NODE_STATUS_SUCCESS: "success", NODE_STATUS_ERROR: "error",
        CanvasNodeType: { Video: "video" },
        VIDEO_NODE_MAX_SIZE: { width: 640, height: 360 },
        captureUserScope: () => activeScope,
        assertUserScope: expected => { if (expected !== activeScope) throw new Abandoned("账号已切换"); },
        isUserScopeAbandonedError: error => error instanceof Abandoned,
        ownedResourceIdFromMediaRef: key => key?.startsWith("resource:") ? key.slice("resource:".length) : undefined,
        resourceFileUrl: id => `/api/v1/canvas-runtime/resources/${id}/file`,
        resourceStorageKey: id => `resource:${id}`,
        fitNodeSize: (width, height) => ({ width, height }),
        mediaResultMetadata: (_kind, metadata) => metadata,
        createDepthCaptureTask: async (...args) => { calls.create.push(args); return boundary.create(...args); },
        waitForGenerationTask: async (...args) => { calls.observe.push(args); return boundary.observe(...args); },
        getResource: async (...args) => { calls.resource.push(args); return boundary.resource(...args); },
        persistMediaNodes: async (...args) => { calls.persist.push(args); return boundary.persist(...args); },
        setNodes: update => { nodesRef.current = typeof update === "function" ? update(nodesRef.current) : update; },
        setConnections: () => {}, setSelectedNodeIds: () => {}, setSelectedConnectionId: () => {},
        message: { error: text => calls.messages.push(text) },
    });
    vm.runInContext(`${javascript}\nObject.assign(this, { depthCaptureNode, retryDepthCaptureNode });`, context);
    return {
        calls, boundary, scope, nodesRef, projectIdRef, depthObserversRef,
        run: () => mode === "create" ? context.depthCaptureNode(source) : context.retryDepthCaptureNode(previous),
        retry: () => context.retryDepthCaptureNode(nodesRef.current.find(node => node.id === "depth-result")),
        current: () => nodesRef.current.find(node => node.id === "depth-result"),
        edit: fields => { nodesRef.current = nodesRef.current.map(node => node.id === "depth-result" ? { ...node, ...fields, metadata: { ...node.metadata, ...fields.metadata } } : node); },
        switchAccount: () => { activeScope = { userScope: "101", epoch: 2 }; },
    };
}

function hold(state, phase) {
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    const boundary = state.boundary[phase];
    state.boundary[phase] = async (...args) => { await pending; return boundary(...args); };
    return release;
}

async function until(predicate) {
    for (let index = 0; index < 50; index++) {
        if (predicate()) return;
        await new Promise(setImmediate);
    }
    assert.fail("实际深度回调没有到达受控边界");
}

for (const mode of ["create", "retry"]) {
    test(`${mode}正常回填永久资源、同一节点与原任务身份`, async () => {
        const state = setup(mode);
        await state.run();
        assert.equal(state.current().metadata.storageKey, "resource:401");
        assert.equal(state.current().metadata.taskId, "301");
        assert.equal(state.current().metadata.status, "success");
        assert.equal(state.calls.create.length, 1);
        assert.equal(state.calls.observe[0][0], "301");
        assert.equal(state.calls.persist.length, 1);
        assert.equal(state.calls.persist[0][2].expectedScope, state.scope);
        assert.equal(state.calls.persist[0][2].signal.aborted, false);
        assert.equal(state.depthObserversRef.current.size, 0);
    });

    test(`${mode}资源响应迟到保留拖拽、改名、提示词和编辑元数据`, async () => {
        const state = setup(mode);
        const release = hold(state, "resource");
        const pending = state.run();
        await until(() => state.calls.resource.length === 1);
        state.edit({ title: "新标题", position: { x: 800, y: 900 }, metadata: { prompt: "新说明", userLabel: "保留" } });
        release();
        await pending;
        assert.equal(state.current().title, "新标题");
        assert.deepEqual(state.current().position, { x: 800, y: 900 });
        assert.equal(state.current().metadata.prompt, "新说明");
        assert.equal(state.current().metadata.userLabel, "保留");
        assert.equal(state.calls.persist[0][0][0].title, "新标题");
    });

    for (const field of ["taskId", "taskClientOperationId", "taskClientOperationInput", "depthSourceNodeId", "storageKey", "adopt", "delete"]) {
        test(`${mode}观察期间${field}变更停止旧状态和终态回填`, async () => {
            const state = setup(mode);
            const release = hold(state, "observe");
            const pending = state.run();
            await until(() => state.calls.observe.length === 1);
            if (field === "delete") state.nodesRef.current = state.nodesRef.current.filter(node => node.id !== "depth-result");
            else state.edit({ metadata: field === "adopt" ? { status: "success", storageKey: "resource:402" } : { [field]: "replacement", taskStage: "新的操作", taskProgress: 0 } });
            const before = JSON.stringify(state.nodesRef.current);
            release();
            await pending;
            assert.equal(JSON.stringify(state.nodesRef.current), before);
            assert.equal(state.calls.persist.length, 0);
            assert.deepEqual(state.calls.messages, []);
        });
    }

    test(`${mode}提交回执迟到不能绑定到替代操作`, async () => {
        const state = setup(mode);
        const release = hold(state, "create");
        const pending = state.run();
        await until(() => state.calls.create.length === 1);
        state.edit({ metadata: { taskClientOperationId: "replacement", taskStage: "新的操作" } });
        const before = JSON.stringify(state.nodesRef.current);
        release();
        await pending;
        assert.equal(JSON.stringify(state.nodesRef.current), before);
        assert.equal(state.calls.persist.length, 0);
    });

    for (const phase of ["create", "observe", "resource"]) {
        test(`${mode}${phase}旧错误不污染新任务`, async () => {
            const state = setup(mode);
            state.boundary[phase] = async () => { throw new Error("旧操作失败"); };
            const release = hold(state, phase);
            const pending = state.run();
            await until(() => state.calls[phase].length === 1);
            state.edit({ metadata: { taskClientOperationId: "replacement", taskStage: "新的操作" } });
            const before = JSON.stringify(state.nodesRef.current);
            release();
            await pending;
            assert.equal(JSON.stringify(state.nodesRef.current), before);
            assert.deepEqual(state.calls.messages, []);
        });
    }

    for (const change of ["account_epoch", "project", "unmounted"]) {
        test(`${mode}提交错误晚到时${change}静默停止`, async () => {
            const state = setup(mode);
            state.boundary.create = async () => { throw new Error("旧窗口提交失败"); };
            const release = hold(state, "create");
            const pending = state.run();
            await until(() => state.calls.create.length === 1);
            const before = JSON.stringify(state.nodesRef.current);
            if (change === "account_epoch") state.switchAccount();
            else if (change === "project") state.projectIdRef.current = "canvas-two";
            else for (const observer of state.depthObserversRef.current) observer.abort();
            release();
            await pending;
            assert.equal(JSON.stringify(state.nodesRef.current), before);
            assert.deepEqual(state.calls.messages, []);
            assert.equal(state.depthObserversRef.current.size, 0);
        });
    }

    test(`${mode}保存失败保留已绑定任务与永久资源并就近反馈`, async () => {
        const state = setup(mode);
        state.boundary.persist = async () => { throw new Error("保存失败"); };
        await state.run();
        assert.equal(state.current().metadata.taskId, "301");
        assert.equal(state.current().metadata.storageKey, "resource:401");
        assert.equal(state.current().metadata.status, "error");
        assert.equal(state.current().metadata.errorDetails, "保存失败");
        assert.deepEqual(state.calls.messages, ["保存失败"]);
    });

    for (const status of [undefined, 400]) {
        test(`${mode}提交${status ?? "unknown"}后明确重试遵循原操作键规则`, async () => {
            const state = setup(mode);
            state.boundary.create = async () => { throw Object.assign(new Error("提交失败"), { status }); };
            await state.run();
            const originalKey = state.calls.create[0][0].clientOperationId;
            assert.equal(state.current().metadata.taskStage === "submission_unknown", status === undefined);
            assert.equal(state.calls.create.length, 1, "不自动重新提交");
            await state.retry();
            assert.equal(state.calls.create.length, 2);
            assert.equal(state.calls.create[1][0].clientOperationId === originalKey, status === undefined);
        });
    }
}
