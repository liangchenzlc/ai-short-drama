import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

// Run the actual pure source serializer; its surrounding media I/O is unused.
const source = readFileSync(new URL("../src/services/project-asset-sync.ts", import.meta.url), "utf8");
const start = source.indexOf("export function projectGenerationTaskResult(");
const end = source.indexOf("export function hasBackendDeliveredGenerationOutputs", start);
assert.ok(start >= 0 && end > start);
const javascript = ts.transpileModule(source.slice(start, end).replace("export function", "function"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const serialize = vm.runInNewContext(`${javascript}\nprojectGenerationTaskResult`, { generationTaskResult: task => JSON.parse(task.resultJson) });

test("压缩的0与2号产物仍映射真实槽位，不生成不存在的1号槽", () => {
    const task = { id: "task", status: "succeeded", outputs: [{ outputIndex: 0, mediaType: "image", materializedAssetId: "asset-zero" }, { outputIndex: 2, mediaType: "image", materializedAssetId: "asset-two" }] };
    const result = { mode: "image", images: [{ storageKey: "resource:101" }, { storageKey: "resource:103" }] };
    const projected = serialize(task, result);
    assert.deepEqual(Array.from(projected.outputs, output => output.outputIndex), [0, 2]);
    assert.deepEqual(Array.from(projected.outputs, output => output.materializedAssetId), ["asset-zero", "asset-two"]);
    assert.equal(projected.resultState, undefined);
});

test("连续槽位与尚未完全交付的源图片数组仍沿用原顺序", () => {
    const result = { images: [{ storageKey: "resource:101" }, { storageKey: "resource:102" }, { storageKey: "resource:103" }] };
    const task = { status: "succeeded", outputs: [{ outputIndex: 0, mediaType: "image", materializedAssetId: "asset-zero" }, { outputIndex: 2, mediaType: "image", materializedAssetId: "asset-two" }] };
    const projected = serialize(task, result);
    assert.deepEqual(Array.from(projected.outputs, output => output.outputIndex), [0, 1, 2]);
    assert.equal(projected.outputs[1].materializedAssetId, undefined);
    assert.equal(projected.outputs[2].materializedAssetId, "asset-two");
    assert.equal(projected.resultState, "PENDING_MATERIALIZATION");
    const original = serialize({ status: "succeeded" }, result);
    assert.deepEqual(Array.from(original.outputs, output => output.outputIndex), [0, 1, 2]);
});

test("单个视频或音频交付采用后端真实槽号", () => {
    for (const mediaType of ["video", "audio"]) {
        const projected = serialize({ status: "succeeded", outputs: [{ outputIndex: 2, mediaType, materializedAssetId: "asset-two" }] }, { [mediaType]: { storageKey: "resource:103" } });
        assert.equal(projected.outputs[0].outputIndex, 2);
        assert.equal(projected.outputs[0].materializedAssetId, "asset-two");
    }
});

test("项级outputIndex优先于数组顺序，缺失交付项不补造新槽位", () => {
    const result = { images: [{ outputIndex: 2, storageKey: "resource:103" }, { outputIndex: 0, storageKey: "resource:101" }] };
    const projected = serialize({ status: "succeeded", outputs: [{ outputIndex: 0, mediaType: "image", materializedAssetId: "asset-zero" }] }, result);
    assert.deepEqual(Array.from(projected.outputs, output => output.outputIndex), [2, 0]);
    assert.equal(projected.outputs[0].materializedAssetId, undefined);
    assert.equal(projected.outputs[1].materializedAssetId, "asset-zero");
});
