import assert from "node:assert/strict";
import test from "node:test";
import { portableDrawingSnapshot } from "../src/services/canvas-archive-drawings.ts";

test("绘图归档只嵌入文件资源，精确笔画与正文保持原值且不修改原稿", async () => {
    const url = "/api/v1/canvas-runtime/resources/9007199254740993/file";
    const snapshot = { elements: [{ y: 10.399993896484375, text: url }], files: {
        first: { id: "a", dataURL: url }, second: { id: "b", dataURL: url },
        inline: { dataURL: "data:image/png;base64,AQID" },
    } };
    const requests = [];
    const saved = await portableDrawingSnapshot(snapshot, async id => {
        requests.push(id);
        return new Blob([new Uint8Array([4, 5, 6])], { type: "image/png" });
    });
    assert.deepEqual(requests, ["9007199254740993"]);
    assert.deepEqual(saved.elements, snapshot.elements);
    assert.equal(saved.files.first.dataURL, "data:image/png;base64,BAUG");
    assert.equal(saved.files.second.dataURL, saved.files.first.dataURL);
    assert.deepEqual(saved.files.inline, snapshot.files.inline);
    assert.equal(snapshot.files.first.dataURL, url);
});

test("缺失、非图片或读取失败不能生成表面成功的绘图备份", async () => {
    const drawing = { files: { image: { dataURL: "/api/v1/canvas-runtime/resources/41/file" } } };
    for (const blob of [new Blob([], { type: "image/png" }), new Blob(["error"], { type: "text/html" })]) {
        await assert.rejects(portableDrawingSnapshot(drawing, async () => blob), /无法打包/);
    }
    await assert.rejects(portableDrawingSnapshot(drawing, async () => { throw new Error("storage offline"); }), /storage offline/);
});
