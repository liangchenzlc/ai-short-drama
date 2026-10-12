import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const file = "../src/pages/canvas/use-canvas-media-tools.ts";
const parsed = ts.createSourceFile(file, readFileSync(new URL(file, import.meta.url), "utf8"), ts.ScriptTarget.ES2022, true);
const hook = parsed.statements.find(statement => statement.name?.text === "useCanvasMediaTools");
const declaration = hook.body.statements.flatMap(statement => statement.declarationList?.declarations || [])
    .find(item => item.name?.text === "addPanoramaCaptureNode");
assert.ok(declaration, "真实全景导出回调必须存在");
const sessionFile = "../src/lib/plugins/builtin/editor/local-executor-session.ts";
const session = ts.createSourceFile(sessionFile, readFileSync(new URL(sessionFile, import.meta.url), "utf8"), ts.ScriptTarget.ES2022, true);
const sessionSource = session.statements.filter(statement => ["beginLocalExecutorSession", "assertLocalExecutorSession", "isLocalExecutorSessionStop"].includes(statement.name?.text))
    .map(statement => statement.getText(session).replace(/^export /, "")).join("\n");
const referenceFile = "../src/lib/canvas/canvas-resource-references.ts";
const references = ts.createSourceFile(referenceFile, readFileSync(new URL(referenceFile, import.meta.url), "utf8"), ts.ScriptTarget.ES2022, true);
const referenceSource = references.statements.filter(statement => ["getContextResourceNodes", "isResourceNode", "resourceKind"].includes(statement.name?.text))
    .map(statement => statement.getText(references).replace(/^export /, "")).join("\n");
