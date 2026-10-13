import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const fileUrl = id => `/api/v1/canvas-runtime/resources/${id}/file`;
const resources = { resourceFileUrl: fileUrl, resourceIdFromStorageKey: key => key?.startsWith("resource:") ? key.slice(9) : "", ownedResourceIdFromMediaRef: (_, url) => /\/resources\/([0-9]+)\/file$/.exec(url || "")?.[1] || "" };
function module(path, require) {
    const source = readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8");
    const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
    const exports = {};
    vm.runInNewContext(code, { exports, require, structuredClone, URL, Error });
    return exports;
}
const reference = module("lib/canvas/canvas-character-reference.ts", name => {
    assert.equal(name, "@/services/api/resources"); return resources;
});
const picker = module("components/canvas/canvas-project-asset-modal.tsx", name => {
    if (name === "@/services/api/resources") return resources;
    if (name === "@/lib/canvas/canvas-character-reference") return reference;
    if (name === "@/lib/asset-category") return { ASSET_CATEGORY_LABELS: {} };
    return {};
});
const plain = value => JSON.parse(JSON.stringify(value));
function card(version = "9007199254740995") {
    return { id: "9007199254740993", title: "阿青", category: "character", position: 0,
        character: { versionId: version, version: "2", definition: { description: "青衣", prompt: "保持青衣" }, representations: ["front", "primary", "turnaround_sheet"].map((role, index) => ({ id: String(index + 1), resourceId: String(index + 42), mediaType: "image", role })), visualStatus: "ready", voiceStatus: "unavailable" } };
}
function node(asset = card()) {
    return { id: "character-node", type: "text", title: asset.title, position: { x: 420, y: 260 }, width: 320, height: 260,
        metadata: { workflowKind: "character", characterAssetId: asset.id, characterVersionId: asset.character.versionId, characterVersionPolicy: "current", characterName: asset.title,
            characterDefinition: structuredClone(asset.character.definition), characterPrompt: reference.compileCharacterReferencePrompt(asset.title, asset.character.definition), characterAliases: [], characterCoverUrl: fileUrl("44"), characterVisualStatus: "ready", characterVoiceStatus: "unavailable" } };
}

test("current payload 保留字符串身份、原 definition、源封面优先级且不造声音事实", () => {
    const asset = card(); const result = picker.projectCharacterToInsertPayload(asset);
    assert.equal(result.kind, "character");
    assert.equal(result.assetId, "9007199254740993"); assert.equal(result.versionId, "9007199254740995");
    assert.equal(result.coverUrl, fileUrl("44")); assert.equal(result.definition, asset.character.definition);
    assert.equal(result.voiceStatus, "unavailable"); assert.equal(result.voiceProfile, undefined);
    assert.equal(result.prompt, "【角色卡：阿青】\n青衣\n保持青衣");
    assert.deepEqual(asset.character.definition, { description: "青衣", prompt: "保持青衣" });
});

test("结构化 compiler 沿用源字段；legacy 只有限使用真实 description/prompt", () => {
    assert.equal(reference.compileCharacterReferencePrompt("阿青", { role: "主角", appearance: "青衣", description: "无需扩造" }), "【角色卡：阿青】\n主角\n青衣");
    assert.equal(reference.compileCharacterReferencePrompt("阿青", { description: "  青衣  ", prompt: "稳定服装", aliases: ["小青"] }), "【角色卡：阿青】\n青衣\n稳定服装");
    assert.equal(reference.compileCharacterReferencePrompt("阿青", {}), "【角色卡：阿青】");
});

