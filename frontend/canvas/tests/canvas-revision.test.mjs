import assert from "node:assert/strict";
import test from "node:test";
import { compareCanvasRevisions, isCanvasRevision, maxCanvasRevision } from "../src/lib/canvas/canvas-server-revision.ts";

test("数据库版本按数值比较且不会经过浮点数，包括 2^53 以上的相邻版本", () => {
    assert.equal(compareCanvasRevisions("9", "10"), -1);
    assert.equal(compareCanvasRevisions("9007199254740993", "9007199254740992"), 1);
    assert.equal(maxCanvasRevision("18446744073709551614", "18446744073709551615"), "18446744073709551615");
    assert.equal(compareCanvasRevisions("0", "0"), 0);
});

test("无效版本不能用零或舍入值兜底覆盖服务端", () => {
    for (const value of [0, 9007199254740992, "-1", "01", "1e5", "18446744073709551616", null]) assert.equal(isCanvasRevision(value), false);
    assert.throws(() => compareCanvasRevisions("1", "invalid"), /版本无效/);
});
