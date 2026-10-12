import assert from "node:assert/strict";
import test from "node:test";

import { readCanvasCharacterImages } from "../src/services/host-character-images.ts";

const id = "9400000000000001";
function fixture() {
    const expected = {
        canvas_key: "catalog-fixture-key", asset_id: "9200000000000001", version_id: "9500000000000001",
        canvas_row_version: "7", source_project_id: "9100000000000002",
        representations: [
            { reference_id: "9600000000000001", role: "side", ordinal: 0, media_id: id },
            { reference_id: "9600000000000002", role: "front", ordinal: 1, media_id: id },
        ],
    };
    const response = {
        canvas_key: expected.canvas_key, canvas_row_version: "7", source_project_id: "9100000000000002",
        asset_id: expected.asset_id, version_id: expected.version_id,
        images: expected.representations.map(slot => ({
            ...slot, resource_key: `resource:${id}`, file_path: `/api/v1/canvas-runtime/resources/${id}/file`,
            requires_canvas_copy: true,
            resource: {
                id, userId: "1", kind: "image", status: "ready", provider: "minio",
                endpoint: "", bucket: "", objectKey: "", publicUrl: "", mimeType: "image/webp",
                size: 12, width: 2, height: 3, durationMs: 0, etag: "",
                createdAt: "2026-10-01T00:00:00.000Z", updatedAt: "2026-10-01T00:00:00.000Z",
            },
        })),
    };
    const calls = [];
    let active = true;
    const transport = { get: async (path, options) => { calls.push({ path, options }); return response; } };
    const assertCurrent = () => { if (!active) throw new Error("账号已切换"); };
    return { expected, response, calls, transport, assertCurrent, switchAccount: () => { active = false; } };
}

function read(f) { return readCanvasCharacterImages(f.transport, f.expected, f.assertCurrent); }

test("只发送一个带预期版本的 GET，并保留重复媒体的真实槽位与实际 MIME", async () => {
    const f = fixture();
    const result = await read(f);
    assert.equal(f.calls.length, 1);
    assert.equal(f.calls[0].path, "/canvas-runtime/canvas-projects/catalog-fixture-key/character-catalog/9200000000000001/image-resources");
    assert.deepEqual(f.calls[0].options, { params: { expected_version_id: f.expected.version_id } });
    assert.equal(result.images.length, 2);
    assert.equal(result.images[0].resource.mimeType, "image/webp");
    assert.equal(result.images[0].requires_canvas_copy, true);
});

for (const field of ["canvas_key", "canvas_row_version", "source_project_id", "asset_id", "version_id"]) {
    test(`响应 ${field} 不匹配时拒绝`, async () => {
        const f = fixture();
        f.response[field] = field === "canvas_key" ? "other-key" : "1";
        await assert.rejects(read(f), /图片解析响应/);
    });
}

test("canonical 中文画布键只编码路径，不变更身份", async () => {
    const f = fixture();
    f.expected.canvas_key = f.response.canvas_key = "画布-1";
    await read(f);
    assert.match(f.calls[0].path, /%E7%94%BB%E5%B8%83-1/);
});

for (const damage of ["missing", "reordered", "role", "media", "reference", "resource", "locator", "mime"]) {
    test(`响应槽位或资源 ${damage} 不匹配时拒绝`, async () => {
        const f = fixture();
        if (damage === "missing") f.response.images.pop();
        else if (damage === "reordered") f.response.images.reverse();
        else if (damage === "resource") f.response.images[0].resource.id = "1";
        else if (damage === "locator") f.response.images[0].resource.objectKey = "private/raw";
        else if (damage === "mime") f.response.images[0].resource.mimeType = "video/mp4";
        else f.response.images[0][damage === "media" ? "media_id" : damage === "reference" ? "reference_id" : "role"] = damage === "role" ? "back" : "1";
        await assert.rejects(read(f), /图片解析响应/);
    });
}

test("读取前账号已切换不发 GET，读取期间切换不返回旧响应", async () => {
    const first = fixture();
    first.switchAccount();
    await assert.rejects(read(first), /账号已切换/);
    assert.equal(first.calls.length, 0);
    const second = fixture();
    second.transport.get = async () => { second.switchAccount(); return second.response; };
    await assert.rejects(read(second), /账号已切换/);
});

test("失败不自动重试或写入，调用者再次读取才发新 GET", async () => {
    const f = fixture();
    let count = 0;
    f.transport.get = async () => { count++; throw new Error("controlled HTTP failure"); };
    await assert.rejects(read(f), /controlled HTTP failure/);
    assert.equal(count, 1);
    await assert.rejects(read(f), /controlled HTTP failure/);
    assert.equal(count, 2);
});

test("请求 ID 不能使用 Number 或超出 uint64，也不能在请求期间被修改", async () => {
    for (const invalid of [1, "0", "18446744073709551616"]) {
        const f = fixture();
        f.expected.version_id = invalid;
        await assert.rejects(read(f), /角色图片读取参数/);
        assert.equal(f.calls.length, 0);
    }
    const f = fixture();
    f.transport.get = async () => { f.expected.version_id = "1"; return f.response; };
    await assert.rejects(read(f), /读取上下文已变化/);
});
