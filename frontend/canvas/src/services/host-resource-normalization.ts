import type { CanvasResourceNormalizeDto } from "@host/api/types/canvases";
import { canonicalizeCanvasMedia } from "./canvas-media-locators.ts";

const maximumId = "18446744073709551615";
const identifier = (value: unknown): value is string => typeof value === "string" && /^[1-9][0-9]{0,19}$/.test(value) && (value.length < maximumId.length || value <= maximumId);
const locatorFields = new Set("storageKey content previewContent drawingPreviewStorageKey drawingPreviewUrl url dataUrl coverUrl imageUrl videoUrl audioUrl referenceUrl referenceUrls artifactRef providerArtifactRef".split(" "));
const idFields = new Set("resourceId resourceIds sampleResourceId referenceResourceId referenceResourceIds".split(" "));
type Reference = { id: string; url?: URL; suffix?: string; bare?: boolean };
type WalkCache = WeakMap<object, Map<string, unknown>>;

function reference(value: string, field: string): Reference | undefined {
    const text = value.trim();
    let id: string | undefined;
    let url: URL | undefined;
    let suffix: string | undefined;
    const bare = idFields.has(field) && /^[1-9][0-9]*$/.test(text);
    if (bare) id = text;
    else if (/^resource:[1-9][0-9]*$/.test(text)) id = text.slice("resource:".length);
    else if ((locatorFields.has(field) || idFields.has(field)) && /^(?:\/|https?:)/i.test(text)) {
        try {
            url = new URL(text, "https://canvas.invalid");
            if (url.protocol !== "http:" && url.protocol !== "https:") return;
            const match = /^\/api\/(?:v1\/canvas-runtime\/)?resources\/([1-9][0-9]*)(\/.*)?$/.exec(url.pathname);
            if (!match) return;
            id = match[1];
            suffix = match[2] || "";
        } catch {
            return;
        }
    }
    if (!id) return;
    if (!identifier(id)) throw new Error("画布资源 ID 超出有效范围");
    return { id, url, suffix, bare };
}

function walk(value: unknown, field: string, transform: (value: string, field: string) => string, parents = new Set<object>(), cache: WalkCache = new WeakMap()): unknown {
    if (typeof value === "string") return transform(value, field);
    if (!value || typeof value !== "object") return value;
    if (parents.has(value) || parents.size >= 64) throw new Error("画布资源结构无法保存，请检查嵌套内容");
    const context = idFields.has(field) ? "id" : locatorFields.has(field) ? "locator" : "other";
    const cached = cache.get(value);
    if (cached?.has(context)) return cached.get(context);
    const remember = (result: unknown) => {
        const entries = cache.get(value) || new Map<string, unknown>();
        entries.set(context, result);
        cache.set(value, entries);
        return result;
    };
    parents.add(value);
    try {
        let changed = false;
        if (Array.isArray(value)) {
            const items = value.map(item => {
                const next = walk(item, field, transform, parents, cache);
                if (next !== item) changed = true;
                return next;
            });
            return remember(changed ? items : value);
        }
        const entries = Object.entries(value).map(([key, item]) => {
            const next = walk(item, /^\d+$/.test(key) ? field : key, transform, parents, cache);
            if (next !== item) changed = true;
            return [key, next];
        });
        return remember(changed ? Object.fromEntries(entries) : value);
    } finally {
        parents.delete(value);
    }
}

export function collectCanvasResourceIds(value: unknown) {
    const ids = new Set<string>();
    walk(value, "", (item, field) => {
        const found = reference(item, field);
        if (found) ids.add(found.id);
        return item;
    });
    return [...ids].sort();
}

/** Replace only resource values. Reordered/new/deleted nodes in the live graph keep their edits. */
export function remapCanvasResourceReferences<T>(document: T, mapping: Record<string, string>): T {
    return createCanvasResourceRemapper(mapping)(document);
}

export type CanvasResourceRemapper = <T>(document: T) => T;

/** Share one short-lived mapper across editor/history so reference-based patches stay stable. */
export function createCanvasResourceRemapper(mapping: Record<string, string>): CanvasResourceRemapper {
    if (Object.entries(mapping).some(([key, value]) => !identifier(key) || !identifier(value))) throw new Error("画布资源映射无效");
    const cache: WalkCache = new WeakMap();
    const transform = (value: string, field: string) => {
        const found = reference(value, field);
        const next = found && mapping[found.id];
        if (!found || !next || next === found.id) return value;
        if (found.bare) return next;
        if (!found.url) return `resource:${next}`;
        // File URLs remain usable, but old origin/signature parameters cannot
        // become the copied file's durable identity. Keep the source playback options.
        const query = new URLSearchParams();
        if (found.url.searchParams.get("variant") === "playback") query.set("variant", "playback");
        if (found.url.searchParams.get("proxy") === "1") query.set("proxy", "1");
        const search = query.size ? `?${query}` : "";
        return `/api/v1/canvas-runtime/resources/${next}${found.suffix}${search}${found.url.hash}`;
    };
    return <T>(document: T) => walk(document, "", transform, new Set(), cache) as T;
}

export function validateCanvasResourceMapping(ids: string[], result: CanvasResourceNormalizeDto) {
    const mapping = result?.resource_map;
    const aliases = result?.resource_aliases;
    if (!mapping || Array.isArray(mapping) || typeof mapping !== "object" || !aliases || Array.isArray(aliases) || typeof aliases !== "object") throw new Error("画布资源归一化响应无效");
    const expected = new Set(ids);
    const targets = new Set(Object.values(mapping));
    if (Object.keys(mapping).length !== expected.size || Object.entries(mapping).some(([key, value]) => !expected.has(key) || !identifier(value))
        || Object.entries(aliases).some(([key, values]) => !targets.has(key) || !identifier(key) || !Array.isArray(values) || values.some(value => !identifier(value)))
        || ids.some(id => mapping[id] !== id && !aliases[mapping[id]]?.includes(id))) throw new Error("画布资源归一化响应不完整，原稿已保留");
}

export async function prepareCanvasDocumentResources<T>(document: T, options: {
    assertActive: () => void;
    normalize: (ids: string[]) => Promise<CanvasResourceNormalizeDto>;
}) {
    options.assertActive();
    // Freeze before the first await. New input belongs to a later save and must
    // never change the request currently being prepared.
    const snapshot = canonicalizeCanvasMedia(structuredClone(document));
    const ids = collectCanvasResourceIds(snapshot);
    const resourceMap: Record<string, string> = {};
    const resourceAliases: Record<string, string[]> = {};
    for (let start = 0; start < ids.length; start += 200) {
        options.assertActive();
        const batch = ids.slice(start, start + 200);
        const result = await options.normalize(batch);
        options.assertActive();
        validateCanvasResourceMapping(batch, result);
        Object.assign(resourceMap, result.resource_map);
        Object.assign(resourceAliases, result.resource_aliases);
    }
    return { document: remapCanvasResourceReferences(snapshot, resourceMap), resourceMap, resourceAliases };
}
