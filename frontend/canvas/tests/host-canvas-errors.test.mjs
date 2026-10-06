import assert from "node:assert/strict";
import test from "node:test";
import { hostCanvasErrorMessage } from "../src/services/host-canvas-errors.ts";

test("画布冲突保留有针对性的已知提示，不把其他错误误报为冲突", () => {
    const fallback = "服务暂时不可用，请稍后重试。";
    assert.match(hostCanvasErrorMessage("canvas_view_preferences_conflict", fallback), /外观.*本机设置已保留/);
    assert.match(hostCanvasErrorMessage("canvas_viewport_conflict", fallback), /视口.*本机位置已保留/);
    assert.equal(hostCanvasErrorMessage("unknown_code", fallback), fallback);
    assert.equal(hostCanvasErrorMessage("constructor", fallback), fallback);
});
