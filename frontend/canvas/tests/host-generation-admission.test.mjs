import assert from "node:assert/strict";
import test from "node:test";
import { canvasConfigGenerationState, canvasGenerationOperationBase, canvasRecoveryTaskForNode, canvasTaskCanUpdateNode, createCanvasTaskAdmission } from "../src/services/host-generation-admission.ts";

const scope = { userScope: "101", epoch: 1 };
function input(mode = "text", operation = "one") {
    return { projectId: "canvas-key", type: `canvas_${mode}`, prompt: "生成内容", logicalModelId: "9007199254740999", input: { mode, prompt: "生成内容", config: {}, metadata: { nodeId: "target", sourceNodeId: "source", clientOperationId: operation } } };
}
function setup(overrides = {}) {
    const events = [], storage = new Map();
    const admission = createCanvasTaskAdmission({
        store: () => ({ getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }),
        assertCurrent: captured => assert.deepEqual(captured, scope),
        saveSource: async () => { events.push("save-ack"); },
        post: async (body, key) => { events.push({ body, key }); return { id: "task-one" }; },
        isUncertain: error => error?.uncertain === true,
        ...overrides,
    });
    return { admission, events, storage };
}

test("四类原任务先等待来源保存回执，再以源操作身份准入", async () => {
    for (const mode of ["text", "image", "video", "audio"]) {
        const { admission, events } = setup();
        assert.deepEqual(await admission.submit(input(mode), scope), { id: "task-one" });
        assert.equal(events[0], "save-ack");
        assert.deepEqual(events[1], { body: input(mode), key: "one" });
    }
});

test("保存冲突、保存失败或存储失败都不提交生成", async () => {
    for (const error of [Object.assign(new Error("conflict"), { status: 409 }), new Error("save failed")]) {
        const { admission, events } = setup({ saveSource: async () => { throw error; } });
        await assert.rejects(admission.submit(input(), scope), value => value === error);
        assert.equal(events.length, 0);
    }
    const { admission, events } = setup({ store: () => ({ getItem: () => null, setItem: () => { throw new Error("storage failed"); } }) });
    await assert.rejects(admission.submit(input(), scope), /storage failed/);
    assert.deepEqual(events, ["save-ack"]);
});

test("响应不明只重放相同键和冻结正文，双重失败保留恢复日志", async () => {
    const attempts = [];
    const unknown = Object.assign(new Error("network"), { uncertain: true });
    const { admission, storage } = setup({ post: async (body, key) => { attempts.push({ body, key }); throw unknown; } });
    await assert.rejects(admission.submit(input(), scope), value => value === unknown);
    assert.equal(attempts.length, 2);
    assert.deepEqual(attempts[0], attempts[1]);
    assert.equal(storage.size, 1);
    const changed = input(); changed.prompt = "新内容";
    await assert.rejects(admission.submit(changed, scope), /不能用同一操作更改参数/);
    assert.equal(attempts.length, 2);
});

test("明确拒绝清理日志，明确再次生成使用新键，账号切换不继续POST", async () => {
    const error = Object.assign(new Error("not supported"), { status: 422 });
    const rejected = setup({ post: async () => { throw error; } });
    await assert.rejects(rejected.admission.submit(input(), scope), value => value === error);
    assert.equal(rejected.storage.size, 0);
    const accepted = setup();
    await accepted.admission.submit(input("text", "first"), scope);
    await accepted.admission.submit(input("text", "second"), scope);
    assert.deepEqual(accepted.events.filter(value => typeof value === "object").map(value => value.key), ["first", "second"]);
    let switched = false;
    const guarded = setup({ assertCurrent: () => { if (switched) throw new Error("账号已切换"); }, saveSource: async () => { switched = true; } });
    await assert.rejects(guarded.admission.submit(input(), scope), /账号已切换/);
    assert.equal(guarded.events.length, 0);
});

test("无模型、无节点关联和客户端密钥不能进入日志或POST", () => {
    const { admission, storage } = setup();
    for (const change of [{ logicalModelId: undefined }, { logicalModelId: "host-1" }, { input: { metadata: {} } }, { input: { config: { api_key: "secret" } } }]) {
        assert.throws(() => admission.submit({ ...input(), ...change }, scope));
    }
    assert.equal(storage.size, 0);
});

