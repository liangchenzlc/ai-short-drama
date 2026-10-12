import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

function parse(file) {
    const source = readFileSync(new URL(file, import.meta.url), "utf8");
    return ts.createSourceFile(file, source, ts.ScriptTarget.ES2022, true);
}

function definitions(file, names) {
    const parsed = parse(file);
    return parsed.statements.filter(statement => names.includes(statement.name?.text))
        .map(statement => statement.getText(parsed).replace(/^export /, "")).join("\n");
}

const lifecycle = parse("../src/pages/canvas/use-canvas-project-lifecycle.ts");
let persist;
function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(lifecycle) === "persistLocalEdits") persist = node.initializer.arguments[0].getText(lifecycle);
    ts.forEachChild(node, visit);
}
visit(lifecycle);
assert.ok(persist, "必须执行生产保存恢复回调");
const javascript = ts.transpileModule([
    definitions("../src/services/file-storage.ts", ["getMediaBlob", "retryPendingMediaUpload"]),
    definitions("../src/lib/canvas/canvas-generation-task-sync.ts", ["audioMetadata", "videoMetadata"]),
    `this.retry = retryPendingMediaUpload; this.persist = ${persist};`,
].join("\n"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;

function deferred() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

function setup(kind = "video") {
    const expected = { userScope: "101", epoch: 1 };
    let active = expected;
    const key = `${kind}:101:original`;
    const file = new File(["original bytes"], kind === "video" ? "原视频.mp4" : "原音频.wav", { type: kind === "video" ? "video/mp4" : "audio/wav" });
    const node = { id: "node", type: kind, title: "原节点", position: { x: 0, y: 0 }, metadata: { storageKey: key, content: "blob:original", durationMs: 2000, bytes: file.size, mimeType: file.type, assetId: "asset", ...(kind === "video" ? { naturalWidth: 160, naturalHeight: 90, hasAudio: true, videoPreview: { content: "/poster", storageKey: "resource:900", width: 160, height: 90 } } : {}) } };
    const nodesRef = { current: [node] };
    const assets = [{ id: "asset", kind, coverUrl: kind === "video" ? "/poster" : "", metadata: { resourceKey: key }, data: { url: "blob:original", storageKey: key, durationMs: 2000, bytes: file.size, mimeType: file.type, ...(kind === "video" ? { width: 160, height: 90, hasAudio: true } : {}) } }];
    const events = [];
    const editorProjectIdRef = { current: "canvas" };
    const imageRecoveryControllers = { current: new Set() };
    const remote = { id: "1001", publicUrl: "/durable", size: file.size, mimeType: file.type, durationMs: 2000, ...(kind === "video" ? { width: 160, height: 90 } : {}) };
    const boundaries = {
        read: async () => file,
        upload: async () => remote,
        bind: async () => ({ confirmed: true, assetId: "asset" }),
    };
    const context = vm.createContext({
        Error, DOMException, Map, AbortController, File,
        projectId: "canvas", projectLoaded: true, localMode: true, editorProjectIdRef,
        imageRecoveryControllers, nodesRef, connectionsRef: { current: [] },
        chatSessions: [], activeChatId: null, canvasAppearance: { mode: "dark" }, backgroundMode: "dots", showImageInfo: false,
        observedContentRef: { current: null }, viewportRef: { current: { x: 0, y: 0, k: 1 } },
        CanvasNodeType: { Image: "image", Video: "video", Audio: "audio" },
        captureUserScope: () => expected,
        assertUserScope: scope => { if (scope.userScope !== active.userScope || scope.epoch !== active.epoch) throw new Error("账号已切换"); },
        resourceIdFromStorageKey: key => key?.startsWith("resource:") ? key.slice(9) : "",
        getCachedResourceBlob: () => { throw new Error("不应读取其他永久资源"); },
        getLocalMediaBlob: async key => { events.push({ kind: "read", key }); return boundaries.read(key); },
        uploadResourceFile: async (blob, kind, meta) => { events.push({ kind: "upload", blob, mediaKind: kind, meta }); return boundaries.upload(blob, kind, meta); },
        resourceStorageKey: id => `resource:${id}`,
        resourceFileUrl: id => `/resources/${id}/file`,
        retryPendingImageUpload: () => { throw new Error("本片音视频不应重传图片"); },
        imageMetadata: () => { throw new Error("本片音视频不应覆盖图片"); },
        useAssetStore: { getState: () => ({ assets, updateAsset: (id, patch) => { events.push({ kind: "asset", id }); const index = assets.findIndex(asset => asset.id === id); assets[index] = { ...assets[index], ...patch }; } }) },
        ensureCanvasNodeAsset: async options => { events.push({ kind: "bind", node: options.node }); return boundaries.bind(options); },
        setNodes: nodes => { events.push({ kind: "display" }); nodesRef.current = nodes; },
        updateProject: (id, patch) => events.push({ kind: "project", id, patch }),
        flushCanvasStorePersistence: async () => events.push({ kind: "flush" }),
        syncLocalCanvasProjectToBackend: async id => events.push({ kind: "sync", id }),
        saveHostCanvasViewport: async () => events.push({ kind: "viewport" }),
        saveHostCanvasViewPreferences: async () => events.push({ kind: "appearance" }),
        traceCanvasGraph: () => { throw new Error("测试不应走无关图追踪"); },
        useCanvasStore: { getState: () => ({ openProject: () => ({ nodes: nodesRef.current }) }) },
    });
    vm.runInContext(javascript, context);
    return { expected, key, file, node, nodesRef, assets, remote, events, boundaries, editorProjectIdRef, imageRecoveryControllers,
        retry: (keyOverride = key, meta = {}) => context.retry(keyOverride, expected, "canvas", meta),
        save: options => context.persist(options),
        switchAccount: () => { active = { userScope: "202", epoch: 2 }; },
    };
}

for (const kind of ["audio", "video"]) {
    test(`${kind}重传使用原File、文件名、声明、本人范围与原请求键`, async () => {
        const state = setup(kind);
        const meta = kind === "video" ? { width: 160, height: 90, durationMs: 2000 } : { durationMs: 2000 };
        const result = await state.retry(state.key, meta);
        const upload = state.events.find(event => event.kind === "upload");
        assert.equal(upload.blob, state.file);
        assert.equal(upload.mediaKind, kind);
        assert.equal(upload.meta.fileName, state.file.name);
        assert.equal(upload.meta.idempotencyKey, state.key);
        assert.equal(upload.meta.expectedScope, state.expected);
        assert.equal(upload.meta.hostCanvasKey, "canvas");
        assert.equal(upload.meta.durationMs, 2000);
        assert.equal(result.storageKey, "resource:1001");
        assert.equal(state.events.filter(event => event.kind === "upload").length, 1);
    });
}

test("外人、本地文件及永久键在读取与上传前拒绝", async () => {
    const state = setup();
    for (const key of ["video:202:other", "audio:202:other", "file:101:other", "resource:1001"]) await assert.rejects(state.retry(key), /不属于本人/);
    assert.equal(state.events.length, 0);
});

test("原字节缺失或读取期间切换账号时不提交新请求", async () => {
    const missing = setup();
    missing.boundaries.read = async () => null;
    await assert.rejects(missing.retry(), /本地原文件不可用/);
    assert.equal(missing.events.filter(event => event.kind === "upload").length, 0);
    const switched = setup();
    switched.boundaries.read = async () => { switched.switchAccount(); return switched.file; };
    await assert.rejects(switched.retry(), /账号已切换/);
    assert.equal(switched.events.filter(event => event.kind === "upload").length, 0);
});

test("回执丢失后显式重试仍使用原字节和原键", async () => {
    const state = setup("audio");
    state.boundaries.upload = async () => { throw new Error("回执丢失"); };
    await assert.rejects(state.retry(), /回执丢失/);
    state.boundaries.upload = async () => state.remote;
    await state.retry();
    const uploads = state.events.filter(event => event.kind === "upload");
    assert.equal(uploads.length, 2);
    assert.ok(uploads.every(event => event.blob === state.file && event.meta.idempotencyKey === state.key));
});

test("两个节点引用同一离线视频时只重传一次，保持封面音轨与素材ID", async () => {
    const state = setup();
    state.nodesRef.current.push({ ...state.node, id: "second", metadata: { ...state.node.metadata } });
    await state.save();
    assert.equal(state.events.filter(event => event.kind === "upload").length, 1);
    assert.equal(state.events.filter(event => event.kind === "bind").length, 2);
    for (const node of state.nodesRef.current) {
        assert.equal(node.metadata.storageKey, "resource:1001");
        assert.equal(node.metadata.assetId, "asset");
        assert.equal(node.metadata.hasAudio, true);
        assert.deepEqual(node.metadata.videoPreview, state.node.metadata.videoPreview);
    }
    assert.equal(state.assets[0].id, "asset");
    assert.equal(state.assets[0].data.storageKey, "resource:1001");
    assert.equal(state.assets[0].coverUrl, "/poster");
    assert.equal(state.assets[0].data.hasAudio, true);
    assert.ok(state.events.findIndex(event => event.kind === "flush") < state.events.findIndex(event => event.kind === "sync"));
});

test("素材绑定未确认时保留节点草稿且不写云图，后续恢复复用原键", async () => {
    const state = setup("audio");
    state.boundaries.bind = async () => ({ confirmed: false, assetId: "asset" });
    await assert.rejects(state.save(), /素材绑定尚未确认/);
    assert.equal(state.nodesRef.current[0].metadata.storageKey, state.key);
    assert.ok(!state.events.some(event => event.kind === "display" || event.kind === "sync"));
    state.boundaries.bind = async () => ({ confirmed: true, assetId: "asset" });
    await state.save();
    assert.equal(state.nodesRef.current[0].metadata.storageKey, "resource:1001");
    assert.ok(state.events.filter(event => event.kind === "upload").every(event => event.meta.idempotencyKey === state.key));
});

test("等待重传时的新标题、提示词、封面和音轨变更不会被旧结果覆盖", async () => {
    const state = setup();
    state.boundaries.upload = async () => {
        state.nodesRef.current = [{ ...state.node, title: "新标题", metadata: { ...state.node.metadata, prompt: "新提示词", hasAudio: false, videoPreview: { content: "/new-poster", storageKey: "resource:901" } } }];
        return state.remote;
    };
    await state.save();
    const current = state.nodesRef.current[0];
    assert.equal(current.title, "新标题");
    assert.equal(current.metadata.prompt, "新提示词");
    assert.equal(current.metadata.hasAudio, false);
    assert.equal(current.metadata.videoPreview.storageKey, "resource:901");
});

for (const change of ["deleted", "replaced", "type-changed"]) {
    test(`重传期间节点${change}时不绑定或回填旧媒体`, async () => {
        const state = setup();
        state.boundaries.upload = async () => {
            state.nodesRef.current = change === "deleted" ? [] : [{ ...state.node, ...(change === "type-changed" ? { type: "audio" } : {}), metadata: { ...state.node.metadata, ...(change === "replaced" ? { storageKey: "resource:new" } : {}) } }];
            return state.remote;
        };
        await state.save();
        assert.ok(!state.events.some(event => event.kind === "bind" || event.kind === "display"));
        assert.ok(!state.nodesRef.current.some(node => node.metadata.storageKey === "resource:1001"));
    });
}

test("素材绑定期间节点资源改变时不覆盖新资源", async () => {
    const state = setup("audio");
    state.boundaries.bind = async () => {
        state.nodesRef.current = [{ ...state.node, metadata: { ...state.node.metadata, storageKey: "resource:new" } }];
        return { confirmed: true, assetId: "asset" };
    };
    await state.save();
    assert.equal(state.nodesRef.current[0].metadata.storageKey, "resource:new");
});

for (const change of ["navigation", "account", "abort"]) {
    test(`重传回执到达前${change}时停止旧页面的回填与图保存`, async () => {
        const state = setup();
        const gate = deferred();
        state.boundaries.upload = () => gate.promise;
        const saving = state.save();
        await new Promise(resolve => setImmediate(resolve));
        if (change === "navigation") state.editorProjectIdRef.current = "other-canvas";
        if (change === "account") state.switchAccount();
        if (change === "abort") for (const controller of state.imageRecoveryControllers.current) controller.abort();
        gate.resolve(state.remote);
        await assert.rejects(saving, /Aborted|账号已切换/);
        assert.ok(!state.events.some(event => ["bind", "asset", "display", "project", "sync"].includes(event.kind)));
        assert.equal(state.imageRecoveryControllers.current.size, 0);
    });
}

test("仅本地落盘不触发音视频重传或远端保存", async () => {
    const state = setup();
    await state.save({ requireRemote: false });
    assert.ok(state.events.some(event => event.kind === "flush"));
    assert.ok(!state.events.some(event => ["read", "upload", "bind", "sync", "viewport", "appearance"].includes(event.kind)));
});
