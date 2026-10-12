import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";

// 运行实际输出Hook、场景合并与节点stamp；只替换React和I/O，不代表供应商验收。
const stubs = {
    react: "export const useCallback = value => value; export const useEffect = () => {}; export const useRef = current => ({ current });",
    antd: "export const App = { useApp: () => ({ message: {} }) };",
    "@/lib/canvas/canvas-generation-task-sync": "export const imageMetadata = image => ({ storageKey: image.storageKey, content: image.url, naturalWidth: image.width, naturalHeight: image.height });",
    "@/lib/canvas/director/director-clay-output": "export const directorClayVideoMetadata = () => { throw new Error('本专项不创建视频'); };",
    "@/lib/canvas/canvas-project-domain": "export const createCanvasNode = () => { throw new Error('本专项使用已有导演节点'); };",
    "@/lib/canvas/director/director-templates": "export const createDirectorSceneFromTemplate = () => { throw new Error('本专项使用已有场景'); };",
    "@/services/image-storage": "export const uploadImage = (...args) => globalThis.directorBoundary.upload(...args);",
    "@/services/file-storage": "export const uploadMediaFile = () => { throw new Error('本专项不上传视频'); };",
    "@/services/project-asset-sync": "export const ensureCanvasNodeAsset = (...args) => globalThis.directorBoundary.ensure(...args);",
    "@/lib/user-scope-guard": `
        export class UserScopeAbandonedError extends Error { constructor() { super('账号已切换'); } }
        export const captureUserScope = () => globalThis.directorBoundary.scope;
        export const userScopeMatches = expected => expected === globalThis.directorBoundary.scope;
    `,
    "@/stores/canvas/use-canvas-store": "export const useCanvasStore = { getState: () => globalThis.directorBoundary.store };",
};
const bundled = await build({
    stdin: { contents: 'export { useCanvasDirector } from "@/pages/canvas/use-canvas-director"; export { stampCanvasNodeChanges } from "@/lib/canvas/canvas-node-timestamps";', resolveDir: fileURLToPath(new URL("..", import.meta.url)), loader: "ts" },
    bundle: true, write: false, format: "esm", platform: "node",
    alias: { "@": fileURLToPath(new URL("../src", import.meta.url)) },
    plugins: [{ name: "director-io", setup(builder) {
        builder.onResolve({ filter: /.*/ }, args => stubs[args.path] ? { path: args.path, namespace: "director-io" } : undefined);
        builder.onLoad({ filter: /.*/, namespace: "director-io" }, args => ({ contents: stubs[args.path], loader: "js" }));
    } }],
});
const { useCanvasDirector, stampCanvasNodeChanges } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);

function setup() {
    const before = "2026-10-01T00:00:00.000Z";
    const after = "2026-10-09T00:00:00.000Z";
    const scene = { id: "scene-one", title: "原场景", updatedAt: before, shots: [{ id: "shot-one", prompt: "原意图" }], objects: [] };
    const source = { id: "director-one", type: "director", title: "导演台 1", position: { x: 0, y: 0 }, width: 640, height: 640, createdAt: before, updatedAt: before, metadata: { workflowKind: "shot", directorSceneId: scene.id, directorShotId: "shot-one" } };
    const nodesRef = { current: [source] };
    const connectionsRef = { current: [] };
    const project = { id: "canvas-one", directorScenes: [scene] };
    const scope = { userScope: "9007199254740999", epoch: 1 };
    const calls = { upload: 0, ensure: 0, nodes: 0, scene: 0 };
    const boundary = globalThis.directorBoundary = {
        scope, store: { projects: [project] },
        upload: async (_blob, _progress, expected) => {
            assert.equal(expected, scope);
            calls.upload++;
            return { storageKey: "resource:9007199254740998", url: "/api/v1/canvas-runtime/resources/9007199254740998/file", width: 320, height: 180 };
        },
        ensure: async input => {
            assert.equal(input.expectedScope, scope);
            calls.ensure++;
            return { assetId: "asset-one", confirmed: true };
        },
    };
    const hook = useCanvasDirector({
        projectId: project.id, domainProjectId: "9007199254740900", directorNodeId: source.id,
        directorScenes: [scene], nodesRef, connectionsRef, getCanvasCenter: () => ({ x: 0, y: 0 }),
        setNodes: value => { calls.nodes++; nodesRef.current = stampCanvasNodeChanges(nodesRef.current, typeof value === "function" ? value(nodesRef.current) : value, after); },
        setConnections: value => { connectionsRef.current = typeof value === "function" ? value(connectionsRef.current) : value; },
        setSelectedNodeIds: () => {}, setSelectedConnectionId: () => {}, setDirectorNodeId: () => {},
        updateProject: (id, patch) => { assert.equal(id, project.id); calls.scene++; Object.assign(project, patch); },
    });
    const output = { scene, shot: scene.shots[0], prompt: "输出意图", beauty: new Blob(["png"], { type: "image/png" }) };
    return { hook, output, boundary, calls, nodesRef, connectionsRef, project, source, before, after };
}

