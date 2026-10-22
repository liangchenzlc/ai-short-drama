import assert from "node:assert/strict";
import test from "node:test";
import { generationTaskStageLabel, generationTaskShowsProgress, generationTaskStallHint } from "../../src/lib/generation-task-display.ts";

test("交付阶段不伪造百分比，只有真实停滞状态提示检查详情", () => {
    const now = Date.UTC(2026, 9, 16);
    for (const stage of ["saving", "downloading", "materializing", "processing"]) {
        const task = { status: "running", stage, updatedAt: new Date(now - 91_000).toISOString() };
        assert.equal(generationTaskShowsProgress(task), false);
        assert.notEqual(generationTaskStageLabel(task), stage);
        assert.match(generationTaskStallHint(task, now), /请勿重复生成/);
        assert.equal(generationTaskStallHint({ ...task, status: "succeeded" }, now), "");
        assert.equal(generationTaskStallHint({ ...task, updatedAt: new Date(now).toISOString() }, now), "");
    }
    assert.equal(generationTaskStallHint({ status: "running", stage: "saving", updatedAt: "invalid" }, now), "");
});
