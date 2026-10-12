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

// 执行实际结果构建、消费者和素材匹配；仅替换网络、像素I/O及账号/持久化边界。
const javascript = ts.transpileModule([
    definitions("../src/services/canvas-generation-consumer.ts", ["throwIfAborted", "consumeCanvasEmotionGenerationResult"]),
    definitions("../src/services/host-generation-admission.ts", ["isCanvasEditorToolInput", "isCanvasEditorToolTask", "canvasTaskCanUpdateNode"]),
    definitions("../src/services/generation-consumer-dedupe.ts", ["generationEffectApplied"]),
    definitions("../src/lib/canvas/canvas-generation-task-sync.ts", ["generationTaskInput", "generationTaskMode", "reuseGeneratedMediaStorageKey", "workflowMetadataForResultNode", "imageMetadata", "completedTaskMetadata", "applyGeneratedMediaResultMetadata", "buildGenerationTaskNodeResult"]),
    definitions("../src/lib/canvas/canvas-node-size.ts", ["fitNodeSize", "nodeSizeFromRatio"]),
    definitions("../src/lib/canvas/canvas-node-semantics.ts", ["mediaResultMetadata"]),
    definitions("../src/lib/canvas/canvas-node-asset.ts", ["findCanvasNodeAsset", "sameAssetResource", "assetStorageKey"]),
].join("\n"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;

function setup() {
    const scope = { userScope: "101", epoch: 1 };
    let active = scope;
    const source = { id: "source", type: "image", metadata: { storageKey: "resource:102", content: "/changed-source.png" } };
    const emotionEdit = { sourceNodeId: "source", faceBox: { x: 225, y: 50, width: 55, height: 55 }, editRegion: { x: 178, y: 8, width: 142, height: 152 } };
    const node = { id: "target", type: "image", title: "角色2 · 期待", position: { x: 600, y: 100 }, width: 420, height: 236.25, metadata: { taskId: "task", status: "loading", prompt: "冻结提示词", size: "320x180", references: ["resource:101"], emotionEdit } };
    let project = { id: "canvas", nodes: [source, node], connections: [{ id: "edge", fromNodeId: "source", toNodeId: "target" }] };
    const events = [];
    const globals = {
        DOMException,
        CanvasNodeType: { Image: "image", Video: "video", Audio: "audio", Text: "text" },
        NODE_DEFAULT_SIZE: { image: { width: 720, height: 405 } },
        MEDIA_NODE_MIN_SIZE: { width: 420, height: 236 },
        assertUserScope: expected => { if (active !== expected) throw new Error("账号已切换"); },
        attachNodeEffectKey: (task, target, slot) => `attach-node:${task}:${target}:${slot}`,
        parseBackendGenerationResult: task => JSON.parse(task.resultJson),
        ownedResourceIdFromMediaRef: () => "",
        canvasResourceKeysEquivalent: (left, right) => left === right,
        useCanvasStore: { getState: () => ({ projects: [project] }) },
        defaultGenerationResultMediaIO: {
            resolveImageUrl: async (key, fallback) => { events.push({ kind: "source", key }); return key ? `/file/${key}` : fallback; },
            compositeEmotionImage: async (original, generated, region, face) => {
                events.push({ kind: "composite", original, generated, region, face });
                return "data:image/png;base64,derived";
            },
        },
        uploadImage: async (data, progress, expected, canvasId, identity) => {
            globals.assertUserScope(expected);
            events.push({ kind: "upload", data, canvasId, identity });
            return { storageKey: "resource:301", url: "/derived.png", width: 320, height: 180, bytes: 2048, mimeType: "image/png" };
        },
    };
    const context = vm.createContext(globals);
    vm.runInContext(`${javascript}\nthis.consume = consumeCanvasEmotionGenerationResult; this.findAsset = findCanvasNodeAsset;`, context);
    const task = { id: "task", type: "canvas_image", status: "succeeded", outputs: [{ outputIndex: 0, mediaType: "image", materializedAssetId: "provider-asset" }], resultJson: JSON.stringify({ images: [{ storageKey: "resource:201", dataUrl: "/provider.png", width: 256, height: 256 }] }) };
    const input = { canvasId: "canvas", nodeId: "target", task, outputIndex: 0, expectedScope: scope, nodesRef: { current: project.nodes }, isCurrent: () => true, setNodes: nodes => { events.push({ kind: "display" }); input.nodesRef.current = nodes; } };
    const runtime = {
        attachResult: async (task, target, slot, consumer, signal) => { await consumer({ signal }); return "applied"; },
        ensureAsset: async options => { events.push({ kind: "asset", node: options.node }); return { assetId: "derived-asset", confirmed: true }; },
        persistEffect: async effect => { events.push({ kind: "persist", effect }); project = { ...project, nodes: effect.nodes }; return project; },
        syncProject: async () => { events.push({ kind: "sync" }); },
    };
    const replaceTarget = patch => {
        input.nodesRef.current = input.nodesRef.current.map(item => item.id === "target" ? { ...item, ...patch, metadata: { ...item.metadata, ...patch.metadata } } : item);
    };
    return { input, runtime, events, source, context, consume: () => context.consume(input, runtime), replaceTarget, switchAccount: () => { active = { userScope: "202", epoch: 2 }; } };
}

test("实际表情消费者读取冻结原图，合成后以独立资源/素材保存，再确认完成", async () => {
    const environment = setup();
    await environment.consume();
    assert.deepEqual(environment.events.map(event => event.kind), ["source", "composite", "upload", "asset", "persist", "display"]);
    assert.equal(environment.events[0].key, "resource:101");
    assert.equal(environment.events[1].original, "/file/resource:101");
    assert.equal(environment.events[1].generated, "/provider.png");
    assert.equal(environment.events[2].identity, "image:101:emotion:task:0");
    const result = environment.input.nodesRef.current[1];
    assert.equal(result.metadata.storageKey, "resource:301");
    assert.equal(result.metadata.assetId, "derived-asset");
    assert.equal(result.metadata.status, "success");
    assert.equal(result.metadata.naturalWidth, 320);
    assert.equal(result.width, 420);
    assert.equal(result.height, 236.25);
    assert.deepEqual(result.position, { x: 600, y: 100 });
    assert.deepEqual(Array.from(result.metadata.generationEffectKeys), ["attach-node:task:target:0"]);
    assert.equal(environment.input.nodesRef.current[0], environment.source);
});

test("再次恢复已保存的合成图只确认原保存，不再合成或上传", async () => {
    const environment = setup();
    await environment.consume();
    environment.events.length = 0;
    await environment.consume();
    assert.deepEqual(environment.events.map(event => event.kind), ["sync", "display"]);
    assert.equal(environment.input.nodesRef.current[1].metadata.storageKey, "resource:301");
});

test("派生保存失败不回填成功，重试复用同一个图片上传键", async () => {
    const environment = setup();
    const persist = environment.runtime.persistEffect;
    environment.runtime.persistEffect = async () => { throw new Error("controlled save failure"); };
    await assert.rejects(environment.consume(), /controlled save failure/);
    assert.equal(environment.input.nodesRef.current[1].metadata.status, "loading");
    assert.equal(environment.events.some(event => event.kind === "display"), false);
    environment.runtime.persistEffect = persist;
    await environment.consume();
    assert.deepEqual(environment.events.filter(event => event.kind === "upload").map(event => event.identity), ["image:101:emotion:task:0", "image:101:emotion:task:0"]);
});

for (const failure of ["source-deleted", "missing-region", "not-confirmed", "task-replaced", "account-switched"]) {
    test(`表情异常 ${failure} 保留原节点且不持久确认成功`, async () => {
        const environment = setup();
        if (failure === "source-deleted") environment.input.nodesRef.current = environment.input.nodesRef.current.slice(1);
        if (failure === "missing-region") environment.replaceTarget({ metadata: { emotionEdit: { ...environment.input.nodesRef.current[1].metadata.emotionEdit, editRegion: undefined } } });
        if (failure === "not-confirmed") environment.runtime.ensureAsset = async () => ({ confirmed: false });
        if (failure === "task-replaced") environment.replaceTarget({ metadata: { taskId: "new-task" } });
        if (failure === "account-switched") environment.switchAccount();
        await assert.rejects(environment.consume());
        assert.equal(environment.events.some(event => event.kind === "persist" || event.kind === "display"), false);
        assert.equal(environment.input.nodesRef.current.find(node => node.id === "target").metadata.status, "loading");
    });
}

test("素材保存期间切换账号不能写入或回填旧表情结果", async () => {
    const environment = setup();
    environment.runtime.ensureAsset = async () => { environment.switchAccount(); return { confirmed: true, assetId: "old-account-asset" }; };
    await assert.rejects(environment.consume(), /账号已切换/);
    assert.equal(environment.events.some(event => event.kind === "persist" || event.kind === "display"), false);
});

test("素材保存期间修改提示词保留新输入，不能回填旧表情结果", async () => {
    const environment = setup();
    environment.runtime.ensureAsset = async () => { environment.replaceTarget({ metadata: { prompt: "新输入" } }); return { confirmed: true, assetId: "derived-asset" }; };
    await assert.rejects(environment.consume(), /参数已变化/);
    assert.equal(environment.input.nodesRef.current[1].metadata.prompt, "新输入");
    assert.equal(environment.events.some(event => event.kind === "persist" || event.kind === "display"), false);
});

test("同任务原图不能冒用为表情合成图的素材身份", () => {
    const environment = setup();
    const node = { id: "target", type: "image", metadata: { taskId: "task", storageKey: "resource:301" } };
    const provider = { id: "provider-asset", kind: "image", metadata: { taskId: "task", nodeId: "target", canvasId: "canvas" }, data: { storageKey: "resource:201" } };
    const derived = { ...provider, id: "derived-asset", data: { storageKey: "resource:301" } };
    assert.equal(environment.context.findAsset([provider], node, "canvas", "task"), undefined);
    assert.equal(environment.context.findAsset([provider, derived], node, "canvas", "task"), derived);
});
