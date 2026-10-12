import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";

// Execute the imported BeefTV request builder and host profile adapter. External
// storage/HTTP are controlled here; this verifies source DTOs, not a provider.
const stubs = {
    "@/services/file-storage": "export const getMediaBlob = async () => null; export const resolveMediaUrl = async () => '';",
    "@/services/image-storage": "export const getImageBlob = async () => null; export const resolveImageUrl = async () => ''; export const uploadImage = async () => { throw new Error('unexpected upload'); };",
    "@/lib/canvas/canvas-generation-task-sync": "export const imageMetadata = () => { throw new Error('unexpected task sync'); };",
    "@/lib/canvas/canvas-file-upload": "export const interruptFileUpload = () => { throw new Error('unexpected file upload'); };",
    "@/services/api/resources": "export const resourceIdFromStorageKey = key => /^resource:([1-9]\\d*)$/.exec(key || '')?.[1]; export const resourceStorageKey = id => 'resource:' + id; export const ownedResourceIdFromMediaRef = media => resourceIdFromStorageKey(media.storageKey); export const getResource = async () => { throw new Error('unexpected resource lookup'); }; export const uploadResourceFile = async () => { throw new Error('unexpected upload'); };",
    "@/services/api/task-center": "export const createGenerationTask = async () => { throw new Error('unexpected HTTP'); }; export const waitForGenerationTask = createGenerationTask;",
    "@/services/api/image": "export const buildBackendToolRequests = () => [];",
    "@/services/api/request": "export class ApiError extends Error {}",
    "@/stores/use-user-store": "export const useUserStore = Object.assign(() => undefined, { getState: () => ({ user: null }) });",
    "@/stores/use-plugin-store": "export const usePluginStore = { getState: () => ({ runtimeStatuses: { 'runninghub-workflow-provider': 'enabled' } }) };",
};
const bundled = await build({
    stdin: { contents: 'export * from "./src/services/api/generation-task.ts"; export * from "./src/services/host-model-config.ts"; export { buildGenerationConfig } from "./src/lib/canvas/canvas-project-generation.ts"; export { useConfigStore } from "./src/stores/use-config-store.ts";', resolveDir: fileURLToPath(new URL("..", import.meta.url)) },
    tsconfig: fileURLToPath(new URL("../tsconfig.json", import.meta.url)),
    bundle: true, write: false, format: "esm", platform: "node", target: "node22",
    plugins: [{ name: "external-boundaries", setup(builder) {
        builder.onResolve({ filter: /^@\// }, args => args.path in stubs ? { path: args.path, namespace: "controlled" } : undefined);
        builder.onLoad({ filter: /.*/, namespace: "controlled" }, args => ({ contents: stubs[args.path], loader: "js" }));
    } }],
});
const { runBackendGenerationTask, prepareBackendGenerationTask, backendProviderConfig, sourceModelConfig, parseBackendGenerationResult, buildGenerationConfig, logicalModelIDForConfig, useConfigStore } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);

test("旧节点metadata.model经宿主alias恢复，未知同名和能力不匹配选择保留不可用", () => {
    const value = sourceModelConfig({ row_version: "1", preferences: {}, models: [{ id: "9007199254740999", name: "文本", model_key: "current-model", provider: "fixture", service_type: "text", enabled: true, has_api_key: true, is_default: true, selection_aliases: ["old::legacy-model"] }] });
    const known = buildGenerationConfig(value, { id: "old-node", type: "text", metadata: { model: "old::legacy-model" } }, "text");
    assert.equal(known.model, "host-9007199254740999::current-model");
    for (const [mode, stored] of [["text", "deleted::current-model"], ["image", "old::legacy-model"]]) {
        const unknown = buildGenerationConfig(value, { id: "old-node", type: mode, metadata: { model: stored } }, mode);
        assert.equal(unknown.model, stored);
        assert.equal(logicalModelIDForConfig(unknown), mode === "image" ? "9007199254740999" : "");
        if (mode === "text") assert.equal(useConfigStore.getState().isAiConfigReady(unknown, unknown.model), false);
    }
    const empty = sourceModelConfig({ row_version: "1", preferences: {}, models: [] });
    assert.equal(buildGenerationConfig(empty, undefined, "text").model, "");
});

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

test("宿主工作流按稳定模型身份选择，原请求只发送身份和标量参数", async () => {
    const id = "9007199254740999";
    for (const mode of ["image", "video", "audio"]) {
        const profile = { protocol: `runninghub-workflow-${mode}`, runninghub_workflow: {
            kind: "workflow", workflow_id: "same-workflow", title: "工作流", workflow_json: {},
            fields: [{ nodeId: "1", fieldName: "cfg", fieldType: "NUMBER", fieldValue: 5 }],
        } };
        const config = sourceModelConfig({ row_version: "1", preferences: {}, models: [
            { id: "9007199254740998", name: "另一个条目", model_key: "same-workflow", service_type: mode, enabled: true, has_api_key: true, runtime_profile: profile },
            { id, name: "已选择条目", model_key: "same-workflow", service_type: mode, enabled: true, has_api_key: true, runtime_profile: profile },
        ] });
        assert.equal(config.channels.length, 0);
        assert.equal(config.runningHub.apiKey, "");
        const selected = buildGenerationConfig(config, { id: "config", type: "config", metadata: {
            workflowProvider: "runninghub", runningHubWorkflowId: "same-workflow", runningHubWorkflowKind: "workflow",
            runningHubModelId: id, workflowParameters: { "field:1:cfg": 7 },
        } }, mode);
        assert.equal(useConfigStore.getState().isAiConfigReady(selected, selected.model), true);
        for (const runningHub of [
            { ...selected.runningHub, enabled: false },
            { ...selected.runningHub, selectedLogicalModelId: "9007199254741000" },
            { ...selected.runningHub, selectedKind: "app" },
        ]) assert.equal(useConfigStore.getState().isAiConfigReady({ ...selected, runningHub }, selected.model), false);
        const body = await prepareBackendGenerationTask({ projectId: "canvas", mode, prompt: "生成内容", config: selected, metadata: { nodeId: "config" } });
        assert.equal(body.logicalModelId, id);
        assert.equal(body.input.config.videoGenerateAudio, selected.videoGenerateAudio);
        assert.equal(body.input.config.videoWatermark, selected.videoWatermark);
        assert.equal(body.input.config.videoArkPrivateAssetUpload, selected.videoArkPrivateAssetUpload);
        assert.deepEqual(body.input.execution, { provider: "runninghub", kind: "workflow", interfaceType: `runninghub-workflow-${mode}`, name: "工作流", workflowId: "same-workflow" });
        assert.deepEqual(body.input.metadata.workflowParameters, { "field:1:cfg": 7 });
        for (const key of ["apiKey", "baseUrl", "workflowJson", "workflowFields", "runningHubUploadApiKey", "secretKey"]) assert.equal(key in body.input.config, false);
        selected.runningHub.workflows.find(item => item.logicalModelId === id).hasApiKey = false;
        assert.equal(useConfigStore.getState().isAiConfigReady(selected, selected.model), false);
        await assert.rejects(prepareBackendGenerationTask({ mode, prompt: "生成内容", config: selected }), /保存积分 API Key/);
    }
});

function hostVideoConfig(runtimeProfile = null, preferences = {}) {
    const id = "9007199254740999";
    const model = `host-${id}::minimax-h3`;
    return sourceModelConfig({ row_version: "1", preferences: {
        model, videoModel: model, size: "16:9", videoSeconds: "5", vquality: "480p", ...preferences,
    }, models: [{ id, name: "离线视频配置", model_key: "minimax-h3", provider: "fixture",
        service_type: "video", enabled: true, has_api_key: true, runtime_profile: runtimeProfile }] });
}

function videoRuntimeProfile(protocol = "openai-videos", supported = false) {
    return { version: 1, api_format: "openai", protocol, capability_config: { version: 1, video: {
        duration: { selection: "range", min: 5, max: 15, step: 1, default: 5 },
        ratios: ["16:9", "9:16"], defaultRatio: "16:9",
        resolutions: ["480p", "720p"], defaultResolution: "480p",
        generateAudio: { supported, default: false }, watermark: { supported, default: false },
        operations: ["text_to_video", "image_to_video"], defaultOperation: "text_to_video",
    } } };
}

test("未配置运行协议的宿主视频保留工作区480与原分镜时长，省略未支持布尔字段", async () => {
    const config = hostVideoConfig();
    const before = structuredClone(config);
    const selected = buildGenerationConfig(config, { id: "target", type: "video", metadata: { seconds: "5", size: "16:9" } }, "video");
    const body = await prepareBackendGenerationTask({ projectId: "canvas", mode: "video", prompt: "几何块", config: selected, metadata: { nodeId: "target" } });
    assert.equal(selected.vquality, "480");
    assert.equal(body.input.config.vquality, "480");
    assert.equal(body.input.config.videoSeconds, "5");
    assert.equal(body.input.config.size, "16:9");
    assert.equal(body.logicalModelId, "9007199254740999");
    assert.deepEqual(body.input.capabilityOptions, {});
    for (const key of ["videoGenerateAudio", "videoWatermark", "videoArkPrivateAssetUpload"]) assert.equal(key in body.input.config, false);
    assert.deepEqual(config, before);
});

test("显式宿主能力默认值压制旧工作区值，节点明确选择仍优先", () => {
    const config = hostVideoConfig(videoRuntimeProfile(), { size: "9:16", videoSeconds: "10", vquality: "720" });
    const selected = buildGenerationConfig(config, { id: "target", type: "video" }, "video");
    assert.deepEqual([selected.size, selected.videoSeconds, selected.vquality], ["16:9", "5", "480"]);
    const explicit = buildGenerationConfig(config, { id: "target", type: "video", metadata: { size: "9:16", seconds: "10", vquality: "720p" } }, "video");
    assert.deepEqual([explicit.size, explicit.videoSeconds, explicit.vquality], ["9:16", "10", "720"]);
});

test("已有显式宿主协议仍使用协议默认值", () => {
    const config = hostVideoConfig({ version: 1, api_format: "openai", protocol: "openai-videos" });
    const selected = buildGenerationConfig(config, { id: "target", type: "video" }, "video");
    assert.deepEqual([selected.size, selected.videoSeconds, selected.vquality], ["16:9", "6", "720"]);
});

test("未配置运行协议的宿主图片仍使用模型1:1默认值，工作区16:9不覆盖", () => {
    const id = "9007199254740999";
    const model = `host-${id}::fixture-image`;
    const config = sourceModelConfig({ row_version: "1", preferences: { model, imageModel: model, size: "16:9" },
        models: [{ id, name: "离线图片配置", model_key: "fixture-image", provider: "fixture", service_type: "image",
            enabled: true, has_api_key: true, runtime_profile: null }] });
    const before = structuredClone(config);
    const selected = buildGenerationConfig(config, { id: "image", type: "image" }, "image");
    assert.equal(config.size, "16:9");
    assert.equal(selected.size, "1:1");
    assert.deepEqual(config, before);
});

test("普通视频未支持的输出开关省略，支持的false和true均保留", () => {
    for (const value of ["false", "true"]) {
        for (const supported of [false, true]) {
            const config = hostVideoConfig(videoRuntimeProfile("openai-videos", supported), {
                videoGenerateAudio: value, videoWatermark: value, videoArkPrivateAssetUpload: value,
            });
            const before = structuredClone(config);
            const result = backendProviderConfig(config, "video");
            for (const key of ["videoGenerateAudio", "videoWatermark"]) {
                assert.equal(key in result, supported);
                if (supported) assert.equal(result[key], value);
            }
            assert.equal("videoArkPrivateAssetUpload" in result, false);
            assert.deepEqual(config, before);
        }
    }
});

test("Ark素材开关仅发送给已声明的Ark视频协议", () => {
    for (const protocol of ["volcengine-ark-video", "volcengine-ark-agent-plan-video", "openai-videos"]) {
        for (const value of ["false", "true"]) {
            const config = hostVideoConfig(videoRuntimeProfile(protocol), { videoArkPrivateAssetUpload: value });
            const result = backendProviderConfig(config, "video");
            if (protocol.startsWith("volcengine-ark")) assert.equal(result.videoArkPrivateAssetUpload, value);
            else assert.equal("videoArkPrivateAssetUpload" in result, false);
        }
    }
});

test("非视频模式的原配置字段保留", () => {
    const config = hostVideoConfig();
    for (const mode of ["text", "image", "audio"]) {
        const result = backendProviderConfig(config, mode);
        for (const key of ["videoGenerateAudio", "videoWatermark", "videoArkPrivateAssetUpload"]) assert.equal(result[key], config[key]);
    }
});

test("已有本地协议普通视频沿所选能力过滤，不改变渠道与协议身份", () => {
    const hosted = hostVideoConfig(videoRuntimeProfile());
    const channel = { ...hosted.channels[0], id: "local", scope: "user", credentialRef: undefined, baseUrl: "https://fixture.invalid/v1",
        modelProfiles: [{ ...hosted.channels[0].modelProfiles[0], logicalModelId: undefined }] };
    const config = { ...hosted, hostModelDirectory: false, channels: [channel], model: "local::minimax-h3" };
    const result = backendProviderConfig(config, "video");
    assert.equal(result.channelId, "");
    assert.equal(result.baseUrl, "https://fixture.invalid/v1");
    assert.equal(result.interfaceType, "openai-videos");
    assert.equal(result.model, "minimax-h3");
    for (const key of ["videoGenerateAudio", "videoWatermark", "videoArkPrivateAssetUpload"]) assert.equal(key in result, false);
});
