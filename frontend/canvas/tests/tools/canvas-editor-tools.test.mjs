import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function definitions(file, names) {
    const content = readFileSync(new URL(file, import.meta.url), "utf8");
    const source = ts.createSourceFile(file, content, ts.ScriptTarget.ES2022, true);
    return source.statements.filter(item => names.includes(item.name?.text))
        .map(item => item.getText(source).replace(/^export /, "")).join("\n");
}
const code = ts.transpileModule([
    definitions("../../src/services/host-generation-admission.ts", ["isCanvasEditorToolInput"]),
    definitions("../../src/services/canvas-generation-consumer.ts", ["persistCanvasGenerationSource"]),
    definitions("../../src/services/api/image-protocols.ts", ["buildBackendToolRequests"]),
    definitions("../../src/services/api/generation-task.ts", ["prepareBackendToolGenerationTask", "runBackendToolGenerationTask", "throwIfAborted"]),
    definitions("../../src/services/plugin-host.ts", ["createPluginHostContext"]),
].join("\n"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;

function setup() {
    const calls = [];
    const scope = { userScope: "101", epoch: 1 };
    let current = scope;
    const assertCurrent = captured => { if (captured !== current) throw new Error("账号已切换"); };
    const globals = {
        Blob, DOMException, crypto,
        logicalModelIDForConfig: config => config.logicalModelId,
        assertAgentExchangeBudget: () => undefined,
        resourceIdFromStorageKey: key => /^resource:([1-9]\d*)$/.exec(key || "")?.[1],
        resolveModelRequestConfig: config => ({ model: config.model, interfaceType: "chat-completion" }),
        modelCapabilityConfigFor: () => ({ text: { streaming: true } }),
        backendProviderConfig: () => ({ apiKey: "must-not-enter-request", size: "1024x1024" }),
        pluginStorageFor: () => ({}),
        requestToolResponse: () => { throw new Error("浏览器不得直连供应商"); },
        captureUserScope: () => current,
        assertUserScope: assertCurrent,
        createGenerationTask: async (body, options) => { calls.push({ body, options }); return { id: "task-1" }; },
        waitForGenerationTask: async (_id, options) => { calls.push({ waitOptions: options }); return { id: "task-1", resultJson: "{}" }; },
        parseBackendGenerationResult: () => ({ toolCalls: [{ function: { name: "optimize_prompt", arguments: "{}" } }] }),
    };
    const context = vm.createContext(globals);
    vm.runInContext(`${code}\nthis.prepare = prepareBackendToolGenerationTask; this.run = runBackendToolGenerationTask; this.host = createPluginHostContext; this.saveSource = persistCanvasGenerationSource;`, context);
    return { context, calls, scope, switchAccount: () => { current = { userScope: "202", epoch: 2 }; } };
}
function options(overrides = {}) {
    return {
        projectId: "canvas-one", prompt: "优化提示词", clientOperationId: "tool-operation-one",
        config: { logicalModelId: "9007199254740997", model: "saved-text", textModel: "saved-text", systemPrompt: "" },
        messages: [{ role: "user", content: [{ type: "text", text: "主体在阅读" }, { type: "image_url", image_url: { url: "resource:9007199254740999" } }] }],
        tools: [{ type: "function", function: { name: "optimize_prompt", parameters: {} } }],
        toolChoice: { type: "function", name: "optimize_prompt" },
        metadata: { source: "prompt-optimizer", nodeId: "image-one" },
        ...overrides,
    };
}

test("工具准备携带同一项目节点与稳定操作键，只发送canonical和永久图片身份", () => {
    const { context } = setup();
    const input = options();
    const before = JSON.stringify(input);
    const task = context.prepare(input);
    assert.equal(task.projectId, "canvas-one");
    assert.equal(task.logicalModelId, "9007199254740997");
    assert.equal(task.input.metadata.nodeId, "image-one");
    assert.equal(task.input.metadata.sourceNodeId, "image-one");
    assert.equal(task.input.metadata.clientOperationId, "tool-operation-one");
    assert.equal(JSON.stringify(task.input.config), "{}");
    assert.equal(JSON.stringify(task.input.agentRequests.canonical.messages), JSON.stringify(input.messages));
    assert.equal(task.input.referenceImages[0].storageKey, "resource:9007199254740999");
    assert.equal(JSON.stringify(input), before);
    assert.ok(!JSON.stringify(task).includes("must-not-enter-request"));
});

test("缺少宿主身份、未保存模型或临时图片在提交前明确拒绝", () => {
    const { context } = setup();
    for (const patch of [
        { projectId: undefined }, { metadata: { source: "prompt-optimizer" } },
        { config: { model: "browser-model", systemPrompt: "" } },
        { messages: [{ role: "user", content: [{ type: "image_url", image_url: { url: "https://temporary.invalid/a" } }] }] },
    ]) assert.throws(() => context.prepare(options(patch)));
});

test("工具保存屏障保存当前来源和连线，不把原图片标记为生成中", async () => {
    const { context, scope } = setup();
    const state = { nodes: [{ id: "image-one", metadata: { content: "/saved.png", status: "success", taskId: "original-task" } }], connections: [{ id: "original-edge" }] };
    const before = JSON.stringify(state);
    const writes = [], saves = [];
    const adapter = { read: () => state, write: value => writes.push(value) };
    context.canvasGenerationLiveAdapterKey = (user, project) => `${user}:${project}`;
    context.canvasGenerationLiveAdapters = new Map([["101:canvas-one", adapter]]);
    context.canvasTaskAdmissionIdentity = task => ({ projectId: task.projectId, nodeId: task.input.metadata.nodeId, sourceNodeId: task.input.metadata.sourceNodeId, operationId: task.input.metadata.clientOperationId });
    context.persistCanvasDocument = async (...args) => saves.push(args);
    await context.saveSource(context.prepare(options()), scope);
    assert.equal(writes.length, 0);
    assert.equal(saves.length, 1);
    assert.equal(saves[0][1].nodes, state.nodes);
    assert.equal(saves[0][1].connections, state.connections);
    assert.equal(saves[0][2], scope);
    assert.equal(JSON.stringify(state), before);
});

test("工具提交与等待共用捕获账号和取消信号，晚响应不进入新账号结果", async () => {
    const environment = setup();
    const controller = new AbortController();
    await environment.context.run(options({ signal: controller.signal }));
    assert.equal(environment.calls[0].options.expectedScope, environment.scope);
    assert.equal(environment.calls[0].options.signal, controller.signal);
    assert.equal(environment.calls[1].waitOptions.expectedScope, environment.scope);
    environment.context.createGenerationTask = async () => { environment.switchAccount(); return { id: "late-task" }; };
    await assert.rejects(environment.context.run(options()), /账号已切换/);
    assert.equal(environment.calls.length, 2);
});

test("提示词插件宿主通过任务relay传项目节点，不调用浏览器渠道", async () => {
    const { context } = setup();
    let captured;
    context.runBackendToolGenerationTask = async request => {
        captured = request;
        return { content: "", toolCalls: [{ function: { name: "optimize_prompt", arguments: "{}" } }] };
    };
    const plugin = { manifest: { id: "prompt-optimizer", permissions: ["ai.text"] } };
    const installation = { config: {} };
    const host = context.host(plugin, installation, options().config, { projectId: "canvas-one", nodeId: "image-one" });
    const result = await host.services.ai.text.requestToolResponse({ messages: options().messages, tools: options().tools, toolChoice: options().toolChoice });
    assert.equal(captured.projectId, "canvas-one");
    assert.equal(captured.metadata.source, "prompt-optimizer");
    assert.equal(captured.metadata.nodeId, "image-one");
    assert.equal(result.toolCalls[0].name, "optimize_prompt");
});

test("固定源优化器保留永久resource图片并发送原工具schema", async () => {
    const content = readFileSync(new URL("../../src/lib/plugins/builtin/prompt-optimizer.ts", import.meta.url), "utf8");
    const compiled = ts.transpileModule(content, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
    const exports = {};
    const context = vm.createContext({ exports, require: name => {
        assert.equal(name, "../plugin-registry");
        return { registerPlugin: () => undefined };
    } });
    vm.runInContext(compiled, context);
    let captured;
    const provider = exports.promptOptimizerPlugin.createPromptOptimizer({ services: { ai: { text: { requestToolResponse: async request => {
        captured = request;
        return { content: "", toolCalls: [{ name: "optimize_prompt", arguments: JSON.stringify({ optimizedPrompt: "窗边阅读", negativePrompt: "", changes: [], assumptions: [], variants: [] }) }] };
    } } } } });
    const result = await provider.optimize({ prompt: "阅读", mode: "reference", generationMode: "image", targetModel: "saved-image", context: { images: [{ title: "图片一", url: "resource:9007199254740999" }] } });
    assert.equal(result.optimizedPrompt, "窗边阅读");
    const parts = captured.messages.at(-1).content;
    assert.equal(parts.find(part => part.type === "image_url")?.image_url.url, "resource:9007199254740999");
    const snapshot = JSON.parse(readFileSync(new URL("../../../../backend/src/short_drama/ai/data/beeftv_tool_contracts.json", import.meta.url), "utf8"));
    assert.equal(JSON.stringify(captured.tools[0]), JSON.stringify(snapshot.tools.find(tool => tool.function.name === "optimize_prompt")));
});
