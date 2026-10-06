import assert from "node:assert/strict";
import test from "node:test";
import { prepareCanvasDeletion, readCanvasDeletion, clearCanvasDeletion, validateCanvasDeletionStatus } from "../src/services/canvas-deletion-attempt.ts";

const project = { id: "canvas-a", workspaceProjectId: "9007199254740999", revision: "9007199254741001", title: "原作品", nodes: [{ id: "n", metadata: { content: "原文" } }] };
function store() {
    const values = new Map();
    return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}

test("未知删除在刷新后保持原始快照、版本、时间与幂等键", async () => {
    const storage = store();
    const draft = structuredClone(project);
    const first = await prepareCanvasDeletion(storage, "111", draft);
    draft.nodes[0].metadata.content = "后续输入";
    draft.revision = "9007199254741007";
    assert.deepEqual(await prepareCanvasDeletion(storage, "111", draft), first);
    assert.deepEqual(await readCanvasDeletion(storage, "111", project.id), first);
    assert.equal(first.project.nodes[0].metadata.content, "原文");
    await clearCanvasDeletion(storage, project.id);
    assert.equal(await readCanvasDeletion(storage, "111", project.id), null);
});

test("未能持久化的删除、损坏记录和跨账号记录均不能继续", async () => {
    const storage = store();
    await assert.rejects(prepareCanvasDeletion({ ...storage, setItem: () => { throw new Error("quota"); } }, "111", project), /quota/);
    await prepareCanvasDeletion(storage, "111", project);
    await assert.rejects(readCanvasDeletion(storage, "222", project.id), /原有草稿已保留/);
    storage.values.set("canvas-deletion-attempt:canvas-a", "{");
    await assert.rejects(prepareCanvasDeletion(storage, "111", project), /记录损坏/);
    assert.equal(storage.values.get("canvas-deletion-attempt:canvas-a"), "{");
});

test("只接受同作品同代次的本人回执，旧删除被恢复后不能再删当前作品", async () => {
    const attempt = await prepareCanvasDeletion(store(), "111", project);
    const status = { source_key: project.id, project_id: project.workspaceProjectId, archive_key: attempt.archiveKey,
        expected_row_version: project.revision, committed_row_version: "9007199254741002", state: "archived" };
    assert.equal(validateCanvasDeletionStatus(attempt, status), "archived");
    assert.equal(validateCanvasDeletionStatus(attempt, { ...status, state: "superseded" }), "superseded");
    for (const patch of [{ source_key: "other" }, { project_id: "other" }, { archive_key: "other" }, { committed_row_version: "9007199254741004" }, { state: "unknown" }]) {
        assert.throws(() => validateCanvasDeletionStatus(attempt, { ...status, ...patch }), /回执与原请求不一致/);
    }
});
