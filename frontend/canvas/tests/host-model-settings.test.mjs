import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";

const sessionFile = fileURLToPath(new URL("../src/lib/user-session.ts", import.meta.url));
const sessionImports = new Map([...readFileSync(sessionFile, "utf8").matchAll(/import \{([^}]+)\} from "([^"]+)";/g)].map(([, names, path]) => [path, names.split(",").map(name => name.trim()).filter(name => !name.startsWith("type "))]));
const stubs = {
    "@/stores/use-user-store": "export const useUserStore = Object.assign(() => undefined, { getState: () => ({ user: null }) }); export const defaultFeatureAvailability = {};",
    "@/services/api/workspace": "export const getLocalModelConfig = async () => { throw new Error('unexpected read'); }; export const saveLocalModelConfig = getLocalModelConfig;",
    "@/services/host-session": "export const getAuthenticatedCanvasBootstrap = async () => { throw new Error('unexpected bootstrap'); };",
    "@/services/api/generation-task": "export const prepareBackendGenerationTask = options => globalThis.modelTestBoundary.prepare(options); export const runBackendGenerationTask = () => { throw new Error('canvas task must not be submitted'); };",
    "@/services/api/request": "export class ApiError extends Error { constructor(message, options = {}) { super(message); Object.assign(this, options); } } export const http = { post: (...args) => globalThis.modelTestBoundary.post(...args), put: (...args) => globalThis.modelTestBoundary.put(...args), get: (...args) => globalThis.modelTestBoundary.get(...args) };",
    "@/lib/user-scope-guard": "export const captureUserScope = () => ({...globalThis.modelTestBoundary.scope}); export const assertUserScope = expected => { const live = captureUserScope(); if (live.userScope !== expected.userScope || live.epoch !== expected.epoch) throw new Error('账号已切换'); };",
};
const bundled = await build({
    stdin: { contents: 'export * from "./src/services/host-model-config.ts"; export * from "./src/services/host-model-config-ack.ts"; export * from "./src/services/model-config-repository.ts"; export * from "./src/services/host-model-test.ts"; export * from "./src/lib/user-session.ts"; export * from "./src/lib/model-connection-test.ts"; export * from "./src/stores/use-model-connection-tests.ts"; export * from "./src/lib/assistant-model.ts"; export { saveLocalModelConfig } from "./src/services/api/workspace.ts"; export { defaultConfig, normalizeConfigSnapshot, normalizeModelOptionValue, resolveModelChannel, logicalModelIDForConfig, resolveModelRequestConfig, isBuiltinBeefAPIChannel, useConfigStore } from "./src/stores/use-config-store.ts"; export { setActiveUserScope } from "./src/lib/user-scope.ts"; export { ApiError } from "@/services/api/request";', resolveDir: fileURLToPath(new URL("..", import.meta.url)) },
    tsconfig: fileURLToPath(new URL("../tsconfig.json", import.meta.url)), bundle: true, write: false, format: "esm", platform: "node", target: "node22",
    plugins: [{ name: "external-boundaries", setup(builder) {
        builder.onResolve({ filter: /^@\// }, args => {
            if (args.path in stubs) return { path: args.path, namespace: "controlled" };
            if (args.importer === sessionFile && args.path !== "@/stores/use-config-store") return { path: args.path, namespace: "session-boundary" };
        });
        builder.onLoad({ filter: /.*/, namespace: "controlled" }, args => ({ contents: stubs[args.path], loader: "js" }));
        builder.onLoad({ filter: /.*/, namespace: "session-boundary" }, args => ({ contents: sessionImports.get(args.path).map(name => `export const ${name} = {};`).join("\n"), loader: "js" }));
    } }],
});
const { sourceModelConfig, modelPreferences, defaultConfig, normalizeConfigSnapshot, normalizeModelOptionValue, resolveModelChannel, logicalModelIDForConfig, resolveModelRequestConfig, isBuiltinBeefAPIChannel, assistantModelOptions, saveLocalModelConfig, useConfigStore, localWorkspaceConfig, reconcileHostModelConfigAck, createModelConfigRepository, runHostModelTest, testChannelModelConnection, useModelConnectionTests, currentModelConnectionReceipt, setActiveUserScope, ApiError } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);
const channel = (id = "personal") => ({ id, name: "个人服务", baseUrl: "https://provider.example/v1", apiKey: "draft-key", secretKey: "draft-secret", apiFormat: "openai", enabled: true, models: ["model"], modelProfiles: [{ model: "model", capability: "text", protocol: "chat-completion" }] });
const config = () => normalizeConfigSnapshot({ config: { ...defaultConfig, textModel: "personal::model", model: "personal::model", channels: [channel()] } }).config;
const defer = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const tick = () => new Promise(resolve => setImmediate(resolve));

