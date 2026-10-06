import assert from "node:assert/strict";
import test from "node:test";
import { collectCanvasResourceIds, remapCanvasResourceReferences, prepareCanvasDocumentResources, createCanvasResourceRemapper } from "../src/services/host-resource-normalization.ts";
import { canonicalizeCanvasMedia } from "../src/services/canvas-media-locators.ts";

test("媒体语义覆盖裁剪来源、导演/绘图封面，文字媒体类型不能改变正文", () => {
    const source = { nodes: [
        { type: "text", metadata: { content: "blob:literal", mimeType: "image/png", storageKey: "resource:42" } },
        { type: "drawing", metadata: { drawingPreviewUrl: "blob:preview", drawingPreviewStorageKey: "resource:18446744073709551615" } },
        { type: "director", metadata: { directorCoverUrl: "data:image/png;base64,preview" } },
        { type: "video", metadata: { videoTrimSource: { content: "blob:trim", storageKey: "resource:44" } } },
    ], directorScenes: [{ kind: "model", storageKey: "resource:45", url: "https://provider.example/signed?token=temporary" }], unknown: { kind: [], type: {}, content: "blob:unknown" } };
    const result = canonicalizeCanvasMedia(source);
    assert.equal(result.nodes[0], source.nodes[0]);
    assert.equal(result.nodes[1].metadata.drawingPreviewUrl, "/api/v1/canvas-runtime/resources/18446744073709551615/file");
    assert.equal(Object.hasOwn(result.nodes[2].metadata, "directorCoverUrl"), false);
    assert.equal(result.nodes[3].metadata.videoTrimSource.content, "/api/v1/canvas-runtime/resources/44/file");
    assert.equal(result.directorScenes[0].url, "/api/v1/canvas-runtime/resources/45/file");
    assert.equal(result.unknown, source.unknown);
    assert.equal(canonicalizeCanvasMedia(result), result);
});

test("没有稳定身份的临时媒体拒绝新保存，原草稿及旧回执内容不会被破坏", async () => {
    const source = { nodes: [{ type: "image", metadata: { content: "blob:unuploaded", storageKey: "browser-local-key" } }] };
    assert.throws(() => canonicalizeCanvasMedia(source), /媒体尚未持久化/);
    assert.equal(canonicalizeCanvasMedia(source, false), source);
    await assert.rejects(prepareCanvasDocumentResources(source, { assertActive() {}, normalize: () => assert.fail("invalid media must not start resource copying") }), /媒体尚未持久化/);
    const empty = { nodes: [{ type: "image", metadata: { previewContent: "blob:pending" } }] };
    assert.deepEqual(canonicalizeCanvasMedia(empty).nodes[0].metadata, {});
});

test("同一对象分别用于正文和媒体时，投影保留各自语义及共享引用", () => {
    const metadata = { content: "blob:preview", storageKey: "resource:42" };
    const source = { nodes: [{ type: "text", metadata }, { type: "image", metadata }, { type: "image", metadata }] };
    const result = canonicalizeCanvasMedia(source);
    assert.equal(result.nodes[0].metadata, metadata);
    assert.equal(result.nodes[1].metadata, result.nodes[2].metadata);
    assert.equal(result.nodes[1].metadata.content, "/api/v1/canvas-runtime/resources/42/file");
});

test("保存快照用规范文件地址替代媒体展示 Blob，不改实时预览或正文", async () => {
    const source = { nodes: [
        { id: "image", type: "image", metadata: { content: "blob:page-preview", storageKey: "resource:42", previewContent: "blob:unfinished-preview", prompt: "blob:literal-prompt" } },
        { id: "text", type: "text", metadata: { content: "blob:literal-text", storageKey: "resource:43" } },
    ], timeline: { clips: [{ id: "clip", directMedia: { kind: "video", storageKey: "resource:44", url: "https://old/api/resources/44/file?signature=expired&variant=playback#t=2" } }] } };
    const result = await prepareCanvasDocumentResources(source, { assertActive() {}, normalize: async ids => ({ resource_map: Object.fromEntries(ids.map(id => [id, id])), resource_aliases: {} }) });
    assert.equal(result.document.nodes[0].metadata.content, "/api/v1/canvas-runtime/resources/42/file");
    assert.equal(Object.hasOwn(result.document.nodes[0].metadata, "previewContent"), false);
    assert.equal(result.document.nodes[0].metadata.prompt, "blob:literal-prompt");
    assert.equal(result.document.nodes[1].metadata.content, "blob:literal-text");
    assert.equal(result.document.timeline.clips[0].directMedia.url, "/api/v1/canvas-runtime/resources/44/file?variant=playback#t=2");
    assert.equal(source.nodes[0].metadata.content, "blob:page-preview");
});

