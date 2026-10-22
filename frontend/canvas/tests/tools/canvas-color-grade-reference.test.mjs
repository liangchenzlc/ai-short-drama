import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const gradeFile = "../../src/lib/canvas/canvas-color-grade.ts";
const parsed = ts.createSourceFile(gradeFile, readFileSync(new URL(gradeFile, import.meta.url), "utf8"), ts.ScriptTarget.ES2022, true);
const scopeFile = "../../src/lib/user-scope-guard.ts";
const scopes = ts.createSourceFile(scopeFile, readFileSync(new URL(scopeFile, import.meta.url), "utf8"), ts.ScriptTarget.ES2022, true);
const scopeSource = scopes.statements.filter(statement => ["UserScopeAbandonedError", "captureUserScope", "userScopeMatches", "assertUserScope", "isUserScopeAbandonedError"].includes(statement.name?.text))
    .map(statement => statement.getText(scopes).replace(/^export /, "")).join("\n");
const gradeSource = parsed.statements.filter(statement => !ts.isImportDeclaration(statement))
    .map(statement => statement.getText(parsed).replace(/^export /, "")).join("\n");
const javascript = ts.transpileModule(`${scopeSource}\n${gradeSource}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

// 执行实际调色资源解析与账号epoch守卫；图像解码、2D像素和上传为边界替身。
// 真实CSS/Canvas像素另由显式浏览器专项核验。
function setup() {
    const state = { userScope: "101", epoch: 1, loads: [], filters: [], uploads: [], holdDecode: false, upload: async () => {} };
    class ImageBoundary {
        naturalWidth = 256;
        naturalHeight = 128;
        set src(value) {
            state.loads.push(value);
            if (state.holdDecode) state.releaseDecode = () => this.onload();
            else queueMicrotask(() => this.onload());
        }
    }
    const context = vm.createContext({
        Error, Blob, Image: ImageBoundary,
        getActiveUserScope: () => state.userScope,
        getActiveUserScopeEpoch: () => state.epoch,
        resourceStorageKey: id => `resource:${id}`,
        resourceFileUrl: id => `/resource/${id}/file`,
        uploadResourceFile: async (...args) => {
            state.uploads.push(args);
            await state.upload(...args);
            return { id: String(300 + state.uploads.length), mimeType: "image/png" };
        },
        document: { createElement: () => ({
            getContext: () => ({ filter: "none", drawImage() { state.filters.push(this.filter); } }),
            toBlob: callback => callback(new Blob(["controlled graded pixels"], { type: "image/png" })),
        }) },
    });
    vm.runInContext(`${javascript}\nthis.resolve = resolveCanvasColorGradeReference;`, context);
    const image = { id: "grade-node", name: "调色", type: "image/png", dataUrl: "", source: { kind: "colorgrade", url: "/source.png", grade: { brightness: 50, contrast: 100, saturate: 100, hueRotate: 0 } } };
    return { state, image, resolve: (projectId = "canvas-one") => context.resolve(image, projectId) };
}

test("同一账号epoch和画布复用已上传调色资源，输入保持不变", async () => {
    const { state, image, resolve } = setup();
    const before = structuredClone(image);
    const first = await resolve();
    assert.equal(first.storageKey, "resource:301");
    assert.equal(first.url, "/resource/301/file");
    assert.equal((await resolve()).storageKey, first.storageKey);
    assert.equal(state.uploads.length, 1);
    assert.equal(state.loads.length, 1);
    assert.deepEqual(image, before);
    assert.equal(state.uploads[0][2].hostCanvasKey, "canvas-one");
    assert.equal(state.uploads[0][2].expectedScope.userScope, "101");
    assert.equal(state.uploads[0][2].expectedScope.epoch, 1);
});

for (const change of ["account", "epoch", "canvas"]) {
    test(`调色已发布缓存按${change}隔离`, async () => {
        const { state, resolve } = setup();
        const first = await resolve();
        if (change === "account") { state.userScope = "102"; state.epoch = 2; }
        if (change === "epoch") state.epoch = 3;
        const second = await resolve(change === "canvas" ? "canvas-two" : "canvas-one");
        assert.equal(state.uploads.length, 2);
        assert.notEqual(first.storageKey, second.storageKey);
    });
}

test("调色源解码期间切换账号，不上传旧账号像素", async () => {
    const { state, resolve } = setup();
    state.holdDecode = true;
    const pending = resolve();
    state.userScope = "102";
    state.epoch = 2;
    state.releaseDecode();
    await assert.rejects(pending, { name: "UserScopeAbandonedError" });
    assert.equal(state.uploads.length, 0);
});

test("上传期间切换账号，迟到回执不进入调色缓存或返回旧资源", async () => {
    const { state, resolve } = setup();
    let release;
    const ready = new Promise(value => { release = value; });
    state.upload = async () => ready;
    const pending = resolve();
    await new Promise(value => setImmediate(value));
    assert.equal(state.uploads.length, 1);
    state.epoch = 3;
    release();
    await assert.rejects(pending, { name: "UserScopeAbandonedError" });
    await resolve();
    assert.equal(state.uploads.length, 2);
});

test("解码等待期间输入对象原地变化，导出冻结的调色值，新参数另行上传", async () => {
    const { state, image, resolve } = setup();
    state.holdDecode = true;
    const pending = resolve();
    image.source.grade.brightness = 75;
    state.releaseDecode();
    await pending;
    assert.equal(state.filters[0], "brightness(50%) contrast(100%) saturate(100%) hue-rotate(0deg)");
    state.holdDecode = false;
    await resolve();
    assert.equal(state.uploads.length, 2);
    assert.equal(state.filters[1], "brightness(75%) contrast(100%) saturate(100%) hue-rotate(0deg)");
});

test("调色上传失败保留错误上下文，明确重试不复用失败缓存", async () => {
    const { state, resolve } = setup();
    state.upload = async () => { throw new Error("storage unavailable"); };
    await assert.rejects(resolve(), /调色参考图生成失败：storage unavailable/);
    state.upload = async () => {};
    await resolve();
    assert.equal(state.uploads.length, 2);
});
