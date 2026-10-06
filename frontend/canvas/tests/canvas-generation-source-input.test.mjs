import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";

// Execute the imported BeefTV request builder and host profile adapter. External
// storage/HTTP are controlled here; this verifies source DTOs, not a provider.
const stubs = {
    "@/services/file-storage": "export const getMediaBlob = async () => null;",
    "@/services/image-storage": "export const getImageBlob = async () => null;",
    "@/services/api/resources": "export const resourceIdFromStorageKey = key => /^resource:([1-9]\\d*)$/.exec(key || '')?.[1]; export const resourceStorageKey = id => 'resource:' + id; export const ownedResourceIdFromMediaRef = media => resourceIdFromStorageKey(media.storageKey); export const getResource = async () => { throw new Error('unexpected resource lookup'); }; export const uploadResourceFile = async () => { throw new Error('unexpected upload'); };",
    "@/services/api/task-center": "export const createGenerationTask = async () => { throw new Error('unexpected HTTP'); }; export const waitForGenerationTask = createGenerationTask;",
    "@/services/api/image": "export const buildBackendToolRequests = () => [];",
    "@/services/api/request": "export class ApiError extends Error {}",
    "@/stores/use-user-store": "export const useUserStore = Object.assign(() => undefined, { getState: () => ({ user: null }) });",
};
const bundled = await build({
    stdin: { contents: 'export * from "./src/services/api/generation-task.ts"; export * from "./src/services/host-model-config.ts";', resolveDir: fileURLToPath(new URL("..", import.meta.url)) },
    tsconfig: fileURLToPath(new URL("../tsconfig.json", import.meta.url)),
    bundle: true, write: false, format: "esm", platform: "node", target: "node22",
    plugins: [{ name: "external-boundaries", setup(builder) {
        builder.onResolve({ filter: /^@\// }, args => args.path in stubs ? { path: args.path, namespace: "controlled" } : undefined);
        builder.onLoad({ filter: /.*/, namespace: "controlled" }, args => ({ contents: stubs[args.path], loader: "js" }));
    } }],
});
const { runBackendGenerationTask, sourceModelConfig, parseBackendGenerationResult } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);

test("真实源请求构造器从宿主模型读取十进制logicalModelId并发送四类安全camel输入", async () => {
    for (const mode of ["text", "image", "video", "audio"]) {
        const logicalModelId = "9007199254740999";
        const config = sourceModelConfig({ row_version: "1", preferences: { model: `host-${logicalModelId}::fixture-model` }, models: [{ id: logicalModelId, name: "模型", model_key: "fixture-model", provider: "fixture", service_type: mode, enabled: true, has_api_key: true }] });
        const captured = [];
        const result = await runBackendGenerationTask({
            projectId: "canvas-key", mode, prompt: "生成内容", config,
            metadata: { nodeId: "target", sourceNodeId: "source" },
        }, {
            createId: () => `source-operation-${mode}`,
            createTask: async (body, requestConfig) => {
                captured.push({ body: JSON.parse(JSON.stringify(body)), requestConfig });
                return { id: "9007199254741001", status: "queued" };
            },
            waitTask: async () => ({ id: "9007199254741001", status: "succeeded", resultJson: JSON.stringify({ mode, text: mode === "text" ? "结果" : undefined }) }),
        });
        assert.equal(result.mode, mode);
        const body = captured[0].body;
        assert.equal(body.logicalModelId, logicalModelId);
        assert.equal(body.projectId, "canvas-key");
        assert.equal(body.type, `canvas_${mode}`);
        assert.equal(body.input.metadata.clientOperationId, `source-operation-${mode}`);
        assert.deepEqual(body.input.referenceImages, []);
        assert.deepEqual(body.input.referenceVideos, []);
        assert.deepEqual(body.input.referenceAudios, []);
        assert.deepEqual(body.input.capabilityOptions, {});
        for (const key of ["apiKey", "secretKey", "credentialRef", "baseUrl", "channelId", "interfaceType"]) assert.equal(key in body.input.config, false);
        if (mode === "text") assert.deepEqual(body.input.textOptions, { stream: true, thinking: false });
    }
});

test("真实源解析器消费后端文本resultJson，缺少结果不能直接进入回填", () => {
    assert.equal(parseBackendGenerationResult({ resultJson: JSON.stringify({ mode: "text", text: "已生成" }) }).text, "已生成");
    assert.throws(() => parseBackendGenerationResult({}), /没有返回结果/);
});
