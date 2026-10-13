import assert from "node:assert/strict";
import test from "node:test";
import { zipSync } from "fflate";
import { createZip, readZip } from "../src/lib/zip.ts";

const bytes = (value) => new TextEncoder().encode(value);
const archive = (entries) => new Blob([zipSync(entries, { level: 0 })]);

test("ZIP重新导入保留媒体原字节、中文路径和正文", async () => {
    const image = new Uint8Array([137, 80, 78, 71, 0, 255]);
    const zip = await createZip([
        { name: "项目/画布.json", data: JSON.stringify({ title: "重导入", resource: "media/图.png" }) },
        { name: "media/图.png", data: image },
    ]);
    const imported = await readZip(zip);
    assert.equal(imported.size, 2);
    assert.deepEqual(new Uint8Array(await imported.get("media/图.png").arrayBuffer()), image);
    assert.deepEqual(JSON.parse(await imported.get("项目/画布.json").text()), { title: "重导入", resource: "media/图.png" });
});

test("合法ZIP的完全同名条目必须在解压覆盖前拒绝", async () => {
    const data = zipSync({ "first.txt": bytes("original"), "other.txt": bytes("replacement") }, { level: 0 });
    const needle = bytes("other.txt"), replacement = bytes("first.txt");
    let changed = 0;
    for (let offset = 0; offset <= data.length - needle.length; offset++) {
        if (needle.every((value, index) => data[offset + index] === value)) {
            data.set(replacement, offset);
            changed++;
        }
    }
    assert.equal(changed, 2, "仅替换合法ZIP的本地头和中央目录文件名");
    await assert.rejects(readZip(new Blob([data])), /重名文件.*first\.txt/);
});

test("ZIP规范化别名冲突及越界路径不能覆盖导入文件", async () => {
    await assert.rejects(readZip(archive({ "doc/a.txt": bytes("first"), "doc/./a.txt": bytes("second") })), /重名文件/);
    for (const name of ["../outside.txt", "/absolute.txt", "C:/outside.txt", "doc\\outside.txt"]) {
        await assert.rejects(readZip(archive({ [name]: bytes("invalid") })), /非法路径|越界路径/);
    }
    await assert.rejects(createZip([{ name: "a.txt", data: "one" }, { name: "./a.txt", data: "two" }]), /重名文件/);
});

test("损坏ZIP明确失败，不返回空备份", async () => {
    await assert.rejects(readZip(new Blob([bytes("not a zip")])), /备份已损坏/);
});
