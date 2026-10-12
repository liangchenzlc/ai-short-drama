import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function declaration(file, name) {
    const content = readFileSync(new URL(file, import.meta.url), "utf8");
    const source = ts.createSourceFile(file, content, ts.ScriptTarget.ES2022, true);
    const node = source.statements.find(statement => ts.isFunctionDeclaration(statement) && statement.name?.text === name);
    assert.ok(node, `缺少实际函数 ${name}`);
    return node.getText(source).replace(/^export /u, "");
}
const javascript = ts.transpileModule([
    declaration("../src/services/canvas-revision-conflict.ts", "isCanvasRevisionConflict"),
    declaration("../src/services/local-workspace-repository.ts", "restoreLocalCanvasProjectFromHistoryUnlocked"),
].join("\n"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;

class ApiError extends Error {
    constructor(message, status, reason) {
        super(message);
        this.status = status;
        this.reason = reason;
    }
}

for (const [label, status, reason, specific] of [
    ["旧 Skill 作者归属", 409, "canvas_legacy_skill_body_private", true],
    ["普通版本冲突", 409, "stale_revision", false],
    ["缺少版本前提", 428, undefined, false],
    ["服务暂时不可用", 503, "storage_unavailable", true],
]) {
    test(`历史恢复拒绝：${label}保留正确原因，失败后不读取或应用作品`, async () => {
        const expected = { userScope: "owner", epoch: 3 };
        const heads = { drawing: { revision: "9007199254740993", deleted: false } };
        const error = new ApiError("请先确认作者归属并迁移本人模板，原数据已保留", status, reason);
        const requests = [];
        let reads = 0;
        const restore = vm.runInNewContext(`${javascript}\nrestoreLocalCanvasProjectFromHistoryUnlocked`, {
            ApiError,
            canvasDeleting: new Set(),
            saveKey: (scope, id) => `${scope}:${id}`,
            assertDispatchGuard: scope => assert.equal(scope, expected),
            rethrowIfAbandoned: () => {},
            restoreCanvasHistory: async (...args) => { requests.push(args); throw error; },
            readLocalCanvasProjectFromBackend: () => { reads++; assert.fail("恢复拒绝后不得读取作品"); },
        });
        await assert.rejects(() => restore("canvas", "snapshot", "9007199254740994", expected, heads), actual => {
            if (specific) assert.equal(actual, error);
            else assert.equal(actual.message, "当前画布已有更新，这次没有恢复。请重新打开版本记录后再试。");
            return true;
        });
        assert.equal(requests.length, 1);
        assert.deepEqual(requests[0].slice(0, 3), ["canvas", "snapshot", "9007199254740994"]);
        assert.equal(requests[0][3].expectedScope, expected);
        assert.equal(requests[0][4], heads);
        assert.equal(reads, 0);
    });
}
