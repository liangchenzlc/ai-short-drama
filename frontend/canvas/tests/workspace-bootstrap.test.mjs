import assert from "node:assert/strict";
import test from "node:test";
import { initializeWorkspaceState } from "../src/services/workspace-bootstrap.ts";

test("工作区先确认身份和缓存命名空间，再载入配置与画布", async () => {
    const actions = [];
    await initializeWorkspaceState({ loadWorkspace: async () => { actions.push("auth"); return "member"; }, applySession: async user => { actions.push(user); }, restoreModelConfig: async () => { actions.push("models"); }, restoreProjects: async () => { actions.push("projects"); } });
    assert.deepEqual(actions, ["auth", "member", "models", "projects"]);
});

test("认证失败不得以匿名本地身份继续恢复任何缓存或写入", async () => {
    const unauthorized = new Error("unauthorized");
    const forbidden = async () => { assert.fail("不应恢复匿名工作区"); };
    await assert.rejects(initializeWorkspaceState({ loadWorkspace: async () => { throw unauthorized; }, applySession: forbidden, restoreModelConfig: forbidden, restoreProjects: forbidden }), error => error === unauthorized);
});
