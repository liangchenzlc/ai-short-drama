import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";

const fixture = JSON.parse(await readFile(new URL("./fixtures/asset-contract.json", import.meta.url), "utf8"));
const moduleUrl = (source) => `data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(source)).toString("base64")}`;
const categoryUrl = moduleUrl(await readFile(new URL("../src/lib/asset-category.ts", import.meta.url), "utf8"));
// Resolve the source alias for Node; do not change any parser or category behavior.
const parser = await readFile(new URL("../src/lib/asset-record.ts", import.meta.url), "utf8");
assert.ok(parser.includes('"@/lib/asset-category"'));
const { parseAssetRecord } = await import(moduleUrl(parser.replace('"@/lib/asset-category"', JSON.stringify(categoryUrl))));

for (const example of fixture.cases) {
    test(`原版素材合同：${example.name}`, () => {
        const record = structuredClone({ ...fixture.common, ...example.input });
        if (!example.valid) {
            assert.throws(() => parseAssetRecord(record));
            return;
        }
        const parsed = parseAssetRecord(record);
        assert.deepEqual(JSON.parse(JSON.stringify(parsed.data)), example.expected_data);
        for (const [key, value] of Object.entries(example.expected_fields ?? {})) {
            assert.deepEqual(parsed[key], value);
        }
    });
}

test("原版拒绝非有限媒体数字", () => {
    for (const value of [NaN, Infinity, -Infinity]) {
        const record = structuredClone({ ...fixture.common, ...fixture.cases[2].input });
        record.data.width = value;
        assert.throws(() => parseAssetRecord(record));
    }
});
