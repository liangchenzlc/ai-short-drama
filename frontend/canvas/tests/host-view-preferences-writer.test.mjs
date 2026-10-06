import assert from "node:assert/strict";
import test from "node:test";
import { createViewPreferencesWriter, viewPreferences } from "../src/services/host-view-preferences-writer.ts";

const dark = viewPreferences({ appearance: { mode: "dark" } });
const light = viewPreferences({ appearance: { mode: "light" } });
const lines = { ...light, backgroundMode: "lines" };

test("主题和网格连续修改串行保存，旧读取和旧回执不能覆盖新设置", async () => {
    let finish;
    const pending = new Promise(resolve => { finish = resolve; });
    const calls = [];
    const writer = createViewPreferencesWriter(async (_id, input) => {
        calls.push(structuredClone(input));
        if (calls.length === 1) await pending;
        return { preferences: { ...input.preferences, appearance: { ...input.preferences.appearance, custom: null } } };
    });
    writer.observe("one", dark);
    const first = writer.save("one", light);
    await Promise.resolve();
    const latest = writer.save("one", lines);
    writer.observe("one", {});
    assert.equal(calls.length, 1);
    finish();
    await Promise.all([first, latest]);
    assert.deepEqual(calls, [
        { expected_preferences: dark, preferences: light },
        { expected_preferences: light, preferences: lines },
    ]);
    await writer.save("one", lines);
    assert.equal(calls.length, 2);
});

test("外观响应未知时复用原请求，窗口冲突冻结后续自动覆盖", async () => {
    let failure = new Error("network lost");
    const calls = [];
    const writer = createViewPreferencesWriter(async (_id, input) => {
        calls.push(structuredClone(input));
        if (failure) throw failure;
        return { preferences: input.preferences };
    });
    writer.observe("one", dark);
    await assert.rejects(writer.save("one", light), /network/);
    failure = undefined;
    await writer.save("one", lines);
    assert.deepEqual(calls[0], calls[1]);
    assert.deepEqual(calls[2], { expected_preferences: light, preferences: lines });
    failure = Object.assign(new Error("another window"), { status: 409 });
    await assert.rejects(writer.save("one", dark), /window/);
    const count = calls.length;
    await assert.rejects(writer.save("one", light), /window/);
    assert.equal(calls.length, count);
});

test("尚未读取外观时不写入；失败回执不被误报为保存成功", async () => {
    const writer = createViewPreferencesWriter(async () => ({ preferences: dark }));
    await assert.rejects(writer.save("one", light), /读取/);
    writer.observe("one", dark);
    await assert.rejects(writer.save("one", light), /回执/);
});