const javascript = ts.transpileModule(`${sessionSource}\n${referenceSource}\nconst ${declaration.getText(parsed)};`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

// 执行真实导出回调和编辑器会话校验；仅替换上传、素材保存、React与图状态。
function setup() {
    const expectedScope = { userScope: "101", epoch: 1 };
    let activeScope = expectedScope;
    const source = { id: "source", type: "image", metadata: { storageKey: "resource:201", content: "/source.png" } };
    const panorama = { id: "panorama", type: "panorama", title: "全景", position: { x: 10, y: 20 }, width: 320, metadata: { panoramaConfig: { sourceMode: "image", projection: "spherical", directImageUrl: "/source.png" } } };
    const nodesRef = { current: [source, panorama] };
    const connectionsRef = { current: [{ id: "source-edge", fromNodeId: "source", toNodeId: "panorama" }] };
    const projectIdRef = { current: "canvas-one" };
    const imageOperationsRef = { current: new Set() };
    const calls = { upload: [], persist: [], messages: [] };
    class Abandoned extends Error {}
    const boundary = {
        upload: async () => ({ storageKey: "resource:301", url: "/capture.png", width: 400, height: 200 }),
        persist: async () => new Map([["capture", "asset-one"]]),
    };
    const context = vm.createContext({
        Error, DOMException, AbortController, Set,
        projectId: "canvas-one", projectIdRef, nodesRef, connectionsRef, imageOperationsRef,
        useCallback: callback => callback,
        nanoid: () => "capture",
        CanvasNodeType: { Image: "image", Panorama: "panorama" },
        captureUserScope: () => activeScope,
        assertUserScope: expected => { if (expected !== activeScope) throw new Abandoned("账号已切换"); },
        isUserScopeAbandonedError: error => error instanceof Abandoned,
        getNodeResourceKind: node => node.type === "image" ? "image" : null,
        uploadImage: async (...args) => { calls.upload.push(args); return boundary.upload(...args); },
        fitNodeSize: (width, height) => ({ width, height }),
        imageMetadata: image => ({ storageKey: image.storageKey, content: image.url }),
        mediaResultMetadata: (_kind, metadata) => metadata,
        persistMediaNodes: async (...args) => { calls.persist.push(args); return boundary.persist(...args); },
        setNodes: update => { nodesRef.current = typeof update === "function" ? update(nodesRef.current) : update; },
        setConnections: update => { connectionsRef.current = typeof update === "function" ? update(connectionsRef.current) : update; },
        message: {
            success: text => calls.messages.push({ kind: "success", text }),
            error: text => calls.messages.push({ kind: "error", text }),
        },
    });
    vm.runInContext(`${javascript}\nthis.capture = addPanoramaCaptureNode;`, context);
    return {
        capture: () => context.capture(panorama, "data:image/png;base64,capture", "全景 · 当前视角"),
        boundary, expectedScope, source, panorama, nodesRef, connectionsRef, projectIdRef, imageOperationsRef, calls,
        switchAccount: () => { activeScope = { userScope: "101", epoch: 2 }; },
    };
}

function delayedUpload(state) {
    let release;
    const ready = new Promise(resolve => { release = resolve; });
    const upload = state.boundary.upload;
    state.boundary.upload = async (...args) => { await ready; return upload(...args); };
    return release;
}

test("全景PNG正常上传、素材保存与来源连线，保存确认后才提示成功", async () => {
    const state = setup();
    await state.capture();
    assert.equal(state.nodesRef.current.length, 3);
    const output = state.nodesRef.current.find(node => node.id === "capture");
    assert.equal(output.metadata.storageKey, "resource:301");
    assert.equal(output.metadata.generatedFromNodeId, "panorama");
    assert.equal(state.connectionsRef.current.length, 2);
    assert.equal(state.calls.persist.length, 1);
    assert.equal(state.calls.upload[0][2], state.expectedScope);
    assert.equal(state.calls.upload[0][3], "canvas-one");
    assert.equal(state.calls.persist[0][1][0].fromNodeId, "panorama");
    assert.equal(state.calls.persist[0][1][0].toNodeId, "capture");
    assert.equal(state.calls.persist[0][2].expectedScope, state.expectedScope);
    assert.equal(state.calls.persist[0][2].signal.aborted, false);
    assert.equal(state.calls.messages[0].kind, "success");
    assert.equal(state.imageOperationsRef.current.size, 0);
});

for (const change of ["deleted", "source_changed", "source_deleted", "projection_changed", "project_changed", "account_epoch", "unmounted"]) {
    test(`全景上传期间${change}停止旧结果回填`, async () => {
        const state = setup();
        const release = delayedUpload(state);
        const pending = state.capture();
        if (change === "deleted") {
            state.nodesRef.current = [state.source];
            state.connectionsRef.current = [];
        } else if (change === "source_changed") {
            state.source.metadata = { storageKey: "resource:202", content: "/replacement.png" };
        } else if (change === "source_deleted") {
            state.nodesRef.current = [state.panorama];
            state.connectionsRef.current = [];
        } else if (change === "projection_changed") {
            state.panorama.metadata.panoramaConfig.projection = "cylindrical";
        } else if (change === "project_changed") {
            state.projectIdRef.current = "other-canvas";
        } else if (change === "account_epoch") state.switchAccount();
        else for (const controller of state.imageOperationsRef.current) controller.abort();
        release();
        await pending;
        assert.ok(state.nodesRef.current.every(node => node.id !== "capture"));
        assert.ok(state.connectionsRef.current.every(edge => edge.toNodeId !== "capture"));
        assert.equal(state.calls.persist.length, 0);
        assert.deepEqual(state.calls.messages, []);
        assert.equal(state.imageOperationsRef.current.size, 0);
    });
}

test("全景上传期间移动节点保留新位置，导出仍连接原来源", async () => {
    const state = setup();
    const release = delayedUpload(state);
    const pending = state.capture();
    state.nodesRef.current = [state.source, { ...state.panorama, position: { x: 500, y: 200 } }];
    release();
    await pending;
    const output = state.nodesRef.current.find(node => node.id === "capture");
    assert.equal(output.position.x, 916);
    assert.equal(output.position.y, 200);
});

test("永久图片的展示URL刷新仍可完成全景导出", async () => {
    const state = setup();
    const release = delayedUpload(state);
    const pending = state.capture();
    state.source.metadata.content = "/source-refreshed.png";
    release();
    await pending;
    assert.equal(state.calls.persist.length, 1);
    assert.equal(state.calls.messages[0].kind, "success");
});

test("全景自身图片优先于上游，未采用的上游更换不阻止导出", async () => {
    const state = setup();
    state.panorama.metadata.content = "/own.png";
    state.panorama.metadata.storageKey = "resource:205";
    const release = delayedUpload(state);
    const pending = state.capture();
    state.source.metadata = { storageKey: "resource:202", content: "/replacement.png" };
    release();
    await pending;
    assert.equal(state.calls.persist.length, 1);
    assert.equal(state.calls.messages[0].kind, "success");
});

for (const change of ["none", "project_changed", "account_epoch"]) {
    test(`全景保存等待期间${change}不提前或错误提示成功`, async () => {
        const state = setup();
        let release;
        const ready = new Promise(resolve => { release = resolve; });
        const persist = state.boundary.persist;
        state.boundary.persist = async (...args) => { await ready; return persist(...args); };
        const pending = state.capture();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(state.calls.persist.length, 1);
        assert.deepEqual(state.calls.messages, []);
        if (change === "project_changed") state.projectIdRef.current = "other-canvas";
        if (change === "account_epoch") state.switchAccount();
        release();
        await pending;
        if (change === "none") assert.equal(state.calls.messages[0].kind, "success");
        else assert.deepEqual(state.calls.messages, []);
        assert.equal(state.imageOperationsRef.current.size, 0);
    });
}

for (const boundary of ["upload", "persist"]) {
    test(`全景${boundary}失败提供错误反馈并保留来源`, async () => {
        const state = setup();
        state.boundary[boundary] = async () => { throw new Error(`${boundary}失败`); };
        await state.capture();
        assert.ok(state.nodesRef.current.some(node => node === state.panorama));
        assert.equal(state.calls.messages.length, 1);
        assert.equal(state.calls.messages[0].kind, "error");
        assert.match(state.calls.messages[0].text, new RegExp(`${boundary}失败`));
        assert.equal(state.imageOperationsRef.current.size, 0);
        if (boundary === "upload") assert.equal(state.nodesRef.current.length, 2);
        else assert.equal(state.nodesRef.current.find(node => node.id === "capture").metadata.storageKey, "resource:301");
    });
}