test("恢复按本人投影的操作关联匹配，已有新任务绑定不被其他历史覆盖", () => {
    const tasks = [
        { id: "new", projectId: "canvas-key", clientOperationId: "new-op", clientContext: { nodeId: "target" } },
        { id: "old", projectId: "canvas-key", clientOperationId: "old-op", clientContext: { nodeId: "target" } },
        { id: "foreign", projectId: "other", clientOperationId: "old-op", clientContext: { nodeId: "target" } },
    ];
    assert.equal(canvasRecoveryTaskForNode({ id: "target", metadata: { taskClientOperationId: "old-op" } }, "canvas-key", tasks)?.id, "old");
    assert.equal(canvasRecoveryTaskForNode({ id: "target", metadata: { taskId: "new", taskClientOperationId: "old-op" } }, "canvas-key", tasks)?.id, "new");
    assert.equal(canvasRecoveryTaskForNode({ id: "target", metadata: { taskId: "missing" } }, "canvas-key", tasks), undefined);
    assert.equal(canvasTaskCanUpdateNode({ metadata: { taskId: "new", taskClientOperationId: "new-op" } }, tasks[1]), false);
    assert.equal(canvasTaskCanUpdateNode({ metadata: { taskClientOperationId: "new-op" } }, tasks[1]), false);
    assert.equal(canvasTaskCanUpdateNode({ metadata: { taskClientOperationId: "old-op" } }, tasks[1]), true);
});

test("并发同操作复用一份保存与POST，参数变化不能借用正在准入的操作", async () => {
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    const { admission, events } = setup({ saveSource: async () => { await gate; } });
    const first = admission.submit(input(), scope);
    assert.equal(admission.submit(input(), scope), first);
    const changed = input(); changed.prompt = "changed";
    assert.throws(() => admission.submit(changed, scope), /不能更改请求内容/);
    release();
    await first;
    assert.equal(events.length, 1);
});

test("Config刷新恢复按本批target群收尾，不绑定child任务且忽略旧批次", () => {
    const source = { id: "source", metadata: { taskClientOperationId: "current" } };
    const task = (id, operation, type = "canvas_text", status = "succeeded") => ({ id, projectId: "canvas-key", type, status, clientOperationId: operation, clientContext: { nodeId: id, sourceNodeId: "source" } });
    const tasks = [task("one", "current:0"), task("two", "current:1"), task("old", "previous", "canvas_text", "failed")];
    const node = (id, status, content) => ({ id, metadata: { status, content } });
    assert.deepEqual(canvasConfigGenerationState(source, "canvas-key", [node("one", "success", "正文"), node("two", "success", "正文")], tasks), { status: "success", errorDetails: undefined });
    assert.equal(canvasConfigGenerationState(source, "canvas-key", [node("one", "success", "正文"), node("two", "error")], tasks).status, "error");
    assert.equal(canvasConfigGenerationState(source, "canvas-key", [], [task("old", "previous", "canvas_text", "running")]).status, "error");
    assert.equal(source.metadata.taskId, undefined);
    assert.equal(canvasGenerationOperationBase("current:1", { batchIndex: 1, batchCount: 2 }), "current");
});

test("Config图片部分成功沿用源success语义，真实未终态维持loading，无任务明确error", () => {
    const source = { id: "source", metadata: { taskClientOperationId: "current" } };
    const tasks = [{ id: "one", projectId: "canvas-key", type: "canvas_image", status: "succeeded", clientOperationId: "current:0", clientContext: { nodeId: "one", sourceNodeId: "source" } }, { id: "two", projectId: "canvas-key", type: "canvas_image", status: "running", clientOperationId: "current:1", clientContext: { nodeId: "two", sourceNodeId: "source" } }];
    assert.equal(canvasConfigGenerationState(source, "canvas-key", [{ id: "one", metadata: { status: "success", content: "resource:111" } }], tasks).status, "success");
    assert.equal(canvasConfigGenerationState(source, "canvas-key", [{ id: "one", metadata: { status: "loading" } }], tasks).status, "loading");
    assert.equal(canvasConfigGenerationState(source, "canvas-key", [], []).status, "error");
    assert.equal(canvasConfigGenerationState(source, "other-project", [], tasks).status, "error");
    const terminal = tasks.map(task => ({ ...task, status: "succeeded" }));
    const unbound = canvasConfigGenerationState(source, "canvas-key", [{ id: "one", metadata: { status: "loading" } }], terminal, new Set(["one", "two"]));
    assert.equal(unbound.status, "error");
    assert.match(unbound.errorDetails, /生成结果已保留/);
});