const hostModel = (fields = {}) => ({ id: "9007199254740999", name: "宿主文本", model_key: "current-model", provider: "openai", service_type: "text", enabled: true, has_api_key: true, is_default: true,
    selection_aliases: ["personal::old-model"], ...fields });

test("宿主目录唯一投影，恢复旧默认与节点alias并保留协议能力和逻辑ID", () => {
    const runtime = { version: 1, api_format: "claude", protocol: "claude-api", reference_asset_origin: "https://reference.example", concurrency_limit: 3,
        capability_config: { text: { streaming: false, thinking: true } }, default_options: { thinking: true }, logical_capability_spec: { options: {} }, logical_capability_profiles: [{ options: {} }], video_capabilities_version: "fixture-v1" };
    const value = sourceModelConfig({ row_version: "1", preferences: { textModel: "personal::old-model", model: "personal::old-model", assistantModel: "personal::old-model" }, models: [hostModel({ runtime_profile: runtime, has_secret_key: true, headers: [{ name: "X-Private", has_value: true }] })], channels: [channel()] });
    const canonical = "host-9007199254740999::current-model";
    assert.equal(value.channels.length, 1);
    assert.equal(value.textModel, canonical);
    assert.equal(value.assistantModel, canonical);
    assert.equal(normalizeModelOptionValue("personal::old-model", value.channels), canonical);
    assert.equal(normalizeModelOptionValue("host-9007199254740999::renamed-model", value.channels), canonical);
    assert.equal(value.channels[0].modelProfiles[0].logicalModelId, "9007199254740999");
    assert.deepEqual(value.channels[0].modelProfiles[0].capabilityConfig, runtime.capability_config);
    assert.deepEqual(value.channels[0].modelProfiles[0].defaultOptions, runtime.default_options);
    assert.deepEqual(value.channels[0].modelProfiles[0].logicalCapabilityProfiles, runtime.logical_capability_profiles);
    assert.equal(value.channels[0].concurrencyLimit, 3);
    assert.equal(value.channels[0].referenceAssetOrigin, runtime.reference_asset_origin);
    assert.equal(resolveModelRequestConfig(value, canonical).interfaceType, "claude-api");
    assert.equal(resolveModelRequestConfig(value, "personal::old-model").interfaceType, "claude-api");
    assert.equal(logicalModelIDForConfig({ ...value, model: "personal::old-model" }), "9007199254740999");
    assert.equal(value.channels[0].apiKey, "");
    assert.deepEqual(value.channels[0].headers, [{ name: "X-Private", value: "" }]);
});

test("未知旧渠道即使模型同名也不解析到首个宿主模型或取得逻辑ID", () => {
    const value = sourceModelConfig({ row_version: "1", preferences: { model: "deleted::current-model" }, models: [hostModel()] });
    assert.equal(resolveModelChannel(value, value.model).enabled, false);
    assert.equal(logicalModelIDForConfig(value), "");
    assert.equal(resolveModelRequestConfig(value, value.model).credentialRef, undefined);
    assert.equal(useConfigStore.getState().isAiConfigReady(value, value.model), false);
});

test("可信宿主企业凭据来源保留源助手白名单，名称和地址不能伪装内置目录", () => {
    const models = [hostModel({ model_key: "gpt-6-astra", credential_source: "beefapi" }), hostModel({ id: "22", model_key: "other-text", credential_source: "beefapi" }), hostModel({ id: "23", model_key: "manual-text", name: "BeefAPI", credential_source: "manual" })];
    const value = sourceModelConfig({ row_version: "1", preferences: {}, models });
    assert.equal(value.channels[0].credentialSource, "beefapi");
    assert.equal(isBuiltinBeefAPIChannel(value.channels[0]), true);
    assert.equal(isBuiltinBeefAPIChannel(value.channels[2]), false);
    assert.equal(isBuiltinBeefAPIChannel({ id: "personal", scope: "user", name: "BeefAPI", baseUrl: "https://enterprise.beefapi.com", credentialSource: "beefapi" }), false);
    assert.deepEqual(assistantModelOptions(value), ["host-9007199254740999::gpt-6-astra", "host-23::manual-text"]);
});

