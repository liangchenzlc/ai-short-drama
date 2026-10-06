import assert from "node:assert/strict";
import test from "node:test";
import { createViewportWriter } from "../src/services/host-viewport-writer.ts";

const origin = { x: 0, y: 0, k: 1 };
const first = { x: 10.25, y: -20, k: 0.5 };
const latest = { x: 70, y: -80, k: 1.25 };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

test("视口更新串行提交，进行中的响应不能覆盖之后的移动", async () => {
    const gate = deferred();
    const writes = [];
    const writer = createViewportWriter(async (_id, payload) => {
        writes.push(structuredClone(payload));
        if (writes.length === 1) await gate.promise;
        return { viewport: payload.viewport };
    });
    writer.observe("canvas", origin);
    const a = writer.save("canvas", first);
    await Promise.resolve();
    const b = writer.save("canvas", latest);
    assert.equal(writes.length, 1);
    gate.resolve();
    await Promise.all([a, b]);
    assert.deepEqual(writes, [
        { expected_viewport: origin, viewport: first },
        { expected_viewport: first, viewport: latest },
    ]);
    await writer.save("canvas", latest);
    assert.equal(writes.length, 2);
});

test("未知响应重试原视口请求，409 停止并保留未提交位置", async () => {
    const writes = [];
    let failure = Object.assign(new Error("connection lost"), { status: 0 });
    const writer = createViewportWriter(async (_id, payload) => {
        writes.push(structuredClone(payload));
        if (failure) throw failure;
        return { viewport: payload.viewport };
    });
    writer.observe("canvas", origin);
    await assert.rejects(writer.save("canvas", first), /connection lost/);
    failure = undefined;
    await writer.save("canvas", latest);
    assert.deepEqual(writes[1], writes[0]);
    assert.deepEqual(writes[2], { expected_viewport: first, viewport: latest });

    failure = Object.assign(new Error("other window moved"), { status: 409 });
    await assert.rejects(writer.save("canvas", origin), /other window/);
    const count = writes.length;
    writer.observe("canvas", latest);
    await assert.rejects(writer.save("canvas", first), /other window/);
    assert.equal(writes.length, count);
});

test("没有服务端基线不提交，正在保存时旧读取不改变基线", async () => {
    const gate = deferred();
    const writes = [];
    const writer = createViewportWriter(async (_id, payload) => {
        writes.push(structuredClone(payload));
        await gate.promise;
        return { viewport: payload.viewport };
    });
    await assert.rejects(writer.save("missing", first), /读取/);
    writer.observe("canvas", origin);
    const pending = writer.save("canvas", first);
    await Promise.resolve();
    writer.observe("canvas", { x: 999, y: 0, k: 1 });
    gate.resolve();
    await pending;
    await writer.save("canvas", latest);
    assert.deepEqual(writes[1].expected_viewport, first);
});
