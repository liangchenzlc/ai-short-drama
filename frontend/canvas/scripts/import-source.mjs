import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, extname, isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourceArgument = process.argv.indexOf("--source");
if (sourceArgument < 0 || !process.argv[sourceArgument + 1]) {
    throw new Error("请指定 --source <BeefTV 仓库目录>；添加 --write 才迁入文件。");
}
const sourceRoot = resolve(process.argv[sourceArgument + 1]);
const sourceWeb = resolve(sourceRoot, "web");
const expectedCommit = "4ca2a65a7780a8dfcaaa86c33679f84fb04e055c";
const commit = execFileSync("git", ["rev-parse", "HEAD"], { cwd: sourceRoot, encoding: "utf8" }).trim();
if (commit !== expectedCommit) throw new Error("BeefTV 版本与已确认基准不同，请先更新对照基准。");
if (execFileSync("git", ["status", "--porcelain"], { cwd: sourceRoot, encoding: "utf8" }).trim()) {
    throw new Error("BeefTV 工作区存在改动，不能以固定 commit 的名义迁入。");
}
// TypeScript 7's native package does not expose the JavaScript compiler API.
// The host project's pinned TypeScript scanner reads imports without executing source modules.
const compilerRequire = createRequire(resolve(packageRoot, "../package.json"));
const typescript = compilerRequire("typescript");
const entries = [
    "index.html",
    "src/main.tsx",
    "src/pages/canvas/project.tsx",
    "src/components/layout/app-providers.tsx",
    "src/layouts/user-layout.tsx",
    "src/lib/plugins/builtin/index.ts",
    "src/styles/globals.css",
    "src/styles/beeftv-local-overrides.css",
    "src/vite-env.d.ts",
];
const localBoundaries = new Set(["src/router.tsx"]);
const sharedContract = {
    source: "backend/internal/providerpreset/video_contracts.json",
    destination: "src/vendor/beeftv-video-contracts.json",
    oldImport: "../../../backend/internal/providerpreset/video_contracts.json",
    newImport: "@/vendor/beeftv-video-contracts.json",
};
const visited = new Map();
const externalPackages = new Set();
const missing = [];
const apiCalls = [];
const diagnostics = [];
const queue = entries.map((name) => resolve(sourceWeb, name));
const normalize = (name) => name.split(sep).join("/");
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");

function within(root, path) {
    const child = relative(root, path);
    return child !== "" && !child.startsWith("..") && !isAbsolute(child);
}

function resolveImport(file, specifier) {
    if (specifier === sharedContract.oldImport) {
        const bytes = readFileSync(resolve(sourceRoot, sharedContract.source));
        visited.set(sharedContract.destination, {
            source: sharedContract.source, sha256: digest(bytes),
            upstream_sha256: digest(bytes), bytes: bytes.length,
        });
        return;
    }
    const bare = specifier.split("?", 1)[0];
    if (!bare.startsWith(".") && !bare.startsWith("@/")) {
        if (!bare.startsWith("node:") && !bare.startsWith("/") && !bare.includes(":")) {
            externalPackages.add(bare.startsWith("@") ? bare.split("/").slice(0, 2).join("/") : bare.split("/")[0]);
        }
        return;
    }
    const base = bare.startsWith("@/") ? resolve(sourceWeb, "src", bare.slice(2)) : resolve(dirname(file), bare);
    const variants = [base, ...[".ts", ".tsx", ".js", ".jsx", ".mjs", ".json", ".css"].map((suffix) => base + suffix),
        ...["index.ts", "index.tsx", "index.js"].map((name) => resolve(base, name))];
    const candidate = variants.find((path) => existsSync(path) && !readdirSafe(path));
    if (!candidate || !within(sourceWeb, candidate)) {
        missing.push({ file: normalize(relative(sourceWeb, file)), specifier });
        return;
    }
    if (!localBoundaries.has(normalize(relative(sourceWeb, candidate)))) queue.push(candidate);
}

function readdirSafe(path) {
    try { readdirSync(path); return true; } catch { return false; }
}