test("真实工作区PUT仅传个人偏好与版本，源渠道及密钥不会进入HTTP正文", async () => {
    const value = sourceModelConfig({ row_version: "7", preferences: {}, models: [hostModel()] });
    value.channels.push(channel());
    const requests = [];
    boundary({ put: async (...args) => { requests.push(args); return { row_version: "8", preferences: args[1].preferences, models: [hostModel()] }; } });
    const acknowledged = await saveLocalModelConfig(value, "7");
    assert.equal(requests[0][0], "/workspace/model-config");
    assert.deepEqual(Object.keys(requests[0][1]).sort(), ["expected_row_version", "preferences"]);
    assert.equal(requests[0][1].expected_row_version, "7");
    assert.equal(JSON.stringify(requests[0][1]).includes("draft-key"), false);
    assert.equal(acknowledged.revision, "8");
    assert.deepEqual(acknowledged.config.channels.map(item => item.id), ["host-9007199254740999"]);
});

test("无法解析、禁用及空目录的显式选择保留且不能就绪，不随机采用其他模型", () => {
    for (const models of [[hostModel()], [hostModel({ enabled: false })], []]) {
        const value = sourceModelConfig({ row_version: "1", preferences: { textModel: "deleted::model", model: "deleted::model", assistantModel: "deleted::model" }, models });
        assert.equal(value.textModel, "deleted::model");
        assert.equal(value.model, "deleted::model");
        assert.equal(value.assistantModel, "deleted::model");
        assert.equal(useConfigStore.getState().isAiConfigReady(value, value.model), false);
    }
    const noDefault = sourceModelConfig({ row_version: "1", preferences: {}, models: [hostModel({ is_default: false })] });
    assert.equal(noDefault.textModel, "");
    const withDefault = sourceModelConfig({ row_version: "1", preferences: {}, models: [hostModel()] });
    assert.equal(withDefault.textModel, "host-9007199254740999::current-model");
});

test("画布仅保存个人偏好，ACK采用宿主只读目录且保留保存期间的新偏好", () => {
    const submitted = sourceModelConfig({ row_version: "1", preferences: {}, models: [hostModel()] });
    const current = structuredClone(submitted);
    current.audioSpeed = "1.5";
    current.channels.push(channel());
    const durable = sourceModelConfig({ row_version: "2", preferences: {}, models: [hostModel({ model_key: "renamed-model" })] });
    const acknowledged = reconcileHostModelConfigAck(submitted, current, durable);
    assert.deepEqual(acknowledged.channels, durable.channels);
    assert.equal(acknowledged.audioSpeed, "1.5");
    const preferences = modelPreferences(acknowledged);
    assert.equal(preferences.audioSpeed, "1.5");
    for (const key of ["channels", "apiKey", "apiFormat", "runningHub", "hostModelDirectory"]) assert.equal(key in preferences, false);
});

test("宿主模型经真实工作区恢复保留选择与逻辑ID，同时剔除源旧系统代理", () => {
    const id = "9007199254740999";
    const source = sourceModelConfig({ row_version: "1", preferences: { textModel: `host-${id}::gpt-fixture` }, models: [{ id, name: "宿主文本", model_key: "gpt-fixture", service_type: "text", enabled: true, has_api_key: true }], channels: [channel()] });
    source.channels.push({ ...channel("old-system"), scope: "system" });
    const restored = localWorkspaceConfig(source);
    assert.equal(restored.textModel, `host-${id}::gpt-fixture`);
    assert.equal(restored.channels[0].modelProfiles[0].logicalModelId, id);
    assert.equal(restored.channels[0].scope, "system");
    assert.deepEqual(restored.channels.map(item => item.id), [`host-${id}`]);
});

test("保存ACK采用真实逻辑ID与脱敏结果", () => {
    const submitted = config();
    const durable = structuredClone(submitted);
    Object.assign(durable.channels[0], { apiKey: "", secretKey: "", hasApiKey: true, hasSecretKey: true, credentialRef: "host:9007199254740999" });
    durable.channels[0].modelProfiles[0].logicalModelId = "9007199254740999";
    const acknowledged = reconcileHostModelConfigAck(submitted, submitted, durable);
    assert.equal(acknowledged.channels[0].apiKey, "");
    assert.equal(acknowledged.channels[0].secretKey, "");
    assert.equal(acknowledged.channels[0].modelProfiles[0].logicalModelId, "9007199254740999");
});

