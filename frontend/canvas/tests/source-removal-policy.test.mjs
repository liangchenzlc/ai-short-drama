import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const runtime = resolve(root, ".runtime");
mkdirSync(runtime, { recursive: true });
const bytes = "export const original = true;\n";
const hash = createHash("sha256").update(bytes).digest("hex");
const name = "src/pages/home/index.tsx";

function fixture(run) {
    const directory = mkdtempSync(resolve(runtime, "source-removal-test-"));
    mkdirSync(resolve(directory, "scripts"));
    copyFileSync(resolve(root, "scripts/check-source.mjs"), resolve(directory, "scripts/check-source.mjs"));
    writeFileSync(resolve(directory, "source-manifest.json"), JSON.stringify({ commit: "fixed-upstream", files: { [name]: { sha256: hash, upstream_sha256: hash } } }));
    const check = (...args) => spawnSync(process.execPath, [resolve(directory, "scripts/check-source.mjs"), ...args], { encoding: "utf8" });
    const ledger = (entry) => writeFileSync(resolve(directory, "source-adaptations.json"), JSON.stringify({ [name]: entry }));
    try { run({ directory, check, ledger }); } finally { rmSync(directory, { recursive: true, force: true }); }
}

test("未登记的源文件缺失不能通过来源检查", () => fixture(({ check }) => {
    const result = check();
    assert.equal(result.status, 1);
    assert.match(result.stderr, /存在缺失或未登记的源文件改动/u);
}));

test("明确删除登记保留固定来源哈希，后续复活文件必须使来源检查失败", () => fixture(({ directory, check }) => {
    const recorded = check("--record-removal", name, "按用户要求删除整站主页，独立画布不加载此页面。");
    assert.equal(recorded.status, 0, recorded.stderr);
    const ledger = JSON.parse(readFileSync(resolve(directory, "source-adaptations.json"), "utf8"));
    assert.equal(ledger[name].action, "removed");
    assert.equal(ledger[name].upstream_sha256, hash);
    assert.equal("sha256" in ledger[name], false);
    assert.equal(JSON.parse(recorded.stdout).removed, 1);
    mkdirSync(resolve(directory, "src/pages/home"), { recursive: true });
    writeFileSync(resolve(directory, name), bytes);
    const restored = check();
    assert.equal(restored.status, 1);
    assert.match(restored.stderr, /src\/pages\/home\/index.tsx/u);
    assert.equal(check("--record-removal", name, "再次删除").status, 1);
}));

test("删除登记必须有明确原因且匹配原来源哈希，不能绕过源文件检查", () => fixture(({ check, ledger }) => {
    for (const entry of [{ action: "removed", reason: "", upstream_sha256: hash }, { action: "removed", reason: "删除主页", upstream_sha256: "wrong" }, { reason: "删除主页", upstream_sha256: hash }]) {
        ledger(entry);
        assert.equal(check().status, 1);
    }
    assert.equal(check("--record-removal", "../outside.ts", "越界").status, 1);
    assert.equal(check("--record-removal", "src/not-imported.ts", "未在固定清单中").status, 1);
}));
