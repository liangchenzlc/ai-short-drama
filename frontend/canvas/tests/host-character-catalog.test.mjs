import assert from "node:assert/strict";
import test from "node:test";
import { createCanvasCharacterTransport, readCanvasCharacterCatalog, readCanvasCharacterDetail, readCanvasCharacterReferenceCards } from "../src/services/host-character-catalog.ts";

function fixture() {
    const context = { canvas_key: "角色画布-1", canvas_row_version: "7", source_project_id: "9100000000000002" };
    const item = {
        asset_id: "9200000000000001", asset_row_version: "3", current_version_id: "9500000000000001", version_id: "9500000000000001", version: "18446744073709551615", position: 0,
        name: "阿青", label: "主角", description: "青衣少女", prompt: "保持青衣", tags: ["主角"],
        definition: { description: "青衣少女", prompt: "保持青衣" }, definition_origin: "legacy_current", voice_status: "unavailable", voice_sample: null,
        representations: ["side", "front", "back"].map((role, ordinal) => ({ reference_id: String(9600000000000001n + BigInt(ordinal)), role, ordinal, media_id: "9400000000000001", format_code: "webp", mime_type: "image/webp", original_name: "角色.webp", byte_size: 12, width: 2, height: 3 })),
    };
    const imageResponse = () => ({ ...context, asset_id: item.asset_id, version_id: item.current_version_id,
        images: item.representations.map(({ reference_id, role, ordinal, media_id }) => ({ reference_id, role, ordinal, media_id,
            resource_key: `resource:${media_id}`, file_path: `/api/v1/canvas-runtime/resources/${media_id}/file`, requires_canvas_copy: true,
            resource: { id: media_id, userId: "111", kind: "image", status: "ready", provider: "minio", endpoint: "", bucket: "", objectKey: "", publicUrl: "", mimeType: "image/webp", size: 12, width: 2, height: 3, durationMs: 0, etag: "", createdAt: "2026-10-01T00:00:00Z", updatedAt: "2026-10-01T00:00:00Z" },
        })),
    });
    const calls = [];
    let active = true;
    const assertCurrent = () => { if (!active) throw new Error("账号或画布已切换"); };
    const host = { get: async (path, options) => {
        calls.push({ path, options });
        return { data: path.endsWith("image-resources") ? imageResponse() : path.endsWith(`/${item.asset_id}`) ? { ...context, item } : { ...context, items: [item] } };
    } };
    const transport = createCanvasCharacterTransport(host, "111", assertCurrent);
    return { context, item, host, transport, calls, assertCurrent, deactivate: () => { active = false; }, imageResponse };
}
const read = f => readCanvasCharacterCatalog(f.transport, f.context, f.assertCurrent);

test("宿主只解包 data，单 runtime 前缀和 actor 保持；ID/version 与真实 definition 原样映射", async () => {
    const f = fixture();
    const [card] = await read(f);
    assert.equal(card.id, f.item.asset_id);
    assert.equal(card.character.version, "18446744073709551615");
    assert.deepEqual(card.character.definition, f.item.definition);
    assert.notEqual(card.character.definition, f.item.definition);
    assert.deepEqual(Object.keys(card).sort(), ["category", "character", "id", "position", "title"]);
    assert.equal(card.character.visualStatus, "ready");
    assert.equal(card.character.voiceStatus, "unavailable");
    assert.equal(card.character.voice, undefined);
    assert.equal(f.calls.length, 2);
    for (const call of f.calls) {
        assert.equal(call.options.headers["X-Canvas-Actor"], "111");
        assert.equal(call.path.split("canvas-runtime").length, 2);
        assert.match(call.path, /%E8%A7%92%E8%89%B2%E7%94%BB%E5%B8%83-1/);
    }
    assert.deepEqual(f.calls[1].options.params, { expected_version_id: f.item.current_version_id });
});

test("空目录仍读取专用接口，空表示为 missing，部分表示为 partial", async () => {
    const f = fixture();
    f.item.representations = [];
    assert.equal((await read(f))[0].character.visualStatus, "missing");
    f.item.representations = [{ reference_id: "1", role: "primary", ordinal: 0, media_id: "9400000000000001", format_code: "webp", mime_type: null, original_name: "", byte_size: null, width: null, height: null }];
    assert.equal((await read(f))[0].character.visualStatus, "partial");
    f.host.get = async () => ({ data: { ...f.context, items: [] } });
    assert.deepEqual(await read(f), []);
});