test("ACK不覆盖保存期间的新密钥、能力、偏好，亦不复活已删除渠道", () => {
    const submitted = config();
    submitted.channels.push(channel("deleted"));
    const durable = structuredClone(submitted);
    durable.channels[0].apiKey = "";
    durable.channels[0].modelProfiles[0].logicalModelId = "9007199254740999";
    const current = structuredClone(submitted);
    current.channels[0].apiKey = "new-draft-key";
    current.channels[0].modelProfiles[0].displayName = "新名称";
    current.channels = [current.channels[0], channel("new")];
    current.audioSpeed = "1.5";
    const acknowledged = reconcileHostModelConfigAck(submitted, current, durable);
    assert.equal(acknowledged.channels[0].apiKey, "new-draft-key");
    assert.equal(acknowledged.channels[0].modelProfiles[0].displayName, "新名称");
    assert.equal(acknowledged.channels[0].modelProfiles[0].logicalModelId, "9007199254740999");
    assert.equal(acknowledged.audioSpeed, "1.5");
    assert.deepEqual(acknowledged.channels.map(item => item.id), ["personal", "new"]);
});

test("串行保存先接收ACK再以新版本保存并发输入，没有额外保存回声", async () => {
    const firstAck = defer();
    const writes = [];
    const repository = createModelConfigRepository({ read: async () => ({ config: config(), revision: "7" }), write: async (body, revision) => {
        writes.push({ body: structuredClone(body), revision });
        if (writes.length === 1) return firstAck.promise;
        return { saved: true, revision: "9", config: body };
    }, acknowledge: reconcileHostModelConfigAck });
    await repository.hydrate();
    const first = config();
    const saving = repository.commit(first);
    const second = structuredClone(first);
    second.channels[0].apiKey = "new-draft-key";
    void repository.commit(second);
    const durable = structuredClone(first);
    durable.channels[0].apiKey = "";
    durable.channels[0].modelProfiles[0].logicalModelId = "9007199254740999";
    firstAck.resolve({ saved: true, revision: "8", config: durable });
    await saving;
    await repository.flush();
    assert.equal(writes.length, 2);
    assert.deepEqual(writes.map(item => item.revision), ["7", "8"]);
    assert.equal(writes[1].body.channels[0].apiKey, "new-draft-key");
    assert.equal(writes[1].body.channels[0].modelProfiles[0].logicalModelId, "9007199254740999");
    assert.equal(repository.getState().dirty, false);
});

test("409保留原草稿与版本，不自动覆盖或重试", async () => {
    let calls = 0;
    const repository = createModelConfigRepository({ read: async () => ({ config: config(), revision: "7" }), write: async () => { calls++; throw new ApiError("保存冲突", { status: 409 }); } });
    await repository.hydrate();
    await repository.commit(config());
    await repository.flush();
    await repository.commit(config());
    await tick();
    assert.equal(calls, 1);
    assert.deepEqual(repository.getState(), { status: "error", revision: "7", dirty: true, error: "保存冲突" });
});

function boundary(overrides = {}) {
    globalThis.modelTestBoundary = { scope: { userScope: "account-A", epoch: 1 }, prepare: async () => ({ input: { config: { count: "1", baseUrl: "private", apiKey: "private", secretKey: "private" }, textOptions: { stream: false, thinking: false } } }), post: async () => ({ id: "9007199254740999", status: "succeeded", result: { mode: "text", text: "OK" }, canCancel: false }), get: async () => { assert.fail("unexpected polling"); }, ...overrides };
    return globalThis.modelTestBoundary;
}

test("clean企业目录刷新静默采用真实版本，下次显式输入使用新revision", async () => {
    let reads = 0;
    const writes = [];
    let applied;
    const repository = createModelConfigRepository({ read: async () => ({ config: config(), revision: ++reads === 1 ? "7" : "9" }), applyRefresh: config => { applied = config; }, write: async (body, revision) => { writes.push(revision); return { saved: true, revision: "10", config: body }; } });
    await repository.hydrate();
    await repository.refresh();
    assert.ok(applied);
    assert.equal(repository.getState().revision, "9");
    assert.deepEqual(writes, []);
    await repository.commit(config());
    assert.deepEqual(writes, ["9"]);
});