test("current 刷新保留尺寸位置、私人 prompt/voice、同版本已复制 cover，pinned 与空目录不变", () => {
    const asset = card(); const old = node(asset);
    old.metadata.characterCoverUrl = fileUrl("144");
    old.metadata.characterPrompt = "本人新提示词";
    old.metadata.characterVoiceStatus = "ready"; old.metadata.characterVoiceName = "本人声音";
    old.metadata.characterVoiceProfile = { name: "本人声音", provider: "private", language: "zh", timbre: "低沉" };
    old.metadata.characterVoiceInstructions = "本人声音输入";
    const options = { expectedNodes: new Map([[old.id, old]]), coverEquivalent: (a, b) => a === fileUrl("144") && b === fileUrl("44"), preservePrivateVoice: true, preservePrivatePrompt: true };
    const updated = reference.refreshCanvasCharacterReferenceNodes([old], [asset], options)[0];
    assert.equal(updated, old);
    assert.equal(updated.metadata.characterPrompt, "本人新提示词");
    assert.equal(updated.metadata.characterVoiceInstructions, "本人声音输入");
    assert.equal(updated.metadata.characterCoverUrl, fileUrl("144"));
    const newer = card("9007199254740996");
    const changed = reference.refreshCanvasCharacterReferenceNodes([old], [newer], options)[0];
    assert.equal(changed.metadata.characterVersionId, newer.character.versionId);
    assert.equal(changed.metadata.characterCoverUrl, fileUrl("44"));
    assert.equal(changed.metadata.characterPrompt, "本人新提示词");
    assert.deepEqual(changed.position, old.position); assert.equal(changed.width, 320); assert.equal(changed.height, 260);
    const pinned = { ...old, metadata: { ...old.metadata, characterVersionPolicy: "pinned" } };
    assert.equal(reference.refreshCanvasCharacterReferenceNodes([pinned], [newer])[0], pinned);
    const empty = [old]; assert.equal(reference.refreshCanvasCharacterReferenceNodes(empty, [], options), empty);
});

test("刷新仅 patch 请求起点未变化字段，拖动保留；删除/私人输入/身份变化不被覆盖", () => {
    const old = node(); const newer = card("9007199254740996");
    const options = { expectedNodes: new Map([[old.id, old]]) };
    const moved = { ...old, position: { x: 680, y: 390 } };
    assert.deepEqual(reference.refreshCanvasCharacterReferenceNodes([moved], [newer], options)[0].position, moved.position);
    for (const metadata of [{ ...old.metadata, characterPrompt: "输入中" }, { ...old.metadata, characterAssetId: "1" }, { ...old.metadata, characterVersionId: "2" }, { ...old.metadata, characterCoverUrl: fileUrl("88") }]) {
        const current = { ...old, metadata };
        assert.equal(reference.refreshCanvasCharacterReferenceNodes([current], [newer], options)[0], current);
    }
    assert.deepEqual(plain(reference.refreshCanvasCharacterReferenceNodes([], [newer], options)), []);
});

