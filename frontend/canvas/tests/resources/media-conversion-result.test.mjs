import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";

// 执行实际归档helper；只替换上传与账号边界，不代表真实MinIO或Framefield模型验收。
const stubs = {
    "@/services/api/resources": `
        export const resourceStorageKey = id => 'resource:' + id;
        export const resourceFileUrl = id => '/api/v1/canvas-runtime/resources/' + id + '/file';
        export const uploadResourceFile = (...args) => globalThis.conversionBoundary.upload(...args);
    `,
    "@/lib/user-scope-guard": `
        export const assertUserScope = expected => {
            if (expected !== globalThis.conversionBoundary.scope) {
                const error = new Error('账号已切换'); error.name = 'UserScopeAbandonedError'; throw error;
            }
        };
        export const isUserScopeAbandonedError = error => error?.name === 'UserScopeAbandonedError';
    `,
};
const bundled = await build({
    entryPoints: [fileURLToPath(new URL("../../src/services/media-conversion-result.ts", import.meta.url))],
    tsconfig: fileURLToPath(new URL("../../tsconfig.json", import.meta.url)),
    bundle: true, write: false, format: "esm", platform: "node", target: "node22",
    plugins: [{ name: "conversion-boundaries", setup(builder) {
        builder.onResolve({ filter: /^@\// }, args => args.path in stubs ? { path: args.path, namespace: "controlled" } : undefined);
        builder.onLoad({ filter: /.*/, namespace: "controlled" }, args => ({ contents: stubs[args.path], loader: "js" }));
    } }],
});
const { persistMediaConversionResult } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);

function setup() {
    const scope = { userScope: "9007199254740999", epoch: 1 };
    const result = { blob: new Blob(["converted-png"], { type: "image/png" }), width: 64, height: 48 };
    const uploads = [];
    let active = true;
    const options = {
        nodeId: "converter", sourceFingerprint: "v1-source", operation: "depth", hostCanvasKey: "canvas-A",
        expectedScope: scope,
        assertActive: () => { if (!active) throw new DOMException("来源已变化", "AbortError"); },
    };
    globalThis.conversionBoundary = {
        scope,
        upload: async (...args) => {
            uploads.push(args);
            return { id: "18446744073709551610", status: "ready", kind: "image", mimeType: "image/png", size: result.blob.size, width: 64, height: 48 };
        },
    };
    return { options, result, uploads, stop: () => { active = false; } };
}

test("转换结果先永久归档，原字节、尺寸、scope和画布均冻结，同输入输出重放原上传键", async () => {
    const fixture = setup();
    const saved = await persistMediaConversionResult(fixture.result, fixture.options);
    assert.equal(saved.storageKey, "resource:18446744073709551610");
    assert.equal(saved.url, "/api/v1/canvas-runtime/resources/18446744073709551610/file");
    assert.equal(fixture.uploads[0][0], fixture.result.blob);
    assert.equal(fixture.uploads[0][1], "image");
    const meta = fixture.uploads[0][2];
    assert.equal(meta.expectedScope, fixture.options.expectedScope);
    assert.equal(meta.hostCanvasKey, "canvas-A");
    assert.deepEqual([meta.width, meta.height], [64, 48]);
    assert.match(meta.idempotencyKey, /^media-conversion:[a-f0-9]{64}$/);
    await persistMediaConversionResult(fixture.result, fixture.options);
    assert.equal(fixture.uploads[1][2].idempotencyKey, meta.idempotencyKey);
    await persistMediaConversionResult(fixture.result, { ...fixture.options, hostCanvasKey: "canvas-B" });
    assert.notEqual(fixture.uploads[2][2].idempotencyKey, meta.idempotencyKey);
    await persistMediaConversionResult({ ...fixture.result, blob: new Blob(["different-png"], { type: "image/png" }) }, fixture.options);
    assert.notEqual(fixture.uploads[3][2].idempotencyKey, meta.idempotencyKey);
});

test("输出哈希读取期间换来源，不能开始上传", async () => {
    const fixture = setup();
    const original = fixture.result.blob.arrayBuffer.bind(fixture.result.blob);
    fixture.result.blob.arrayBuffer = async () => { fixture.stop(); return original(); };
    await assert.rejects(persistMediaConversionResult(fixture.result, fixture.options), { name: "AbortError" });
    assert.equal(fixture.uploads.length, 0);
});

for (const change of ["source", "account"]) {
    test(`上传等待期间${change}变化，旧成功响应不能回填`, async () => {
        const fixture = setup();
        const upload = globalThis.conversionBoundary.upload;
        globalThis.conversionBoundary.upload = async (...args) => {
            const resource = await upload(...args);
            if (change === "source") fixture.stop();
            else globalThis.conversionBoundary.scope = { ...fixture.options.expectedScope, epoch: 2 };
            return resource;
        };
        await assert.rejects(persistMediaConversionResult(fixture.result, fixture.options), { name: change === "source" ? "AbortError" : "UserScopeAbandonedError" });
    });
}

test("归档失败不返回本机键，明确保留保存失败供原节点重试", async () => {
    const fixture = setup();
    globalThis.conversionBoundary.upload = async () => { throw new Error("HTTP 503"); };
    await assert.rejects(persistMediaConversionResult(fixture.result, fixture.options), /转换结果保存失败.*503/);
});

for (const patch of [{ status: "pending" }, { width: 99 }, { mimeType: "image/jpeg" }, { id: "bad" }]) {
    test(`不接受不完整或不匹配归档回执${JSON.stringify(patch)}`, async () => {
        const fixture = setup();
        const upload = globalThis.conversionBoundary.upload;
        globalThis.conversionBoundary.upload = async (...args) => ({ ...await upload(...args), ...patch });
        await assert.rejects(persistMediaConversionResult(fixture.result, fixture.options), /转换结果.*回执/);
    });
}