for (const field of ["canvas_key", "canvas_row_version", "source_project_id"]) {
    test(`目录 ${field} 不匹配不读取图片`, async () => {
        const f = fixture();
        f.host.get = async () => ({ data: { ...f.context, [field]: "1", items: [f.item] } });
        await assert.rejects(read(f), /来源或版本不匹配/);
    });
}

for (const [label, damage] of [
    ["number ID", f => { f.item.asset_id = 1; }],
    ["number version", f => { f.item.version = 1; }],
    ["uint64 overflow", f => { f.item.current_version_id = "18446744073709551616"; }],
    ["unknown field", f => { f.item.voice = {}; }],
    ["invalid JSON", f => { f.item.definition.bad = NaN; }],
    ["duplicate slot", f => { f.item.representations[1].reference_id = f.item.representations[0].reference_id; }],
    ["invalid role", f => { f.item.representations[0].role = "portrait"; }],
]) {
    test(`拒绝目录 ${label}`, async () => {
        const f = fixture(); damage(f);
        await assert.rejects(read(f), /来源或版本不匹配/);
        assert.equal(f.calls.length, 1);
    });
}

test("详情确认重读 current，并拒绝目录选择后变更的版本或身份", async () => {
    const f = fixture();
    const detail = () => readCanvasCharacterDetail(f.transport, f.context, f.item.asset_id, f.assertCurrent, "9500000000000001");
    assert.equal((await detail()).id, f.item.asset_id);
    f.item.current_version_id = "9500000000000002";
    await assert.rejects(detail(), /当前版本已变化/);
    const original = f.host.get;
    f.host.get = async (path, options) => {
        const result = await original(path, options);
        if (result.data.item) result.data.item.asset_id = "1";
        return result;
    };
    await assert.rejects(readCanvasCharacterDetail(f.transport, f.context, "9200000000000001", f.assertCurrent), /当前版本已变化/);
});

test("图片缺槽、重排或 media 不匹配不会回传任何 card", async () => {
    for (const damage of [r => r.images.pop(), r => r.images.reverse(), r => { r.images[0].media_id = "1"; }]) {
        const f = fixture(); const original = f.host.get;
        f.host.get = async (path, options) => {
            const result = await original(path, options);
            if (path.endsWith("image-resources")) damage(result.data);
            return result;
        };
        await assert.rejects(read(f), /图片解析响应/);
    }
});

test("未绑定来源或无效 canvas 标识不发请求；双 runtime 路径拒绝", async () => {
    for (const damage of [f => { f.context.source_project_id = ""; }, f => { f.context.canvas_key = "../other"; }, f => { f.context.canvas_row_version = "0"; }]) {
        const f = fixture(); damage(f);
        await assert.rejects(read(f), /尚未绑定/); assert.equal(f.calls.length, 0);
    }
    const f = fixture();
    await assert.rejects(f.transport.get("/canvas-runtime/canvas-runtime/canvas-projects/x/character-catalog"), /路径无效/);
});

test("读取前/期间 scope 或 row-version 改变，旧响应不返回、不继续图片请求", async () => {
    const first = fixture(); first.deactivate();
    await assert.rejects(read(first), /已切换/); assert.equal(first.calls.length, 0);
    for (const damage of [f => f.deactivate(), f => { f.context.canvas_row_version = "8"; }]) {
        const f = fixture(); const original = f.host.get;
        f.host.get = async (...args) => { const result = await original(...args); damage(f); return result; };
        await assert.rejects(read(f), /已切换|版本不匹配/); assert.equal(f.calls.length, 1);
    }
});

test("HTTP失败透传，未自动重试；详情异常不替代为旧目录", async () => {
    const f = fixture(); let count = 0;
    f.host.get = async () => { count++; throw new Error("HTTP 409 current version changed"); };
    await assert.rejects(read(f), /HTTP 409/); assert.equal(count, 1);
    await assert.rejects(readCanvasCharacterDetail(f.transport, f.context, f.item.asset_id, f.assertCurrent), /HTTP 409/);
    assert.equal(count, 2);
});