function harness() {
    const active = { userScope: "111", epoch: 1 };
    const refs = []; let refIndex = 0; let queryIndex = 0;
    const effects = []; const queries = []; const states = [null, null];
    const react = { useCallback: fn => fn, useRef: value => refs[refIndex++] ||= { current: value }, useEffect: fn => { effects.push(fn); } };
    const scope = module("lib/user-scope-guard.ts", () => ({ getActiveUserScope: () => active.userScope, getActiveUserScopeEpoch: () => active.epoch }));
    const owner = module("pages/canvas/canvas-owner-epoch.ts", name => name === "react" ? react : { getActiveUserScope: () => active.userScope });
    const access = module("services/host-canvas-access.ts", () => assert.fail("access has no runtime dependency"));
    const images = module("services/host-character-images.ts", () => assert.fail("image reader has no runtime dependency"));
    const catalog = module("services/host-character-catalog.ts", name => { assert.equal(name, "./host-character-images.ts"); return images; });
    const calls = []; let answer = async () => { throw new Error("response not set"); };
    const host = { get: async (...args) => { calls.push(args); return { data: await answer(...args) }; } };
    const query = { useQuery: options => {
        const index = queryIndex++;
        queries[index] = options;
        return { data: states[index], isError: false, isPending: !states[index], isFetching: false, refetch: async () => { states[index] = await options.queryFn(); return states[index]; } };
    } };
    const hook = module("pages/canvas/use-canvas-character-catalog.ts", name => {
        if (name === "react") return react;
        if (name === "@tanstack/react-query") return query;
        if (name === "@host/api/http") return { http: host };
        if (name === "@/lib/user-scope-guard") return scope;
        if (name === "./canvas-owner-epoch") return owner;
        if (name === "@/services/host-canvas-access") return access;
        if (name === "@/services/host-character-catalog") return catalog;
        if (name === "@/lib/canvas/canvas-character-reference") return reference;
        if (name === "@/services/api/resources") return resources;
        if (name === "@/services/host-resource-identities") return { canvasResourceKeysEquivalent: (a, b) => a === b };
        assert.fail(name);
    });
    const nodesRef = { current: [node()] };
    const options = { canvasKey: "canvas-one", sourceProjectId: "91", rowVersion: "7", projectLoaded: true, detailNode: null, nodesRef, setNodes: fn => { nodesRef.current = fn(nodesRef.current); } };
    return { active, calls, queries, states, effects, nodesRef, options, scope, access,
        answer: fn => { answer = fn; },
        render: (changes = {}) => { Object.assign(options, changes); refIndex = queryIndex = 0; effects.length = 0; return hook.useCanvasCharacterCatalog(options); },
    };
}

test("成员没有作者私人 alias 时，目录和详情刷新保持同版本已保存封面", () => {
    for (const index of [0, 1]) {
        const h = harness(); const asset = card(); const saved = node(asset);
        saved.metadata.characterCoverUrl = fileUrl("144");
        h.nodesRef.current = [saved];
        h.render({ detailNode: index === 1 ? saved : null });
        h.states[index] = { contextKey: h.queries[index].queryKey[1], cards: [asset], startingNodes: structuredClone([saved]) };
        h.render(); for (const effect of h.effects) effect();
        assert.equal(h.nodesRef.current[0], saved);
        assert.equal(h.nodesRef.current[0].metadata.characterCoverUrl, fileUrl("144"));
        assert.equal(h.nodesRef.current[0].metadata.characterVersionId, asset.character.versionId);
        assert.equal(h.nodesRef.current[0].metadata.characterVoiceName, undefined);
        const newer = card("9007199254740996");
        h.states[index] = { ...h.states[index], cards: [newer] };
        h.render(); for (const effect of h.effects) effect();
        assert.equal(h.nodesRef.current[0].metadata.characterVersionId, newer.character.versionId);
        assert.equal(h.nodesRef.current[0].metadata.characterCoverUrl, fileUrl("44"));
    }
});

test("同版本刷新不保留空、临时、非规范封面或已无图片的槽位", () => {
    const asset = card();
    for (const cover of [undefined, "", "blob:temporary-image", "https://example.invalid/resources/144/file", `${fileUrl("144")}?signature=temporary`]) {
        const saved = node(asset); saved.metadata.characterCoverUrl = cover;
        const options = { expectedNodes: new Map([[saved.id, saved]]), coverEquivalent: (a, b) => a === b };
        assert.equal(reference.refreshCanvasCharacterReferenceNodes([saved], [asset], options)[0].metadata.characterCoverUrl, fileUrl("44"));
    }
    const saved = node(asset); saved.metadata.characterCoverUrl = fileUrl("144");
    const withoutImages = { ...asset, character: { ...asset.character, representations: [] } };
    assert.equal(reference.refreshCanvasCharacterReferenceNodes([saved], [withoutImages])[0].metadata.characterCoverUrl, undefined);
});

test("旧 render 的 query/selection 不能在新 canvas、source 或 row-version 上启动请求", async () => {
    for (const changes of [{ canvasKey: "canvas-two" }, { sourceProjectId: "92" }, { rowVersion: "8" }]) {
        const h = harness(); h.render(); const staleQuery = h.queries[0].queryFn;
        h.render(changes);
        await assert.rejects(staleQuery(), /上下文已变化/); assert.equal(h.calls.length, 0);
    }
});

