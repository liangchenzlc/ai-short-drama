import assert from "node:assert/strict";
import test from "node:test";
import { canvasKeyFromPath, resourceUploadHeaders, resourceUploadIdentity } from "../src/services/host-resource-upload.ts";

test("上传捕获真实画布路径，全局页面保持私人范围", () => {
    assert.equal(canvasKeyFromPath("/canvas-app/canvas/source%3Aone"), "source:one");
    assert.equal(canvasKeyFromPath("/projects/123"), null);
    assert.equal(canvasKeyFromPath("/canvas-app/canvas/%invalid"), null);
    const key = canvasKeyFromPath("/canvas-app/canvas/one");
    canvasKeyFromPath("/canvas-app/canvas/two");
    assert.deepEqual(resourceUploadHeaders("stable", key), { "X-Idempotency-Key": "stable", "X-Canvas-Key": "one" });
});

test("未知上传结果复用同一 Blob 的键，源本地存储键优先", () => {
    const first = new Blob(["file"]);
    assert.equal(resourceUploadIdentity(first), resourceUploadIdentity(first));
    assert.notEqual(resourceUploadIdentity(first), resourceUploadIdentity(new Blob(["file"])));
    assert.equal(resourceUploadIdentity(first, " source-file-key "), "source-file-key");
});
