import assert from "node:assert/strict";
import test from "node:test";
import { prepareInitialCanvasGroup, readInitialCanvasGroup, clearInitialCanvasGroup } from "../src/services/canvas-initial-group.ts";
import { prepareInitialCanvasWrite, readInitialCanvasWrite } from "../src/services/canvas-initial-write.ts";

const projects = () => ["root", "child"].map(id => ({ id, workspaceProjectId: "root", revision: "0", title: id, nodes: [] }));

test("逐画布记录只写一部分后，整组原文仍可恢复，后续编辑不能换原请求", async () => {
    const values = new Map();
    let failure = true;
    const storage = {
        getItem: key => values.get(key) ?? null,
        setItem: (key, value) => {
            if (failure && key === "canvas-initial-write:child") throw new Error("quota");
            values.set(key, value);
        },
        removeItem: key => values.delete(key),
    };
    const input = projects();
    const group = await prepareInitialCanvasGroup(storage, input);
    await prepareInitialCanvasWrite(storage, group.projects[0]);
    await assert.rejects(prepareInitialCanvasWrite(storage, group.projects[1]), /quota/);
    input[1].title = "后续编辑";
    assert.deepEqual(await prepareInitialCanvasGroup(storage, input), group);
    assert.deepEqual(await readInitialCanvasGroup(storage, "root"), group);
    failure = false;
    for (const project of (await readInitialCanvasGroup(storage, "root")).projects) await prepareInitialCanvasWrite(storage, project);
    await clearInitialCanvasGroup(storage, "root");
    assert.equal(await readInitialCanvasGroup(storage, "root"), null);
    assert.equal((await readInitialCanvasWrite(storage, "child")).title, "child");
});

test("整组存储失败不产生部分记录，损坏或跨组文档拒绝覆盖", async () => {
    const storage = { getItem: () => null, setItem: () => { throw new Error("quota"); }, removeItem: () => assert.fail() };
    await assert.rejects(prepareInitialCanvasGroup(storage, projects()), /quota/);
    for (const raw of ["{", "null", JSON.stringify({ version: 1, rootId: "root", projects: [projects()[0], projects()[0]] }),
        JSON.stringify({ version: 1, rootId: "root", projects: [{ ...projects()[0], workspaceProjectId: "other" }] })]) {
        await assert.rejects(readInitialCanvasGroup({ ...storage, getItem: () => raw }, "root"), /原有草稿已保留/);
    }
});