test("构图回填推进来源与新图片节点stamp，永久资源、素材和单向连线一并保存", async () => {
    const state = setup();
    assert.deepEqual(await state.hook.applyDirectorOutput(state.output), { confirmed: true });
    assert.equal(state.nodesRef.current.length, 2);
    const source = state.nodesRef.current.find(node => node.id === state.source.id);
    const preview = state.nodesRef.current.find(node => node.id === source.metadata.directorPreviewNodeId);
    assert.equal(source.updatedAt, state.after);
    assert.equal(preview.updatedAt, state.after);
    assert.equal(preview.metadata.storageKey, "resource:9007199254740998");
    assert.equal(preview.metadata.assetId, "asset-one");
    assert.equal(state.connectionsRef.current.length, 1);
    assert.equal(state.connectionsRef.current[0].fromNodeId, source.id);
    assert.equal(state.connectionsRef.current[0].toNodeId, preview.id);
    assert.equal(state.project.directorScenes[0].shots[0].previewNodeId, preview.id);
});

test("上传失败保留原节点、场景和连线", async () => {
    const state = setup();
    state.boundary.upload = async () => { throw new Error("永久归档失败"); };
    await assert.rejects(state.hook.applyDirectorOutput(state.output), /永久归档失败/);
    assert.deepEqual(state.calls, { upload: 0, ensure: 0, nodes: 0, scene: 0 });
    assert.equal(state.nodesRef.current[0], state.source);
    assert.deepEqual(state.connectionsRef.current, []);
});

test("同账号新epoch阻止旧上传响应登记素材与回填", async () => {
    const state = setup();
    const upload = state.boundary.upload;
    state.boundary.upload = async (...args) => {
        const result = await upload(...args);
        state.boundary.scope = { ...state.boundary.scope, epoch: 2 };
        return result;
    };
    await assert.rejects(state.hook.applyDirectorOutput(state.output), /账号已切换/);
    assert.equal(state.calls.ensure, 0);
    assert.equal(state.calls.nodes, 0);
});

test("素材登记等待期间删除镜头，不写回旧输出", async () => {
    const state = setup();
    state.boundary.ensure = async () => {
        state.project.directorScenes = [{ ...state.output.scene, shots: [] }];
        return { assetId: "asset-one", confirmed: true };
    };
    await assert.rejects(state.hook.applyDirectorOutput(state.output), /镜头已切换、删除/);
    assert.equal(state.calls.nodes, 0);
    assert.equal(state.project.directorScenes[0].shots.length, 0);
});

test("上传与素材登记期间的新场景和图编辑合并保留", async () => {
    const state = setup();
    state.boundary.ensure = async () => {
        state.project.directorScenes = [{ ...state.output.scene, title: "并发新标题", objects: [{ id: "new-object" }] }];
        state.nodesRef.current = [{ ...state.source, title: "新节点标题" }, { id: "other", type: "text", title: "独立编辑", position: { x: 1, y: 2 }, width: 200, height: 100, metadata: {} }];
        return { assetId: "asset-one", confirmed: true };
    };
    await state.hook.applyDirectorOutput(state.output);
    assert.equal(state.project.directorScenes[0].title, "并发新标题");
    assert.equal(state.project.directorScenes[0].objects[0].id, "new-object");
    assert.equal(state.nodesRef.current.find(node => node.id === state.source.id).title, "新节点标题");
    assert.equal(state.nodesRef.current.find(node => node.id === "other").title, "独立编辑");
});
