import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const modules = new Map();
function load(name) {
    if (modules.has(name)) return modules.get(name);
    const source = readFileSync(new URL(`../src/lib/${name}.ts`, import.meta.url), "utf8");
    const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
    const exports = {};
    modules.set(name, exports);
    vm.runInNewContext(code, { exports, require: imported => {
        if (imported === "@/lib/runtime-mode") return { isLocalRuntimeMode: () => false };
        assert.ok(["./moderation-error", "./task-constraint-error"].includes(imported), imported);
        return load(imported.slice(2));
    } });
    return exports;
}
const { explainGenerationError, generationFailureMetadata } = load("generation-error");
const rejected = { code: "provider_queue_full", message: "模型服务的视频队列已满，本次未创建供应商任务。请稍后显式重试。" };

test("明确未受理的视频队列满保留原因和原显式重新生成入口", () => {
    const result = explainGenerationError(rejected);
    assert.equal(result.category, "provider_unavailable");
    assert.match(result.reason, /队列已满.*未创建供应商任务/);
    assert.match(result.action, /稍后.*显式重试/);
    assert.equal(result.uncertain, false);
    assert.equal(result.blockAutomaticRetry, false);
});

test("旧错误文案恢复后仍明确未受理并允许用户显式重试", () => {
    const result = explainGenerationError(rejected.message);
    assert.equal(result.category, "provider_unavailable");
    assert.match(result.reason, /未创建供应商任务/);
    assert.equal(result.blockAutomaticRetry, false);
});

test("原消费者保存分类后，原节点再次解释时仍保留队列满和未受理原因", () => {
    const metadata = generationFailureMetadata(rejected, "原视频提示词");
    const result = explainGenerationError({ code: metadata.generationErrorCode, message: metadata.errorDetails });
    assert.match(result.reason, /队列已满.*未创建供应商任务/);
    assert.match(result.action, /稍后.*显式重试/);
    assert.equal(result.blockAutomaticRetry, false);
});

test("未确认受理阶段优先于任何队列满或普通503错误文案", () => {
    for (const error of [rejected, { code: "provider_http_error", message: "HTTP 503" }]) {
        const result = explainGenerationError(error, { stage: "submission_unknown" });
        assert.equal(result.uncertain, true);
        assert.equal(result.blockAutomaticRetry, true);
    }
});
