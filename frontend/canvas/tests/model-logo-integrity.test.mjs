import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../vendor/lobehub-icons");

test("模型 Logo 保留全部原版选项、字节和许可证，子集不依赖 UI 库", () => {
    const upstream = JSON.parse(readFileSync(resolve(root, "upstream.json"), "utf8"));
    const toc = JSON.parse(readFileSync(resolve(root, "es/toc.json"), "utf8"));
    const models = toc.filter((item) => ["model", "provider", "application"].includes(item.group));
    assert.equal(upstream.version, "5.16.0");
    assert.equal(models.length, 321);
    assert.deepEqual(models.map((item) => item.id), upstream.models);
    assert.deepEqual(upstream.external_dependencies, ["es-toolkit", "react", "react/jsx-runtime"]);
    for (const [name, digest] of Object.entries(upstream.files)) {
        const bytes = readFileSync(resolve(root, name));
        assert.equal(createHash("sha256").update(bytes).digest("hex"), digest, name);
        if (name.endsWith(".js")) assert.doesNotMatch(bytes.toString("utf8"), /["'](?:antd|@lobehub\/ui)["']/);
    }
    for (const model of models) assert.ok(upstream.files[`es/${model.id}/components/Mono.js`]);
    assert.match(readFileSync(resolve(root, "LICENSE"), "utf8"), /Copyright \(c\) 2023 LobeHub/);
});
