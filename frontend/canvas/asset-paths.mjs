export function assetPrefixes(files) {
    const prefixes = new Set();
    for (const name of Object.keys(files)) {
        if (!name.startsWith("public/")) continue;
        const parts = name.slice(7).split("/");
        prefixes.add(parts.length > 2 ? parts.slice(0, 2).join("/") + "/" : parts.join("/"));
    }
    return [...prefixes].sort((left, right) => right.length - left.length);
}

export function rebaseAssetLiterals(code, prefixes, base) {
    const prefix = "/" + base.replace(/^\/+|\/+$/g, "") + "/";
    return code.replace(/(["'\x60])\/([^"'\x60\s]*)/g, (match, quote, path) => {
        if (!prefixes.some((item) => item.endsWith("/") ? path.startsWith(item) : path === item)) return match;
        return quote + prefix + path;
    });
}