test("只识别真实媒体定位字段与 resource 键，保留超大 ID 和无关创作内容", () => {
    const doc = {
        nodes: [{ id: "30", metadata: { storageKey: "resource:18446744073709551615", prompt: "https://old/api/resources/12/file", taskId: "13", resourceId: "14", referenceResourceIds: ["15", "15"], unrelated: "resource:16" } }],
        timeline: { clips: [{ directMedia: { url: "https://old/api/resources/17/file?variant=playback&proxy=1" } }] },
        directorScenes: [{ model: "resource:18" }],
        content: "这段正文包含 resource:19 和 https://old/api/resources/20/file",
        coverUrl: "blob:local", referenceUrls: ["/api/v1/canvas-runtime/resources/21/file", "https://cdn/image.png"],
    };
    assert.deepEqual(collectCanvasResourceIds(doc), ["14", "15", "16", "17", "18", "18446744073709551615", "21"]);
    const changed = remapCanvasResourceReferences(doc, { "14": "24", "17": "27", "18446744073709551615": "18446744073709551614" });
    assert.equal(changed.nodes[0].metadata.storageKey, "resource:18446744073709551614");
    assert.equal(changed.nodes[0].metadata.resourceId, "24");
    assert.equal(changed.timeline.clips[0].directMedia.url, "/api/v1/canvas-runtime/resources/27/file?variant=playback&proxy=1");
    assert.equal(changed.nodes[0].metadata.prompt, doc.nodes[0].metadata.prompt);
    assert.equal(changed.directorScenes, doc.directorScenes);
    assert.equal(doc.nodes[0].metadata.storageKey, "resource:18446744073709551615");
    assert.equal(remapCanvasResourceReferences(doc, {}), doc);
    assert.throws(() => collectCanvasResourceIds({ storageKey: "resource:18446744073709551616" }), /超出有效范围/);
});

test("复制 URL 保留播放行为，去掉原域名和临时认证参数", () => {
    const input = { content: "https://old/api/resources/42/file?signature=temporary&variant=playback&proxy=1#t=2" };
    assert.equal(remapCanvasResourceReferences(input, { "42": "43" }).content, "/api/v1/canvas-runtime/resources/43/file?variant=playback&proxy=1#t=2");
    assert.equal(remapCanvasResourceReferences(input, { "42": "42" }), input);
});

test("同次映射保留编辑器与撤销历史的共享引用，不制造新的用户编辑", () => {
    const node = { id: "node", metadata: { storageKey: "resource:10" } };
    const live = { nodes: [node] };
    const snapshot = { nodes: live.nodes };
    const patch = { nodes: { changes: [{ id: node.id, before: node, after: node }] } };
    const remap = createCanvasResourceRemapper({ "10": "20" });
    const mapped = remap(live);
    assert.equal(mapped.nodes, remap(snapshot).nodes);
    assert.equal(mapped.nodes[0], remap(patch).nodes.changes[0].before);
    assert.equal(mapped.nodes[0], remap(patch).nodes.changes[0].after);
    assert.equal(mapped.nodes[0].metadata.storageKey, "resource:20");
    assert.equal(node.metadata.storageKey, "resource:10");
    const cycle = { nodes: [] };
    cycle.nodes.push(cycle);
    assert.throws(() => remap(cycle), /嵌套内容/);
    const shared = ["10", "/api/resources/10/file"];
    const contexts = remap({ prompt: shared, content: shared, resourceIds: shared });
    assert.equal(contexts.prompt, shared);
    assert.deepEqual(contexts.content, ["10", "/api/v1/canvas-runtime/resources/20/file"]);
    assert.deepEqual(contexts.resourceIds, ["20", "/api/v1/canvas-runtime/resources/20/file"]);
});

