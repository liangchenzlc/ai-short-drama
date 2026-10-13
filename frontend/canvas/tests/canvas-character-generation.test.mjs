import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

// Run the product reader, normalizer and source hydration; only HTTP/storage/epoch boundaries are controlled.
function load(path, require) {
    const source = readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8");
    const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
    const exports = {};
    vm.runInNewContext(code, { exports, require, structuredClone, URL, URLSearchParams, Error });
    return exports;
}
const plain = value => JSON.parse(JSON.stringify(value));
function harness() {
    const context = { canvas_key: "canvas-character", canvas_row_version: "7", source_project_id: "91" };
    const imageResource = { id: "43", userId: "1", kind: "image", status: "ready", provider: "minio", endpoint: "", bucket: "", objectKey: "", publicUrl: "", mimeType: "image/png", size: 128, width: 3, height: 2, durationMs: 0, etag: "", createdAt: "2026-10-01T00:00:00Z", updatedAt: "2026-10-01T00:00:00Z" };
    const item = { asset_id: "21", asset_row_version: "1", current_version_id: "31", version_id: "31", version: "1", position: 0, name: "阿青", label: "", description: "青衣", prompt: "保持青衣", tags: [], definition: { description: "青衣", voiceLanguage: "中文" }, definition_origin: "legacy_current",
        representations: [{ reference_id: "41", role: "primary", ordinal: 0, media_id: "43", format_code: "image/png", mime_type: "image/png", original_name: "hero.png", byte_size: 128, width: 3, height: 2 }],
        voice_status: "ready", voice_sample: { binding_version: "2", media_id: "44", checksum_sha256: "b".repeat(64), duration_ms: 3200,
            resource: { ...imageResource, id: "44", kind: "audio", mimeType: "audio/wav", durationMs: 3200, etag: "b".repeat(64) } } };
    const calls = [], imageReads = [], mediaReads = [], aliases = [];
    let active = true, invalidMapping = false, epochOnCopy = false;
    const scope = { captureUserScope: () => ({ userScope: "1", epoch: 1 }), assertUserScope: () => { if (!active) throw new Error("账号已切换"); } };
    const host = { get: async (path) => { calls.push(["GET", path]); return { data: path.endsWith("image-resources") ? { ...context, asset_id: "21", version_id: item.version_id,
        images: [{ reference_id: "41", role: "primary", ordinal: 0, media_id: "43", resource_key: "resource:43", file_path: "/api/v1/canvas-runtime/resources/43/file", requires_canvas_copy: true, resource: imageResource }] } : path.endsWith("/21") ? { ...context, item } : { ...context, items: [item] } }; },
        post: async (path, body) => { calls.push(["POST", path, plain(body)]); if (epochOnCopy) active = false;
            const mapping = Object.fromEntries(body.resource_ids.map(id => [id, `1${id}`]));
            return { data: { resource_map: mapping, resource_aliases: invalidMapping ? {} : Object.fromEntries(body.resource_ids.map(id => [mapping[id], [id]])) } }; } };
    const mediaLocators = load("services/canvas-media-locators.ts", () => ({}));
    const normalizer = load("services/host-resource-normalization.ts", () => mediaLocators);
    const images = load("services/host-character-images.ts", () => ({}));
    const catalog = load("services/host-character-catalog.ts", () => images);
    const helper = load("services/host-character-generation.ts", name => {
        if (name === "@host/api/http") return { http: host };
        if (name === "@/lib/user-scope-guard") return scope;
        if (name === "./host-canvas-access") return { hostCanvasAccess: { assert: scope.assertUserScope } };
        if (name === "./host-character-catalog") return catalog;
        if (name === "./host-resource-normalization") return normalizer;
        if (name === "./host-canvas-resource-preparation") return { persistHostCanvasResourceAliases: async (_, value) => aliases.push(plain(value)) };
        assert.fail(name);
    });
    const reference = load("lib/canvas/canvas-character-reference.ts", () => ({}));
    const generation = load("components/canvas/canvas-node-generation.ts", name => {
        if (name === "@/services/host-character-generation") return helper;
        if (name === "@/services/api/resources") return {
            resourceIdFromStorageKey: key => key?.slice(9), resourceStorageKey: id => `resource:${id}`, resourceFileUrl: id => `/api/v1/canvas-runtime/resources/${id}/file`,
            getResource: async id => { mediaReads.push(id); return { mimeType: "audio/wav", objectKey: "", durationMs: 3200, size: 128 }; },
        };
        if (name === "@/services/image-storage") return { imageToDataUrl: async image => { imageReads.push(image.storageKey); return "data:image/png;base64,fixture"; } };
        if (name === "@/lib/canvas/canvas-character-reference") return reference;
        if (name === "@/lib/reference-media-metadata") return { resolveReferenceMediaDuration: async value => value };
        if (name === "@/lib/character-voice-formats") return { audioFileExtension: () => "wav" };
        return {};
    });
    const input = { prompt: "慢慢说话", referenceImages: [], referenceVideos: [], referenceAudios: [], characterReferences: [{ nodeId: "role", assetId: "21" }], resolvedCharacterVersions: [], resolvedCharacterVoices: [], textCount: 0, imageCount: 0, videoCount: 0, audioCount: 0 };
    const hydrate = (mode = "video", samples = true, limits = { maxImages: 3, maxAudios: 2 }) => generation.hydrateNodeGenerationContext(input, context.canvas_key, context.source_project_id, mode, samples, true, limits);
    return { input, item, calls, imageReads, mediaReads, aliases, hydrate, invalid: () => { invalidMapping = true; }, switchOnCopy: () => { epochOnCopy = true; } };
}
test("角色图与样音先复制归一化，再用目标资源生成；绑定声明仍保留真实原样音", async () => {
    const h = harness(); const result = plain(await h.hydrate());
    const post = h.calls.find(call => call[0] === "POST");
    assert.equal(post[1], "/canvas-runtime/resources/normalize");
    assert.deepEqual(post[2], { canvas_key: "canvas-character", resource_ids: ["43", "44"] });
    assert.deepEqual(h.imageReads, ["resource:143"]); assert.deepEqual(h.mediaReads, ["144"]);
    assert.equal(result.referenceImages[0].storageKey, "resource:143");
    assert.equal(result.referenceAudios[0].storageKey, "resource:144");
    assert.equal(result.resolvedCharacterVoices[0].sampleResourceId, "44");
    assert.equal(result.resolvedCharacterVoices[0].bindingVersion, "2");
    assert.deepEqual(h.aliases, [{ "143": ["43"], "144": ["44"] }]);
});
test("不支持声音样本的模型只复制角色图，不携带虚假声音声明", async () => {
    const h = harness(); const result = plain(await h.hydrate("video", false));
    assert.equal(result.referenceAudios.length, 0); assert.equal(result.resolvedCharacterVoices.length, 0);
    assert.deepEqual(h.calls.find(call => call[0] === "POST")[2].resource_ids, ["43"]);
});
test("独立TTS不能把已采用样音冒充供应商音色，不复制或提交", async () => {
    const h = harness(); await assert.rejects(h.hydrate("audio"), /需要供应商音色/);
    assert.equal(h.calls.some(call => call[0] === "POST"), false);
});
test("参考音频容量不足在复制前拒绝", async () => {
    const h = harness(); await assert.rejects(h.hydrate("video", true, { maxImages: 3, maxAudios: 0 }), /容量不足/);
    assert.equal(h.calls.some(call => call[0] === "POST"), false);
});
test("复制映射不完整或期间账号变化，不继续媒体读取和生成", async () => {
    for (const damage of ["invalid", "epoch"]) {
        const h = harness(); if (damage === "invalid") h.invalid(); else h.switchOnCopy();
        await assert.rejects(h.hydrate(), /归一化响应不完整|账号已切换/);
        assert.equal(h.imageReads.length, 0); assert.equal(h.mediaReads.length, 0); assert.equal(h.aliases.length, 0);
    }
});

test("同角色的多个源节点保留输入路径，冻结身份声明仅发送一次", async () => {
    const h = harness(); h.input.characterReferences.push({ nodeId: "role-2", assetId: "21" });
    const result = plain(await h.hydrate());
    assert.equal(result.characterReferences.length, 2);
    assert.equal(result.resolvedCharacterVersions.length, 1); assert.equal(result.resolvedCharacterVoices.length, 1);
    assert.equal(result.referenceAudios.length, 1);
});
