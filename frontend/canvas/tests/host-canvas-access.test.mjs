import assert from "node:assert/strict";
import test from "node:test";
import { createCanvasAccessRegistry, canvasRequestIdentity, canvasRequestCanRevoke } from "../src/services/host-canvas-access.ts";

test("已确认恢复只解除同一账号和会话中该画布的归档冻结", () => {
    const access = createCanvasAccessRegistry();
    const scope = { userScope: "111", epoch: 1 };
    const other = { userScope: "222", epoch: 1 };
    for (const id of ["one", "two"]) access.deny(scope, id);
    access.deny(other, "one");
    access.allow(scope, "one");
    access.assert(scope, "one");
    assert.throws(() => access.assert(scope, "two"));
    assert.throws(() => access.assert(other, "one"));
    assert.equal(canvasRequestIdentity("/canvas-projects/one/recycle-restore"), undefined);
    assert.equal(canvasRequestIdentity("/canvas-projects/one/recycle-status"), undefined);
});

test("撤权仅冻结对应账号会话的对应画布，不误伤其他画布或后续登录", () => {
    const access = createCanvasAccessRegistry();
    const scope = { userScope: "111", epoch: 1 };
    let changes = 0;
    const unsubscribe = access.subscribe(() => { changes++; });
    access.deny(scope, "one");
    access.deny(scope, "one");
    assert.throws(() => access.assert(scope, "one"), /权限/);
    access.assert(scope, "two");
    access.assert({ ...scope, epoch: 2 }, "one");
    access.assert({ ...scope, userScope: "222" }, "one");
    assert.equal(changes, 1);
    unsubscribe();
    access.deny(scope, "two");
    assert.equal(changes, 1);
});

test("初次复制的来源缺失不能冒充目标撤权，已知撤权仍阻止原请求重放", () => {
    assert.equal(canvasRequestCanRevoke("put", "/canvas-projects/copy"), false);
    assert.equal(canvasRequestCanRevoke("get", "/canvas-projects/copy"), true);
    assert.equal(canvasRequestCanRevoke("put", "/canvas-projects/copy/viewport"), true);
    assert.equal(canvasRequestCanRevoke("post", "/ops/canvas.document.commit"), true);
    const access = createCanvasAccessRegistry();
    const scope = { userScope: "111", epoch: 1 };
    access.deny(scope, "copy");
    assert.throws(() => access.assert(scope, canvasRequestIdentity("/canvas-projects/copy")), /权限/);
});

test("历史条目和媒体的 404 不被当作整张画布撤权", () => {
    assert.equal(canvasRequestIdentity("/canvas-projects/a"), "a");
    assert.equal(canvasRequestIdentity("/canvas-projects/a/viewport"), "a");
    assert.equal(canvasRequestIdentity("/canvas-projects/a/view-preferences"), "a");
    assert.equal(canvasRequestIdentity("/ops/canvas.document.commit", { params: { canvasId: "a" } }), "a");
    assert.equal(canvasRequestIdentity("/canvas-projects/a/history/missing"), undefined);
    assert.equal(canvasRequestIdentity("/resources/missing"), undefined);
});