test("异步准备冻结原请求，对实时图只替换仍相同的资源，不复活删除或覆盖新输入", async () => {
    const original = { id: "canvas", nodes: [{ id: "a", position: { x: 1 }, metadata: { storageKey: "resource:10", assetId: "asset" } }, { id: "b", metadata: { storageKey: "resource:10" } }], title: "之前" };
    let finish;
    const pending = prepareCanvasDocumentResources(original, {
        assertActive() {},
        normalize: ids => new Promise(resolve => { assert.deepEqual(ids, ["10"]); finish = resolve; }),
    });
    original.title = "新输入";
    original.nodes = [original.nodes[0], { id: "c", metadata: { storageKey: "resource:11" } }];
    original.nodes[0].position.x = 100;
    finish({ resource_map: { "10": "20" }, resource_aliases: { "20": ["10"] } });
    const prepared = await pending;
    assert.equal(prepared.document.title, "之前");
    assert.deepEqual(prepared.document.nodes.map(node => node.id), ["a", "b"]);
    assert.equal(prepared.document.nodes[0].position.x, 1);
    const live = remapCanvasResourceReferences(original, prepared.resourceMap);
    assert.equal(live.title, "新输入");
    assert.deepEqual(live.nodes.map(node => node.id), ["a", "c"]);
    assert.equal(live.nodes[0].position.x, 100);
    assert.equal(live.nodes[0].metadata.assetId, "asset");
    assert.equal(live.nodes[0].metadata.storageKey, "resource:20");
    assert.equal(live.nodes[1].metadata.storageKey, "resource:11");
});

test("没有资源时不请求后端；分批与部分失败保留同样的资源请求", async () => {
    const calls = [];
    const options = { assertActive() {}, normalize: async ids => {
        calls.push(ids);
        return { resource_map: Object.fromEntries(ids.map(id => [id, id])), resource_aliases: {} };
    } };
    await prepareCanvasDocumentResources({ title: "文字" }, options);
    assert.equal(calls.length, 0);
    const document = { resourceIds: Array.from({ length: 201 }, (_, index) => String(index + 1)) };
    assert.deepEqual((await prepareCanvasDocumentResources(document, options)).document, document);
    assert.deepEqual(calls.map(ids => ids.length), [200, 1]);
    const first = structuredClone(calls);
    calls.length = 0;
    await prepareCanvasDocumentResources(document, options);
    assert.deepEqual(calls, first);
    const failure = new Error("storage response lost");
    await assert.rejects(prepareCanvasDocumentResources(document, { ...options, normalize: async () => { throw failure; } }), error => error === failure);
});

test("缺项、额外映射、非字符串 ID 或来源不符的响应不能进入保存", async () => {
    const malformed = [
        { resource_map: {}, resource_aliases: {} },
        { resource_map: { "10": "20", "11": "21" }, resource_aliases: { "20": ["10"], "21": ["11"] } },
        { resource_map: { "10": 20 }, resource_aliases: {} },
        { resource_map: { "10": "20" }, resource_aliases: { "20": ["11"] } },
        { resource_map: { "10": "20" }, resource_aliases: { "20": ["10"], "30": ["10"] } },
    ];
    for (const response of malformed) await assert.rejects(prepareCanvasDocumentResources({ storageKey: "resource:10" }, {
        assertActive() {}, normalize: async () => response,
    }), /归一化响应/);
});

test("第二批失败不会泄漏半份文档或修改源草稿，重试仍请求同一批身份", async () => {
    const document = { resourceIds: Array.from({ length: 201 }, (_, index) => String(index + 1)) };
    const original = structuredClone(document);
    const requests = [];
    let fail = true;
    const options = { assertActive() {}, normalize: async ids => {
        requests.push([...ids]);
        if (ids.length === 1 && fail) throw new Error("second batch lost");
        const resource_map = Object.fromEntries(ids.map(id => [id, `8${id}`]));
        return { resource_map, resource_aliases: Object.fromEntries(ids.map(id => [`8${id}`, [id]])) };
    } };
    await assert.rejects(prepareCanvasDocumentResources(document, options), /second batch lost/);
    assert.deepEqual(document, original);
    assert.deepEqual(requests.map(ids => ids.length), [200, 1]);
    fail = false;
    const completed = await prepareCanvasDocumentResources(document, options);
    assert.deepEqual(requests.slice(2), requests.slice(0, 2));
    assert.deepEqual(completed.document.resourceIds, original.resourceIds.map(id => `8${id}`));
    assert.deepEqual(document, original);
});

test("等待期间会话失效或画布被删除时停止，旧结果不能返回给调用方回填", async () => {
    let active = true;
    let finish;
    const pending = prepareCanvasDocumentResources({ storageKey: "resource:10" }, {
        assertActive() { if (!active) throw new Error("abandoned"); },
        normalize: () => new Promise(resolve => { finish = resolve; }),
    });
    active = false;
    finish({ resource_map: { "10": "20" }, resource_aliases: { "20": ["10"] } });
    await assert.rejects(pending, /abandoned/);
});