test("企业目录刷新拒绝409草稿，不能自动读取新版本再提交覆盖", async () => {
    let reads = 0;
    let writes = 0;
    const repository = createModelConfigRepository({ read: async () => { reads++; return { config: config(), revision: "7" }; }, write: async () => { writes++; throw new ApiError("保存冲突", { status: 409 }); }, applyRefresh: () => assert.fail("must preserve draft") });
    await repository.hydrate();
    await repository.commit(config());
    await assert.rejects(repository.refresh(), /未保存输入或冲突/);
    await repository.flush();
    assert.equal(reads, 1);
    assert.equal(writes, 1);
    assert.equal(repository.getState().revision, "7");
    assert.equal(repository.getState().dirty, true);
});

test("两个clean目录GET逆序完成时旧快照不能回退已应用revision", async () => {
    const oldRead = defer();
    const newRead = defer();
    const applied = [];
    let reads = 0;
    const repository = createModelConfigRepository({ read: async () => ++reads === 1 ? { config: config(), revision: "7" } : reads === 2 ? oldRead.promise : newRead.promise, write: async () => assert.fail("refresh must not submit"), applyRefresh: current => applied.push(current) });
    await repository.hydrate();
    const older = repository.refresh();
    const newer = repository.refresh();
    newRead.resolve({ config: config(), revision: "9" });
    await newer;
    oldRead.resolve({ config: config(), revision: "8" });
    await assert.rejects(older, /未保存输入或冲突/);
    assert.equal(repository.getState().revision, "9");
    assert.equal(applied.length, 1);
});

test("目录读取期间新增输入保持草稿，旧server快照不能覆盖或更新其版本", async () => {
    const fresh = defer();
    const saving = defer();
    let reads = 0;
    const repository = createModelConfigRepository({ read: async () => ++reads === 1 ? { config: config(), revision: "7" } : fresh.promise, write: async () => saving.promise, applyRefresh: () => assert.fail("must preserve newer input") });
    await repository.hydrate();
    const refreshing = repository.refresh();
    const committed = repository.commit(config());
    fresh.resolve({ config: config(), revision: "9" });
    await assert.rejects(refreshing, /未保存输入或冲突/);
    assert.equal(repository.getState().revision, "7");
    saving.resolve({ saved: true, revision: "8" });
    await committed;
});

test("目录刷新等待drain后及GET后检查账号epoch，不读取或应用另一账号", async () => {
    for (const abandon of ["drain", "read"]) {
        let active = true;
        let reads = 0;
        const waiting = defer();
        const repository = createModelConfigRepository({ read: async () => ++reads === 1 ? { config: config(), revision: "7" } : waiting.promise, write: async () => waiting.promise, applyRefresh: () => assert.fail("must not apply another account") });
        await repository.hydrate();
        const committed = abandon === "drain" ? repository.commit(config()) : null;
        const refreshing = repository.refresh(undefined, () => { if (!active) throw new Error("账号已切换"); });
        active = false;
        waiting.resolve(abandon === "drain" ? { saved: true, revision: "8" } : { config: config(), revision: "9" });
        await assert.rejects(refreshing, /账号已切换/);
        if (committed) await committed;
        assert.equal(reads, abandon === "drain" ? 1 : 2);
    }
});

test("模型测试只提交本人独立任务，临时连接留在POST且安全生成config不含密钥", async () => {
    const calls = [];
    boundary({ post: async (...args) => { calls.push(args); return { id: "9007199254740999", status: "succeeded", result: { mode: "text", text: "OK" } }; } });
    assert.equal((await runHostModelTest({ mode: "text", config: config(), prompt: "OK" })).text, "OK");
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], "/model-tests");
    const [url, body, request] = calls[0];
    assert.equal(body.channel.apiKey, "draft-key");
    assert.equal(body.model, "model");
    assert.equal(body.clientOperationId, request.headers["Idempotency-Key"]);
    assert.deepEqual(body.config, { count: "1" });
    assert.equal("projectId" in body, false);
});

