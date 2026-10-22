import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

function definitions(file, names) {
    const source = readFileSync(new URL(file, import.meta.url), "utf8");
    const parsed = ts.createSourceFile(file, source, ts.ScriptTarget.ES2022, true);
    return parsed.statements.filter(statement => names.includes(statement.name?.text))
        .map(statement => statement.getText(parsed).replace(/^export /, "")).join("\n");
}

// 执行实际媒体保存与缺失绑定判断；仅替换素材 I/O、账号和项目存储边界。
const javascript = ts.transpileModule([
    definitions("../../src/lib/canvas/canvas-media-persist.ts", ["CanvasMediaPersistUnconfirmedError", "throwIfAborted", "isPersistStop", "persistOwnedCanvasMediaNodes"]),
    definitions("../../src/lib/canvas/canvas-node-asset.ts", ["canvasNodesMissingResourceAssetBinding"]),
].join("\n"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;

function deferred() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

function setup({ pendingSource = false, pendingRender = false, unopened = false } = {}) {
    const scope = { userScope: "101", epoch: 1 };
    let activeScope = scope;
    let canvasId = "canvas";
    const source = { id: "source", type: "video", title: "原视频", position: { x: 0, y: 0 }, metadata: { storageKey: "resource:101", content: "/source.mp4", ...(pendingSource ? {} : { assetId: "source-asset" }) } };
    const derived = { id: "derived", type: "video", title: "剪辑结果", position: { x: 600, y: 0 }, metadata: { storageKey: "resource:201", content: "/derived.mp4", prompt: "旧提示词", videoPreview: { storageKey: "resource:202" } } };
    let nodes = pendingRender ? [source] : [source, derived];
    let connections = [{ id: "edge", fromNodeId: "source", toNodeId: "derived" }];
    let stored = unopened ? undefined : { nodes, connections };
    let saved;
    const events = [];
    class Abandoned extends Error {}
    const context = vm.createContext({
        Error, DOMException,
        CanvasNodeType: { Image: "image", Video: "video", Audio: "audio" },
        captureUserScope: () => activeScope,
        assertUserScope: expected => { if (expected !== activeScope) throw new Abandoned("账号已切换"); },
        isUserScopeAbandonedError: error => error instanceof Abandoned,
        canvasResourceKeysEquivalent: (left, right) => left === right || new Set([left, right]).size === 2 && [left, right].every(key => ["resource:201", "resource:derived-alias"].includes(key)),
    });
    vm.runInContext(`${javascript}\nthis.persist = persistOwnedCanvasMediaNodes;`, context);
    const input = { canvasId, mediaNodes: [derived], mediaConnections: connections, expectedScope: scope };
    const deps = {
        ensureCanvasNodeAsset: async ({ node }) => { events.push({ kind: "asset", id: node.id }); return { confirmed: true, assetId: `${node.id}-asset` }; },
        getStoredProject: () => stored,
        hasProject: () => Boolean(stored),
        openProject: async () => { stored = { nodes: [source], connections: [] }; },
        updateProject: (id, snapshot) => { events.push({ kind: "update", id }); stored = snapshot; },
        flushPersistence: async () => { events.push({ kind: "flush" }); },
        syncSnapshot: async (id, snapshot) => { events.push({ kind: "sync", id }); saved = snapshot; },
        readSavedProject: async () => saved,
        setNodes: updater => { events.push({ kind: "display" }); nodes = updater(nodes); },
        getLiveProjectId: () => canvasId,
        getLiveNodes: () => nodes,
        getLiveConnections: () => connections,
        isLocalWorkspace: () => true,
        warn: message => { events.push({ kind: "warn", message }); },
        error: message => { events.push({ kind: "error", message }); },
    };
    const patch = (id, fields) => { nodes = nodes.map(node => node.id === id ? { ...node, ...fields, metadata: { ...node.metadata, ...fields.metadata } } : node); };
    return {
        input, deps, source, derived, events, patch,
        persist: () => context.persist(input, deps),
        nodes: () => nodes, stored: () => stored, saved: () => saved,
        deleteNode: id => { nodes = nodes.filter(node => node.id !== id); connections = connections.filter(edge => edge.fromNodeId !== id && edge.toNodeId !== id); },
        switchAccount: () => { activeScope = { userScope: "202", epoch: 2 }; },
        switchProject: () => { canvasId = "other-canvas"; },
    };
}

test("正常媒体保存保留来源、连线并回读确认派生节点素材", async () => {
    const environment = setup();
    const assets = await environment.persist();
    assert.equal(assets.get("derived"), "derived-asset");
    assert.equal(environment.saved().nodes.find(node => node.id === "source"), environment.source);
    assert.equal(environment.saved().nodes.find(node => node.id === "derived").metadata.assetId, "derived-asset");
    assert.equal(environment.saved().connections[0].id, "edge");
});

for (const pendingSource of [false, true]) {
    test(`素材响应延迟期间保留新预览和编辑输入（来源需绑定=${pendingSource}）`, async () => {
        const environment = setup({ pendingSource });
        const entered = deferred();
        const released = deferred();
        const ensure = environment.deps.ensureCanvasNodeAsset;
        environment.deps.ensureCanvasNodeAsset = async options => {
            if (options.node.id === (pendingSource ? "source" : "derived")) {
                entered.resolve();
                await released.promise;
            }
            return ensure(options);
        };
        const saving = environment.persist();
        await entered.promise;
        const preview = { storageKey: "resource:203", sourceKey: "resource:201", captureVersion: 2 };
        environment.patch("derived", { title: "新标题", position: { x: 700, y: 50 }, metadata: { prompt: "新提示词", videoPreview: preview } });
        released.resolve();
        await saving;
        const saved = environment.saved().nodes.find(node => node.id === "derived");
        assert.equal(saved.metadata.videoPreview, preview);
        assert.equal(saved.title, "新标题");
        assert.equal(saved.position.x, 700);
        assert.equal(saved.metadata.prompt, "新提示词");
        assert.equal(saved.metadata.assetId, "derived-asset");
    });
}

test("本地 flush 期间完成的新预览不会被后续远端快照覆盖", async () => {
    const environment = setup();
    const preview = { storageKey: "resource:203", sourceKey: "resource:201", captureVersion: 2 };
    environment.deps.flushPersistence = async () => { environment.patch("derived", { metadata: { videoPreview: preview } }); };
    await environment.persist();
    assert.equal(environment.saved().nodes.find(node => node.id === "derived").metadata.videoPreview, preview);
});

test("打开项目期间的新预览写入最终快照", async () => {
    const environment = setup({ unopened: true });
    const open = environment.deps.openProject;
    const preview = { storageKey: "resource:203", sourceKey: "resource:201", captureVersion: 2 };
    environment.deps.openProject = async () => { await open(); environment.patch("derived", { metadata: { videoPreview: preview } }); };
    await environment.persist();
    assert.equal(environment.saved().nodes.find(node => node.id === "derived").metadata.videoPreview, preview);
});

for (const change of ["resource", "type", "deleted", "account", "project", "aborted"]) {
    test(`素材 I/O 期间 ${change} 停止旧媒体写入`, async () => {
        const environment = setup();
        const ensure = environment.deps.ensureCanvasNodeAsset;
        const controller = new AbortController();
        environment.input.signal = controller.signal;
        environment.deps.ensureCanvasNodeAsset = async options => {
            if (change === "resource") environment.patch("derived", { metadata: { storageKey: "resource:301", content: "/new.mp4" } });
            if (change === "type") environment.patch("derived", { type: "image" });
            if (change === "deleted") environment.deleteNode("derived");
            if (change === "account") environment.switchAccount();
            if (change === "project") environment.switchProject();
            if (change === "aborted") controller.abort();
            return ensure(options);
        };
        await assert.rejects(environment.persist());
        assert.equal(environment.events.some(event => ["update", "sync", "display"].includes(event.kind)), false);
        assert.equal(environment.nodes().find(node => node.id === "derived")?.metadata.assetId, undefined);
    });
}

test("来源素材绑定期间被删除时不复活旧来源或继续写派生快照", async () => {
    const environment = setup({ pendingSource: true });
    const ensure = environment.deps.ensureCanvasNodeAsset;
    environment.deps.ensureCanvasNodeAsset = async options => { if (options.node.id === "source") environment.deleteNode("source"); return ensure(options); };
    await assert.rejects(environment.persist());
    assert.equal(environment.events.some(event => ["update", "sync", "display"].includes(event.kind)), false);
    assert.equal(environment.nodes().some(node => node.id === "source"), false);
});

test("尚未进入 React live 节点的新结果仍可由输入补入持久快照", async () => {
    const environment = setup({ pendingRender: true });
    await environment.persist();
    assert.equal(environment.saved().nodes.find(node => node.id === "derived").metadata.assetId, "derived-asset");
});

test("同一永久资源的展示 URL 或已登记别名变化允许保存", async () => {
    const environment = setup();
    const ensure = environment.deps.ensureCanvasNodeAsset;
    environment.deps.ensureCanvasNodeAsset = async options => { environment.patch("derived", { metadata: { content: "/refreshed.mp4", storageKey: "resource:derived-alias" } }); return ensure(options); };
    await environment.persist();
    assert.equal(environment.saved().nodes.find(node => node.id === "derived").metadata.content, "/refreshed.mp4");
    assert.equal(environment.saved().nodes.find(node => node.id === "derived").metadata.storageKey, "resource:derived-alias");
});

test("未确认素材不写入成功快照", async () => {
    const environment = setup();
    environment.deps.ensureCanvasNodeAsset = async () => ({ confirmed: false });
    await assert.rejects(environment.persist(), error => error.name === "CanvasMediaPersistUnconfirmedError");
    assert.equal(environment.events.some(event => ["update", "sync", "display"].includes(event.kind)), false);
});

test("快照同步失败向调用方传播并保留 live 媒体", async () => {
    const environment = setup();
    environment.deps.syncSnapshot = async () => { throw new Error("controlled snapshot failure"); };
    await assert.rejects(environment.persist(), /controlled snapshot failure/);
    assert.equal(environment.nodes().find(node => node.id === "derived").metadata.storageKey, "resource:201");
    assert.equal(environment.events.filter(event => event.kind === "error").length, 1);
});
