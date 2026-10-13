import assert from "node:assert/strict";
import test from "node:test";
import { archiveAttemptStore } from "../src/services/host-archive-attempt.ts";

function storage() {
    const values = new Map();
    return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}
const original = () => ({ version: 1, digest: "a".repeat(64), title: "中文画布.zip",
    items: [{ sourceId: "source-a", workspaceId: "group" }, { sourceId: "source-b", workspaceId: "group" }],
    folders: [{ sourceId: "folder-a" }],
});

test("未知导入保存原创建ID，多画布重开仍用同一计划", () => {
    const cache = storage(), attempts = archiveAttemptStore(cache, "9007199254740993");
    const frozen = attempts.prepare(original());
    frozen.items[0].importedId = "created-original";
    frozen.folders[0].importedId = "folder-original";
    attempts.write(frozen);
    const reloaded = archiveAttemptStore(cache, "9007199254740993").prepare(original());
    assert.deepEqual(reloaded, frozen);
    assert.equal(reloaded.items[1].importedId, undefined);
});

test("未核对时其他归档不能替换原计划或清除原键", () => {
    const attempts = archiveAttemptStore(storage(), "11");
    const before = attempts.prepare(original());
    assert.throws(() => attempts.prepare({ ...original(), digest: "b".repeat(64) }), /上次导入尚未核对/);
    assert.throws(() => attempts.clear("b".repeat(64)), /未清除原记录/);
    assert.deepEqual(attempts.read(), before);
    attempts.clear(before.digest);
    assert.equal(attempts.read(), null);
});

test("不同账号不能读取或覆盖他人的导入身份", () => {
    const cache = storage();
    const owner = archiveAttemptStore(cache, "11"), member = archiveAttemptStore(cache, "12");
    owner.prepare(original());
    assert.equal(member.read(), null);
    member.prepare({ ...original(), digest: "b".repeat(64) });
    member.clear("b".repeat(64));
    assert.equal(owner.read().digest, "a".repeat(64));
});

test("损坏或未来核对记录明确停止，不当作没有待处理导入", () => {
    const cache = storage(), attempts = archiveAttemptStore(cache, "11");
    for (const value of ["broken", JSON.stringify({ ...original(), version: 2 }), JSON.stringify({ ...original(), items: [original().items[0], original().items[0]] }), JSON.stringify({ ...original(), items: [{ ...original().items[0], importedId: 42 }] })]) {
        cache.setItem("canvas-archive-attempt:v1:11", value);
        assert.throws(() => attempts.prepare(original()), /核对记录损坏/);
        assert.equal(cache.getItem("canvas-archive-attempt:v1:11"), value);
    }
});

test("无法持久化原创建身份时在导入前失败", () => {
    const cache = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
    assert.throws(() => archiveAttemptStore(cache, "11").prepare(original()), /无法保存导入核对记录/);
    assert.throws(() => archiveAttemptStore(cache, "11.1"), /当前账号身份无效/);
});