test("pinned 详情与图片显式使用历史版本，current 指针仍保持真实最新值", async () => {
    const f = fixture();
    f.item.current_version_id = "9500000000000002";
    const original = f.host.get;
    f.host.get = async (path, options) => {
        const result = await original(path, options);
        if (path.endsWith("image-resources")) result.data.version_id = f.item.version_id;
        return result;
    };
    const result = await readCanvasCharacterDetail(f.transport, f.context, f.item.asset_id, f.assertCurrent, undefined, f.item.version_id);
    assert.equal(result.character.versionId, f.item.version_id);
    assert.deepEqual(f.calls[0].options.params, { requested_version_id: f.item.version_id });
    assert.deepEqual(f.calls[1].options.params, { expected_version_id: f.item.version_id, version_policy: "pinned" });
    f.item.version_id = "9500000000000003";
    await assert.rejects(readCanvasCharacterDetail(f.transport, f.context, f.item.asset_id, f.assertCurrent, undefined, "9500000000000001"), /版本已变化/);
});

test("生成角色引用经过可信目录和详情，current 竞争不隐式追随新版", async () => {
    const f = fixture();
    const references = [{ assetId: f.item.asset_id }];
    const result = await readCanvasCharacterReferenceCards(f.transport, f.context.canvas_key, f.context.source_project_id, references, f.assertCurrent);
    assert.equal(result[0].character.versionId, f.item.version_id);
    assert.equal(f.calls.length, 3);
    const original = f.host.get;
    f.host.get = async (path, options) => {
        const result = await original(path, options);
        if (result.data.item) {
            result.data.item = { ...result.data.item, current_version_id: "9500000000000002", version_id: "9500000000000002" };
        }
        return result;
    };
    await assert.rejects(readCanvasCharacterReferenceCards(f.transport, f.context.canvas_key, f.context.source_project_id, references, f.assertCurrent), /版本已变化/);
});

test("生成引用缺少目录关联、来源变化或读取期间身份修改均拒绝", async () => {
    const missing = fixture();
    await assert.rejects(readCanvasCharacterReferenceCards(missing.transport, missing.context.canvas_key, missing.context.source_project_id, [{ assetId: "1" }], missing.assertCurrent), /未关联/);
    const f = fixture();
    await assert.rejects(readCanvasCharacterReferenceCards(f.transport, f.context.canvas_key, "1", [{ assetId: f.item.asset_id }], f.assertCurrent), /来源已变化/);
    const refs = [{ assetId: f.item.asset_id }];
    const original = f.host.get;
    f.host.get = async (...args) => { const result = await original(...args); refs[0].assetId = "1"; return result; };
    await assert.rejects(readCanvasCharacterReferenceCards(f.transport, f.context.canvas_key, f.context.source_project_id, refs, f.assertCurrent), /引用已变化/);
});


function adoptedSample(f) {
    const id = "9400000000000100";
    return { binding_version: "18446744073709551615", media_id: id, checksum_sha256: "b".repeat(64), duration_ms: 3200,
        resource: { ...f.imageResponse().images[0].resource, id, kind: "audio", mimeType: "audio/wav", durationMs: 3200, etag: "b".repeat(64) } };
}
test("已采用样音保留独立绑定版本和真实资源，不造TTS voiceKey", async () => {
    const f = fixture(); f.item.voice_status = "ready"; f.item.voice_sample = adoptedSample(f);
    const [card] = await read(f);
    assert.equal(card.character.voiceStatus, "ready");
    assert.equal(card.character.voice.profile.bindingVersion, "18446744073709551615");
    assert.equal(card.character.voice.profile.sampleResourceId, f.item.voice_sample.media_id);
    assert.equal(card.character.voice.profile.voiceKey, "");
    assert.equal(card.character.voice.profile.name, "阿青 · 已采用样音");
});
for (const damage of ["private", "resource", "sha", "duration", "mime", "unavailable"]) {
    test(`声音公共投影拒绝不可信 ${damage}`, async () => {
        const f = fixture(); f.item.voice_status = "ready"; f.item.voice_sample = adoptedSample(f);
        if (damage === "private") f.item.voice_sample.record_id = "1";
        else if (damage === "resource") f.item.voice_sample.resource.id = "1";
        else if (damage === "sha") f.item.voice_sample.resource.etag = "a".repeat(64);
        else if (damage === "duration") f.item.voice_sample.duration_ms = 8000;
        else if (damage === "mime") f.item.voice_sample.resource.mimeType = "video/mp4";
        else f.item.voice_status = "unavailable";
        await assert.rejects(read(f), /来源或版本不匹配/);
        assert.equal(f.calls.length, 1);
    });
}