while (queue.length) {
    const file = queue.pop();
    const name = normalize(relative(sourceWeb, file));
    if (visited.has(name)) continue;
    const bytes = readFileSync(file);
    const adapted = sourceBytes("web/" + name, bytes);
    visited.set(name, {
        source: "web/" + name, sha256: digest(adapted),
        upstream_sha256: digest(bytes), bytes: adapted.length,
    });
    if (!/\.(?:[cm]?[jt]sx?|css)$/.test(file)) continue;
    const content = bytes.toString("utf8");
    if (extname(file) === ".css") {
        for (const match of content.matchAll(/@import\s+["']([^"']+)["']/g)) resolveImport(file, match[1]);
    } else {
        for (const imported of typescript.preProcessFile(content, true, true).importedFiles) {
            resolveImport(file, imported.fileName);
        }
        for (const match of content.matchAll(/new URL\(\s*["']([^"']+)["']\s*,\s*import\.meta\.url\s*\)/g)) {
            resolveImport(file, match[1]);
        }
    }
    for (const match of content.matchAll(/\bhttp\.(get|post|put|patch|delete)\s*(?:<[^;\n]+?>)?\s*\(\s*(["'\x60])([^"'\x60]+)\2/g)) {
        apiCalls.push({ file: name, method: match[1].toUpperCase(), path: match[3] });
    }
    const debugCalls = content.match(/\bconsole\.(?:log|debug)\s*\(/g)?.length ?? 0;
    const unfinishedNotes = content.match(/\bTODO\b/g)?.length ?? 0;
    if (debugCalls || unfinishedNotes) diagnostics.push({ file: name, debugCalls, unfinishedNotes });
}
if (missing.length) throw new Error("未解析的源码依赖：" + JSON.stringify(missing));

function collectAssets(directory) {
    for (const item of readdirSync(directory, { withFileTypes: true })) {
        const file = resolve(directory, item.name);
        if (item.isSymbolicLink()) throw new Error("静态资源不允许隐式跟随符号链接：" + file);
        if (item.isDirectory()) {
            if (normalize(relative(sourceWeb, file)) !== "public/welcome") collectAssets(file);
        } else {
            const bytes = readFileSync(file);
            visited.set(normalize(relative(sourceWeb, file)), {
                source: "web/" + normalize(relative(sourceWeb, file)), sha256: digest(bytes), bytes: bytes.length,
            });
        }
    }
}
collectAssets(resolve(sourceWeb, "public"));
for (const name of ["LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "VERSION", "CHANGELOG.md"]) {
    const bytes = readFileSync(resolve(sourceRoot, name));
    visited.set("vendor/beeftv/" + name, { source: name, sha256: digest(bytes), bytes: bytes.length });
}
const sourcePackage = JSON.parse(readFileSync(resolve(sourceWeb, "package.json"), "utf8"));
const versions = {};
for (const name of Object.keys({ ...sourcePackage.dependencies, ...sourcePackage.devDependencies }).sort()) {
    const file = resolve(sourceWeb, "node_modules", name, "package.json");
    if (!existsSync(file)) throw new Error("源依赖尚未安装，无法锁定实际版本：" + name);
    versions[name] = JSON.parse(readFileSync(file, "utf8")).version;
}
const files = Object.fromEntries([...visited.entries()].sort(([left], [right]) => left.localeCompare(right)));
const manifest = {
    repository: "https://github.com/glanderness/BeefTV",
    commit, sourceLockSha256: digest(readFileSync(resolve(sourceWeb, "bun.lock"))),
    entries, localBoundaries: [...localBoundaries], versions,
    externalPackages: [...externalPackages].sort(), files, apiCalls, diagnostics,
};
const shouldWrite = process.argv.includes("--write");
function sourceBytes(source, bytes) {
    if (source === "web/src/lib/beefapi-video-contracts.ts") {
        return Buffer.from(bytes.toString("utf8").replace(sharedContract.oldImport, sharedContract.newImport));
    }
    return bytes;
}
if (shouldWrite) {
    const adaptationsPath = resolve(packageRoot, "source-adaptations.json");
    const adaptations = existsSync(adaptationsPath) ? JSON.parse(readFileSync(adaptationsPath, "utf8")) : {};
    for (const name of Object.keys(files)) {
        if (adaptations[name]?.action === "removed") throw new Error("目标文件已按画布边界物理删除，拒绝重新导入整站源码：" + name);
    }
    for (const [name, metadata] of Object.entries(files)) {
        const destination = resolve(packageRoot, name);
        if (!within(packageRoot, destination)) throw new Error("输出路径越界：" + name);
        if (existsSync(destination) && digest(readFileSync(destination)) !== metadata.sha256) {
            throw new Error("目标文件已经有适配改动，拒绝覆盖：" + name);
        }
    }
    for (const [name, metadata] of Object.entries(files)) {
        const destination = resolve(packageRoot, name);
        mkdirSync(dirname(destination), { recursive: true });
        writeFileSync(destination, sourceBytes(metadata.source, readFileSync(resolve(sourceRoot, metadata.source))));
    }
    writeFileSync(resolve(packageRoot, "source-manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
}
process.stdout.write(JSON.stringify({
    commit, written: shouldWrite, files: visited.size,
    bytes: [...visited.values()].reduce((sum, item) => sum + item.bytes, 0),
    apiCalls: apiCalls.length, diagnostics,
}, null, 2) + "\n");
