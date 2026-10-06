import assert from "node:assert/strict";
import test from "node:test";
import { compareDrawingRevisions, drawingRevision, nextDrawingRevision } from "../src/lib/canvas/canvas-drawing-revision.ts";

test("绘图版本保留 uint64 字符串精度并读取旧本机安全整数", () => {
    assert.equal(drawingRevision(7), "7");
    assert.equal(drawingRevision("9007199254740993"), "9007199254740993");
    assert.equal(compareDrawingRevisions("10", "9"), 1);
    assert.equal(compareDrawingRevisions("9007199254740993", "9007199254740992"), 1);
    assert.equal(compareDrawingRevisions(7, "7"), 0);
    assert.equal(nextDrawingRevision("9007199254740993"), "9007199254740994");
    for (const invalid of [-1, 0.5, Number.MAX_SAFE_INTEGER + 1, "01", "-1", "18446744073709551616", null]) {
        assert.throws(() => drawingRevision(invalid), /绘图版本/);
    }
    assert.throws(() => nextDrawingRevision("18446744073709551615"), /绘图版本/);
});
