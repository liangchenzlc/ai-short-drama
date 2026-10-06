import assert from "node:assert/strict";
import test from "node:test";
import { createResourceIdentityRegistry } from "../src/services/host-resource-identity-registry.ts";

const author = { userScope: "author", epoch: 1 };

test("副本、二次副本和同源副本恢复同一个素材身份，超大 ID 不转成数字", () => {
    const registry = createResourceIdentityRegistry();
    const original = "18446744073709551611";
    const copy = "18446744073709551612";
    const second = "18446744073709551613";
    const sibling = "18446744073709551614";
    registry.merge(author, { [second]: [copy, original] });
    registry.merge(author, { [sibling]: [original] });
    const expected = registry.canonical(author, original);
    for (const value of [original, copy, second, sibling]) assert.equal(registry.canonical(author, value), expected);
    assert.equal(registry.canonical(author, "18446744073709551615"), "18446744073709551615");
    assert.equal(registry.canonical(author, "local:image"), "local:image");
});

test("私人来源按账号与登录 epoch 隔离，切回同一账号不能复用旧会话映射", () => {
    const registry = createResourceIdentityRegistry();
    registry.merge(author, { "20": ["10"] });
    assert.equal(registry.canonical(author, "20"), "10");
    const other = { userScope: "other", epoch: 2 };
    assert.equal(registry.canonical(other, "20"), "20");
    registry.merge(other, {});
    assert.equal(registry.canonical(author, "20"), "20");
    assert.equal(registry.canonical({ ...author, epoch: 3 }, "20"), "20");
});

test("无效来源响应不能部分污染已有映射", () => {
    const registry = createResourceIdentityRegistry();
    registry.merge(author, { "20": ["10"] });
    assert.throws(() => registry.merge(author, { "30": ["10"], "40": ["18446744073709551616"] }), /来源响应无效/);
    assert.equal(registry.canonical(author, "30"), "30");
    assert.equal(registry.canonical(author, "20"), "10");
    assert.throws(() => registry.merge(author, { "40": [10] }), /来源响应无效/);
    for (const malformed of [null, undefined, [], "invalid"]) {
        assert.throws(() => registry.merge(author, malformed), /来源响应无效/);
    }
});

test("长副本链使用迭代查找，重复来源与自引用不会形成环", () => {
    const registry = createResourceIdentityRegistry();
    const aliases = {};
    for (let index = 1; index < 15000; index += 1) aliases[String(index)] = [String(index + 1)];
    registry.merge(author, aliases);
    registry.merge(author, { "15000": ["1", "15000"] });
    assert.equal(registry.canonical(author, "1"), registry.canonical(author, "15000"));
});