test("A→B→A epoch、卸载或撤权均使在途角色读取放弃；不继续 resolver", async () => {
    for (const abandon of [h => { h.active.epoch += 2; }, h => { h.access.hostCanvasAccess.deny(h.scope.captureUserScope(), "canvas-one"); }, h => { const cleanup = h.effects[0](); cleanup(); }]) {
        const h = harness(); h.render(); let release;
        h.answer(() => new Promise(resolve => { release = resolve; }));
        const pending = h.queries[0].queryFn();
        abandon(h);
        release({ canvas_key: "canvas-one", canvas_row_version: "7", source_project_id: "91", items: [] });
        await assert.rejects(pending, /账号已切换|上下文已变化|访问权限已变化/);
        assert.equal(h.calls.length, 1);
    }
});

test("缓存响应在撤权后 effect不回灌节点，未绑定来源禁用查询且不回个人库", () => {
    const h = harness(); h.render();
    const contextKey = h.queries[0].queryKey[1]; const before = h.nodesRef.current;
    h.states[0] = { contextKey, cards: [card("9007199254740996")], startingNodes: before };
    h.access.hostCanvasAccess.deny(h.scope.captureUserScope(), "canvas-one");
    h.render(); for (const effect of h.effects) effect();
    assert.equal(h.nodesRef.current, before);
    const empty = h.render({ sourceProjectId: "" });
    assert.equal(h.queries[0].enabled, false); assert.equal(h.queries[1].enabled, false);
    assert.deepEqual(plain(empty.characters), []); assert.match(empty.error, /尚未关联/);
});

test("selection 每次确认重读详情，并保留所选版本约束", async () => {
    const h = harness(); h.render(); const asset = card();
    h.states[0] = { contextKey: h.queries[0].queryKey[1], cards: [asset], startingNodes: h.nodesRef.current };
    const selected = h.render();
    h.answer(async () => ({ canvas_key: "canvas-one", canvas_row_version: "7", source_project_id: "91", item: {
        asset_id: asset.id, asset_row_version: "1", current_version_id: "9007199254740996", version_id: "9007199254740996", version: "3", position: 0, name: "阿青", label: "", description: "", prompt: "", tags: [], definition: {}, definition_origin: "structured", representations: [],
    } }));
    await assert.rejects(selected.readSelectedCharacter(asset.id, h.scope.captureUserScope()), /当前版本已变化/);
    assert.equal(h.calls.length, 1); assert.match(h.calls[0][0], new RegExp(`${asset.id}$`));
});

test("目录请求起点冻结角色字段，原地修改别名也不能被在途响应覆盖", async () => {
    const h = harness(); h.render(); const asset = card();
    const context = { canvas_key: "canvas-one", canvas_row_version: "7", source_project_id: "91" };
    const item = { asset_id: asset.id, asset_row_version: "1", current_version_id: "9007199254740996", version_id: "9007199254740996", version: "3", position: 0, name: "阿青", label: "", description: "新设定", prompt: "", tags: [], definition: { description: "新设定" }, definition_origin: "legacy_current", voice_status: "unavailable", voice_sample: null, representations: [] };
    h.answer(async path => path.endsWith("image-resources") ? { ...context, asset_id: asset.id, version_id: item.current_version_id, images: [] } : { ...context, items: [item] });
    const pending = h.queries[0].queryFn();
    h.nodesRef.current[0].metadata.characterAliases.push("本人刚输入的别名");
    h.states[0] = await pending;
    h.render(); for (const effect of h.effects) effect();
    assert.deepEqual(plain(h.nodesRef.current[0].metadata.characterAliases), ["本人刚输入的别名"]);
    assert.equal(h.nodesRef.current[0].metadata.characterVersionId, asset.character.versionId);
});
