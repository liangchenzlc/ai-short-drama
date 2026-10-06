import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(readFileSync(resolve(root, "source-manifest.json"), "utf8"));
const ledgerPath = resolve(root, "source-adaptations.json");
const ledger = existsSync(ledgerPath) ? JSON.parse(readFileSync(ledgerPath, "utf8")) : {};
const digest = (path) => createHash("sha256").update(readFileSync(path)).digest("hex");
const args = process.argv.slice(2);
if (args[0] === "--record" || args[0] === "--record-removal") {
    const name = args[1];
    const reason = args.slice(2).join(" ").trim();
    const path = resolve(root, name || "");
    if (!manifest.files[name] || !reason || relative(root, path).startsWith("..") || isAbsolute(relative(root, path))) {
        throw new Error("必须指定清单内的源文件及具体适配原因。");
    }
    const upstream_sha256 = manifest.files[name].upstream_sha256 || manifest.files[name].sha256;
    if (args[0] === "--record-removal") {
        if (existsSync(path)) throw new Error("删除记录只能登记已物理删除的源文件。");
        ledger[name] = { action: "removed", reason, upstream_sha256 };
    } else {
        ledger[name] = { reason, sha256: digest(path), upstream_sha256 };
    }
    writeFileSync(ledgerPath, JSON.stringify(ledger, null, 2) + "\n");
}
const changed = [];
for (const [name, metadata] of Object.entries(manifest.files)) {
    const path = resolve(root, name);
    if (ledger[name]?.action === "removed") {
        if (existsSync(path) || !ledger[name].reason?.trim() || ledger[name].upstream_sha256 !== (metadata.upstream_sha256 || metadata.sha256)) changed.push(name);
        continue;
    }
    if (!existsSync(path) || digest(path) !== (ledger[name]?.sha256 || metadata.sha256)) changed.push(name);
}
for (const name of Object.keys(ledger)) if (!manifest.files[name]) changed.push(name);
if (changed.length) throw new Error("存在缺失或未登记的源文件改动：\n" + changed.join("\n"));
process.stdout.write(JSON.stringify({ commit: manifest.commit, files: Object.keys(manifest.files).length, adaptations: Object.keys(ledger).length, removed: Object.values(ledger).filter((entry) => entry.action === "removed").length }) + "\n");