test("受理不明重放相同正文与幂等键，继续观察真实原任务", async () => {
    const calls = [];
    boundary({ post: async (...args) => { calls.push(args); if (calls.length === 1) throw new ApiError("响应丢失", { status: 503 }); return { id: "9007199254740999", status: "succeeded", result: { mode: "text", text: "OK" } }; } });
    await runHostModelTest({ mode: "text", config: config(), prompt: "replay" });
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[0][1], calls[1][1]);
    assert.equal(calls[0][2].headers["Idempotency-Key"], calls[1][2].headers["Idempotency-Key"]);
});

test("409及程序异常均不自动提交第二次模型测试", async () => {
    for (const error of [new ApiError("conflict", { status: 409 }), new TypeError("invalid code")]) {
        let calls = 0;
        boundary({ post: async () => { calls++; throw error; } });
        await assert.rejects(runHostModelTest({ mode: "text", config: config(), prompt: error.message }), thrown => thrown === error);
        assert.equal(calls, 1);
    }
});

test("无效模型测试回执不能当成成功或触发第二次供应商提交", async () => {
    for (const receipt of [{ id: 42, status: "succeeded" }, { id: "0", status: "succeeded" }, { id: "42", status: "unknown" }]) {
        let calls = 0;
        boundary({ post: async () => { calls++; return receipt; } });
        await assert.rejects(runHostModelTest({ mode: "text", config: config(), prompt: "invalid receipt" }), /模型测试回执无效/);
        assert.equal(calls, 1);
    }
});

test("账号 A→B→A 后不复用旧临时密钥任务的幂等身份", async () => {
    const calls = [];
    const env = boundary({ post: async (...args) => { calls.push(args); throw new ApiError("响应丢失", { status: 503 }); } });
    const options = { mode: "text", config: config(), prompt: "epoch" };
    await assert.rejects(runHostModelTest(options));
    env.scope.epoch = 3;
    env.post = async (...args) => { calls.push(args); return { id: "9007199254740999", status: "succeeded", result: { mode: "text", text: "OK" } }; };
    await runHostModelTest(options);
    assert.notEqual(calls[0][2].headers["Idempotency-Key"], calls.at(-1)[2].headers["Idempotency-Key"]);
});

test("账号变化或观察取消后不使用旧响应、不发起取消供应商请求", async () => {
    for (const abandon of ["account", "abort"]) {
        const accepted = defer();
        const controller = new AbortController();
        let calls = 0;
        const env = boundary({ post: async () => { calls++; return accepted.promise; } });
        const watching = runHostModelTest({ mode: "text", config: config(), prompt: abandon, signal: controller.signal });
        await tick();
        if (abandon === "account") env.scope.epoch++;
        else controller.abort();
        accepted.resolve({ id: "9007199254740999", status: "succeeded", result: { mode: "text", text: "old" } });
        await assert.rejects(watching, abandon === "account" ? /账号已切换/ : { name: "AbortError" });
        assert.equal(calls, 1);
    }
});

test("源模型测试保留注入执行器与逻辑ID，空供应商结果不能标为测试通过", async () => {
    const active = channel();
    active.modelProfiles[0].logicalModelId = "9007199254740999";
    const runTask = async options => { assert.equal(options.config.channels[0].modelProfiles[0].logicalModelId, "9007199254740999"); return { mode: "text", text: "OK" }; };
    assert.equal(await testChannelModelConnection(active, "model", "text", "chat-completion", runTask), "已收到文本响应");
    await assert.rejects(testChannelModelConnection(active, "model", "text", "chat-completion", async () => ({ mode: "text", text: " " })), /没有返回可用结果/);
});

test("模型测试回执只属于当前账号会话，切换账号立即清空且A→B→A不恢复旧凭据证据", () => {
    const active = channel();
    setActiveUserScope("receipt-A");
    useModelConnectionTests.getState().record(active, "model", { success: true, detail: "已收到文本响应" });
    const previous = useModelConnectionTests.getState().receipts;
    assert.equal(currentModelConnectionReceipt(previous, active, "model").success, true);
    setActiveUserScope("receipt-B");
    assert.deepEqual(useModelConnectionTests.getState().receipts, {});
    assert.equal(currentModelConnectionReceipt(previous, active, "model"), undefined);
    setActiveUserScope("receipt-A");
    assert.equal(currentModelConnectionReceipt(previous, active, "model"), undefined);
    setActiveUserScope("guest");
});
