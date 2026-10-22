import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function load(path, require = () => ({})) {
    const source = readFileSync(new URL(`../../src/${path}`, import.meta.url), "utf8");
    const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
    const exports = {};
    vm.runInNewContext(code, { exports, require, structuredClone, URL, URLSearchParams, Error });
    return exports;
}
const plain = value => JSON.parse(JSON.stringify(value));
const reference = load("lib/canvas/canvas-character-reference.ts");
const context = load("lib/canvas/canvas-storyboard-context.ts");
const nodes = [
    { id: "style", title: "画风 · 写实", type: "text", metadata: { workflowKind: "styleboard", stylePresetId: "real", content: "写实电影" } },
    { id: "role", title: "阿青", type: "text", metadata: { workflowKind: "character", characterName: "阿青", characterDefinition: { role: "主角", appearance: "青衣", voiceTimbre: "低沉" } } },
];

test("独立画布以当前角色节点冻结姓名与设定，无需来源项目或角色版本", () => {
    const value = plain(context.resolveStoryboardGenerationContext(nodes));
    assert.deepEqual(value.characters, [{ nodeId: "role", name: "阿青", definition: nodes[1].metadata.characterDefinition }]);
    assert.equal(value.projectStyle.prompt, "写实电影");
});

test("角色名缺失时拒绝分镜，原生角色设计图片不会被当作空角色卡", () => {
    assert.throws(() => context.resolveStoryboardGenerationContext([nodes[0], { ...nodes[1], title: "", metadata: { ...nodes[1].metadata, characterName: "" } }]), /角色名称/);
    assert.equal(context.resolveStoryboardGenerationContext([nodes[0], { id: "image-role", type: "image", metadata: { workflowKind: "character", content: "resource:3" } }]).characters.length, 0);
});

function generationHarness() {
    const reads = [];
    const generation = load("components/canvas/canvas-node-generation.ts", name => {
        if (name === "@/lib/canvas/canvas-character-reference") return reference;
        if (name === "@/lib/reference-media-metadata") return { resolveReferenceMediaDuration: async value => ({ ...value, durationMs: 1200 }) };
        if (name === "@/services/image-storage") return { imageToDataUrl: async image => { reads.push(image.storageKey); return "data:image/png;base64,owned"; } };
        if (name.startsWith("@/services/host-character")) assert.fail("不能读取退役角色目录");
        return {};
    });
    return { generation, reads };
}
const input = () => ({ prompt: "说话", referenceImages: [{ id: "ref", storageKey: "resource:43" }], referenceVideos: [], referenceAudios: [{ id: "voice", url: "/owned.wav" }], characterReferences: [{ nodeId: "role", name: "阿青", definition: nodes[1].metadata.characterDefinition }], textCount: 0, imageCount: 1, videoCount: 0, audioCount: 1 });

test("角色生成编译本地设定并保留自己的图片和声音引用", async () => {
    const h = generationHarness();
    const value = plain(await h.generation.hydrateNodeGenerationContext(input(), "canvas", "video"));
    assert.match(value.prompt, /角色卡：阿青/);
    assert.match(value.prompt, /青衣/);
    assert.deepEqual(h.reads, ["resource:43"]);
    assert.equal(value.referenceImages[0].dataUrl, "data:image/png;base64,owned");
    assert.equal(value.referenceAudios[0].durationMs, 1200);
    assert.equal("resolvedCharacterVersions" in value, false);
    assert.equal("resolvedCharacterVoices" in value, false);
});

test("纯提示词重试不追加角色文本；参考图数量仍按模型能力拒绝", async () => {
    const h = generationHarness();
    assert.equal((await h.generation.hydrateNodeGenerationContext(input(), "canvas", "image", false)).prompt, "说话");
    await assert.rejects(h.generation.hydrateNodeGenerationContext(input(), "canvas", "image", true, { maxImages: 0 }), /参考图数量不足/);
});

test("复制分镜时原生角色节点引用随副本重映射", () => {
    const copy = load("lib/canvas/canvas-node-copy.ts", name => name.includes("canvas-project-generation") ? { resetGenerationTaskMetadata: value => ({ ...value }) } : name === "@/types/canvas" ? { CanvasNodeType: { Image: "image", Video: "video", Audio: "audio" } } : {});
    const node = { id: "script", type: "script", metadata: { storyboard: { rows: [{ id: "r", characters: [{ characterName: "阿青", characterNodeId: "role" }] }], referenceNodeIds: ["role"], visibleColumns: [] } } };
    const value = copy.isolateCopiedNodeMetadata(node, new Map([["role", "role-copy"]]));
    assert.equal(value.storyboard.rows[0].characters[0].characterNodeId, "role-copy");
});
