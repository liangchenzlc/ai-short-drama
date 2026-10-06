import assert from "node:assert/strict";
import test from "node:test";
import { prepareInitialCanvasWrite, readInitialCanvasWrite, clearInitialCanvasWrite, initialCanvasDocument, initialCanvasResourceIds } from "../src/services/canvas-initial-write.ts";

test("归档的素材绑定意图随首次请求持久保存，不能进入共享图", async () => {
    const values = new Map();
    const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
    const project = { id: "archive", revision: "0", nodes: [], initialArchiveBindings: true };
    await prepareInitialCanvasWrite(storage, project);
    const restored = await readInitialCanvasWrite(storage, "archive");
    assert.equal(restored.initialArchiveBindings, true);
    assert.deepEqual(initialCanvasDocument(restored), { id: "archive", revision: "0", nodes: [] });
});

test("绘图首次请求冻结精确笔画与全部媒体，清单不进入共享图", async () => {
    const values = new Map();
    const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
    const drawing = { drawingId: "sketch", revision: "0", engine: "excalidraw", shapeCount: 1, pageCount: 1,
        snapshot: { elements: [{ y: 10.399993896484375 }], files: { picture: { dataURL: "/api/v1/canvas-runtime/resources/43/file" } } },
        previewResourceId: "41", render: { resourceId: "42", storageKey: "resource:42" },
    };
    const project = { id: "copy", revision: "0", nodes: [], initialDrawingDocuments: [drawing] };
    const frozen = await prepareInitialCanvasWrite(storage, project);
    drawing.snapshot.elements[0].y = 77;
    assert.deepEqual(await prepareInitialCanvasWrite(storage, project), frozen);
    const resumed = await readInitialCanvasWrite(storage, "copy");
    assert.equal(resumed.initialDrawingDocuments[0].snapshot.elements[0].y, 10.399993896484375);
    assert.deepEqual(initialCanvasResourceIds(resumed), ["41", "42", "43"]);
    assert.equal("initialDrawingDocuments" in initialCanvasDocument(resumed), false);
    assert.equal(resumed.initialDrawingDocuments.length, 1);
});

test("首次保存响应不明时保留原请求，后续输入另行提交", async () => {
    const values = new Map();
    const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value); }, removeItem: key => { values.delete(key); } };
    const project = { id: "a", revision: "0", title: "初始", nodes: [] };
    const first = await prepareInitialCanvasWrite(storage, project);
    project.title = "后续编辑";
    assert.deepEqual(await prepareInitialCanvasWrite(storage, project), first);
    assert.equal(first.title, "初始");
    await clearInitialCanvasWrite(storage, project.id);
    assert.equal((await prepareInitialCanvasWrite(storage, project)).title, "后续编辑");
});

test("无法持久化首次请求时应当停止，不能丢失重试身份", async () => {
    const storage = { getItem: () => null, setItem: () => { throw new Error("quota"); }, removeItem: () => {} };
    await assert.rejects(prepareInitialCanvasWrite(storage, { id: "a", nodes: [] }), /quota/);
});

test("新的首次请求规范媒体展示地址，既有未知结果请求仍按原文恢复", async () => {
    let raw = null;
    const storage = { getItem: () => raw, setItem: (_key, value) => { raw = value; }, removeItem: () => {} };
    const original = { id: "a", revision: "0", nodes: [{ id: "image", type: "image", metadata: { storageKey: "resource:42", content: "blob:local" } }] };
    const created = await prepareInitialCanvasWrite(storage, original);
    assert.equal(created.nodes[0].metadata.content, "/api/v1/canvas-runtime/resources/42/file");
    assert.equal(original.nodes[0].metadata.content, "blob:local");
    raw = JSON.stringify(original);
    const existing = raw;
    assert.deepEqual(await prepareInitialCanvasWrite(storage, created), original);
    assert.equal(raw, existing);
});

test("缺失首次记录不能生成恢复请求，损坏记录不能覆盖", async () => {
    let raw = null;
    const storage = { getItem: () => raw, setItem: () => assert.fail("read must not create"), removeItem: () => assert.fail("read must not delete") };
    assert.equal(await readInitialCanvasWrite(storage, "a"), null);
    for (raw of ["", "{broken", "null", JSON.stringify({ id: "other", revision: "0", nodes: [] }), JSON.stringify({ id: "a", revision: "1", nodes: [] })]) {
        await assert.rejects(readInitialCanvasWrite(storage, "a"), /原有草稿已保留/);
    }
    raw = JSON.stringify({ id: "a", revision: "0", nodes: [] });
    assert.deepEqual(await readInitialCanvasWrite(storage, "a"), JSON.parse(raw));
});
