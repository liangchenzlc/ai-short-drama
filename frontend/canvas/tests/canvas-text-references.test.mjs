import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

// 执行实际引用算法；只替换存储、HTTP和生成边界，不代表浏览器或供应商验收。
const stubs = {
    "@/services/file-storage": "export const getMediaBlob = async () => null; export const resolveMediaUrl = async () => '';",
    "@/services/image-storage": "export const getImageBlob = async () => null; export const resolveImageUrl = async () => ''; export const uploadImage = async () => { throw new Error('unexpected upload'); }; export const imageToDataUrl = uploadImage;",
    "@/lib/canvas/canvas-generation-task-sync": "export const imageMetadata = () => { throw new Error('unexpected sync'); };",
    "@/lib/canvas/canvas-file-upload": "export const interruptFileUpload = () => { throw new Error('unexpected upload'); };",
    "@/services/api/task-center": "export const createGenerationTask = async () => { throw new Error('unexpected generation'); }; export const waitForGenerationTask = createGenerationTask;",
    "@/services/api/image": "export const buildBackendToolRequests = () => [];",
    "@/services/api/request": "export class ApiError extends Error {} export const http = new Proxy({}, { get: () => () => { throw new Error('unexpected HTTP'); } });",
    "@/stores/use-user-store": "export const useUserStore = Object.assign(() => undefined, { getState: () => ({ user: null }) });",
    "@/lib/canvas/canvas-drawing-storage": `
        export let boundary;
        export function setDrawingBoundary(value) { boundary = { loads: [], uploads: [], publications: [], confirmed: true, ...value }; }
        export async function loadCanvasDrawingRender(...args) { boundary.loads.push(args); return boundary.render; }
        export async function saveCanvasDrawingRenderPublication(...args) { boundary.publications.push(args); return boundary.confirmed; }
    `,
    "@/services/api/resources": `
        import { boundary } from "@/lib/canvas/canvas-drawing-storage";
        export const resourceIdFromStorageKey = key => /^resource:([1-9]\\d*)$/.exec(key || '')?.[1];
        export const resourceStorageKey = id => 'resource:' + id;
        export const ownedResourceIdFromMediaRef = media => resourceIdFromStorageKey(media.storageKey);
        export const resourceFileUrl = id => '/api/v1/canvas-runtime/resources/' + id + '/file';
        export const getResource = async () => { throw new Error('unexpected lookup'); };
        export async function uploadResourceFile(...args) {
            boundary.uploads.push(args);
            if (boundary.uploadError) throw boundary.uploadError;
            return boundary.resource;
        }
    `,
};
const bundled = await build({
    stdin: {
        contents: 'export { buildNodeGenerationContext } from "./src/components/canvas/canvas-node-generation.ts"; export { getGenerationCount } from "./src/lib/canvas/canvas-project-generation.ts"; export { resolveCanvasDrawingReference } from "./src/lib/canvas/canvas-drawing-reference.ts"; export { setDrawingBoundary, boundary } from "@/lib/canvas/canvas-drawing-storage";',
        resolveDir: fileURLToPath(new URL("..", import.meta.url)),
    },
    tsconfig: fileURLToPath(new URL("../tsconfig.json", import.meta.url)),
    bundle: true, write: false, format: "esm", platform: "node", target: "node22",
    plugins: [{ name: "text-reference-boundaries", setup(builder) {
        builder.onResolve({ filter: /^@\// }, args => args.path in stubs ? { path: args.path, namespace: "controlled" } : undefined);
        builder.onLoad({ filter: /.*/, namespace: "controlled" }, args => ({ contents: stubs[args.path], loader: "js" }));
    } }],
});
const source = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);

function node(id, type, metadata = {}) {
    return { id, type, title: id, position: { x: 0, y: 0 }, width: 320, height: 200, metadata };
}
const red = node("red", "image", { content: "/red.png", storageKey: "resource:101" });
const blue = node("blue", "image", { content: "/blue.png", storageKey: "resource:102" });
const target = node("text", "text");
const edges = [red, blue].map(item => ({ id: item.id + "-text", fromNodeId: item.id, toNodeId: target.id }));

test("普通文字自动引用保持连线顺序，显式反序和重复引用按首次出现去重及重新编号", () => {
    const nodes = [red, blue, target];
    const automatic = source.buildNodeGenerationContext(target.id, nodes, edges, "描述两张图", []);
    assert.deepEqual(automatic.referenceImages.map(image => image.storageKey), ["resource:101", "resource:102"]);
    const explicit = source.buildNodeGenerationContext(target.id, nodes, edges, "先 @图片2，再 @图片1，复核 @图片2。", []);
    assert.deepEqual(explicit.referenceImages.map(image => image.storageKey), ["resource:102", "resource:101"]);
    assert.equal(explicit.prompt, "先 @图片1，再 @图片2，复核 @图片1。");
    assert.equal(explicit.imageCount, 2);
});

test("缺失引用拒绝生成，单独显式引用不会自动补入另一条连线图片", () => {
    assert.throws(() => source.buildNodeGenerationContext(target.id, [red, blue, target], edges, "参考 @图片3。", []), /@图片3 没有对应的画布资源/);
    const selected = source.buildNodeGenerationContext(target.id, [red, blue, target], edges, "参考 @图片2。", []);
    assert.deepEqual(selected.referenceImages.map(image => image.id), [blue.id]);
    assert.equal(selected.prompt, "参考 @图片1。");
});

test("绘图作为图片引用保留子文档、精确版本及笔画数量，提示词保留绘图标签", () => {
    const drawing = node("drawing-node", "drawing", { drawingId: "drawing-document", drawingRevision: "9007199254740993", drawingShapeCount: 2 });
    const context = source.buildNodeGenerationContext(target.id, [drawing, target], [{ id: "drawing-text", fromNodeId: drawing.id, toNodeId: target.id }], "描述 @绘图1。", []);
    assert.equal(context.prompt, "描述 @绘图1。");
    assert.equal(context.referenceImages.length, 1);
    assert.deepEqual(context.referenceImages[0].source, { kind: "drawing", drawingId: "drawing-document", revision: "9007199254740993", shapeCount: 2 });
});

test("文字份数沿源归一化为1到15，取绝对值并向下取整", () => {
    for (const [value, expected] of [["", 1], ["0", 1], ["0.5", 1], ["2.9", 2], ["-3", 3], ["15", 15], ["16", 15], ["invalid", 1]]) {
        assert.equal(source.getGenerationCount(value), expected, value);
    }
});

function drawingImage(shapeCount = 1) {
    return { id: "drawing-node", name: "参考绘图", type: "image/png", dataUrl: "", source: { kind: "drawing", drawingId: "drawing-document", revision: "9007199254740993", shapeCount } };
}

test("已归档绘图复用当前版本永久资源，无重复上传或发布", async () => {
    const image = drawingImage();
    const before = structuredClone(image);
    source.setDrawingBoundary({ render: { revision: image.source.revision, storageKey: "resource:9007199254740999", url: "/saved.png", mimeType: "image/png" } });
    const resolved = await source.resolveCanvasDrawingReference("canvas-key", image);
    assert.equal(resolved.storageKey, "resource:9007199254740999");
    assert.equal(resolved.url, "/saved.png");
    assert.deepEqual(image, before);
    assert.deepEqual(source.boundary.loads, [["canvas-key", "drawing-document"]]);
    assert.deepEqual(source.boundary.uploads, []);
    assert.deepEqual(source.boundary.publications, []);
});

test("空绘图、缺少当前生成图和旧版本生成图在上传前拒绝", async () => {
    source.setDrawingBoundary({});
    await assert.rejects(source.resolveCanvasDrawingReference("canvas-key", drawingImage(0)), /为空/);
    assert.deepEqual(source.boundary.loads, []);
    for (const render of [null, { revision: "9007199254740992", storageKey: "resource:101" }]) {
        source.setDrawingBoundary({ render });
        await assert.rejects(source.resolveCanvasDrawingReference("canvas-key", drawingImage()), /缺少当前版本/);
        assert.deepEqual(source.boundary.uploads, []);
        assert.deepEqual(source.boundary.publications, []);
    }
});

test("本机绘图先上传当前渲染再按精确版本发布，返回永久身份", async () => {
    const image = drawingImage();
    const blob = new Blob(["drawing-render"], { type: "image/png" });
    source.setDrawingBoundary({ render: { revision: image.source.revision, blob, storageKey: "drawing:local", width: 320, height: 200, mimeType: "image/png" }, resource: { id: "9007199254740999", mimeType: "image/png" } });
    const resolved = await source.resolveCanvasDrawingReference("canvas-key", image);
    assert.equal(resolved.storageKey, "resource:9007199254740999");
    assert.equal(resolved.url, "/api/v1/canvas-runtime/resources/9007199254740999/file");
    assert.deepEqual(source.boundary.uploads, [[blob, "image", { width: 320, height: 200, fileName: "drawing-drawing-document-r9007199254740993.png" }]]);
    assert.deepEqual(source.boundary.publications, [["canvas-key", "drawing-document", "9007199254740993", { storageKey: resolved.storageKey, url: resolved.url }]]);
});

test("绘图上传失败带上下文，上传期间版本变动拒绝返回引用", async () => {
    const render = { revision: drawingImage().source.revision, blob: new Blob(["render"]), storageKey: "drawing:local", width: 320, height: 200, mimeType: "image/png" };
    source.setDrawingBoundary({ render, uploadError: new Error("controlled upload failure") });
    await assert.rejects(source.resolveCanvasDrawingReference("canvas-key", drawingImage()), /绘图参考图上传失败：controlled upload failure/);
    assert.deepEqual(source.boundary.publications, []);
    source.setDrawingBoundary({ render, resource: { id: "101", mimeType: "image/png" }, confirmed: false });
    await assert.rejects(source.resolveCanvasDrawingReference("canvas-key", drawingImage()), /绘图在上传期间发生了修改，请重新生成/);
    assert.equal(source.boundary.publications.length, 1);
});
