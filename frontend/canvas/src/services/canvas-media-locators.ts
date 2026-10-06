const mediaKinds = new Set(["image", "video", "audio", "model", "panorama"]);
const textKinds = new Set(["text", "markdown", "html", "svg", "script", "skill", "config", "batch-table", "chart"]);
const maximumId = "18446744073709551615";
const derivedPairs = { drawingPreviewUrl: "drawingPreviewStorageKey", directorCoverUrl: "directorCoverStorageKey" };

function resource(value: unknown) {
    if (typeof value !== "string") return;
    const text = value.trim();
    const key = /^resource:([1-9][0-9]*)$/.exec(text);
    let url: URL | undefined;
    let match: RegExpExecArray | null = null;
    if (!key && /^(?:\/|https?:)/i.test(text)) {
        try {
            url = new URL(text, "https://canvas.invalid");
            match = /^\/api\/(?:v1\/canvas-runtime\/)?resources\/([1-9][0-9]*)(\/.*)?$/.exec(url.pathname);
        } catch { return; }
    }
    const id = key?.[1] || match?.[1];
    if (!id) return;
    if (id.length > 20 || (id.length === 20 && id > maximumId)) throw new Error("画布资源 ID 超出有效范围");
    return { id, suffix: match?.[2] || "/file", url };
}

function fileUrl(found: NonNullable<ReturnType<typeof resource>>) {
    const query = new URLSearchParams();
    if (found.url?.searchParams.get("variant") === "playback") query.set("variant", "playback");
    if (found.url?.searchParams.get("proxy") === "1") query.set("proxy", "1");
    return `/api/v1/canvas-runtime/resources/${found.id}${found.suffix}${query.size ? `?${query}` : ""}${found.url?.hash || ""}`;
}

const transient = (value: unknown) => typeof value === "string" && /^(blob:|data:)/i.test(value.trim());
const mediaKind = (value: unknown) => typeof value === "string" && mediaKinds.has(value);

/** 仅规范媒体展示字段；正文、提示词和实时编辑对象保持原值。 */
export function canonicalizeCanvasMedia<T>(document: T, strict = true): T {
    const cache = new WeakMap<object, Map<string, unknown>>();
    const parents = new Set<object>();
    function visit(value: unknown, inherited = ""): unknown {
        if (!value || typeof value !== "object") return value;
        if (parents.has(value) || parents.size >= 64) throw new Error("画布媒体结构无法保存");
        const cached = cache.get(value);
        if (cached?.has(inherited)) return cached.get(inherited);
        parents.add(value);
        try {
            let result: unknown;
            if (Array.isArray(value)) {
                const items = value.map(item => visit(item));
                result = items.some((item, index) => item !== value[index]) ? items : value;
            } else {
                const input = value as Record<string, unknown>;
                const nodeText = typeof input.type === "string" && textKinds.has(input.type);
                const isText = inherited === "text" || input.kind === "text" || nodeText;
                const media = !isText && (inherited === "media" || mediaKind(input.kind)
                    || (typeof input.mimeType === "string" && /^(image|video|audio)\//i.test(input.mimeType)));
                const nodeMedia = mediaKind(input.type);
                const next = Object.fromEntries(Object.entries(input).map(([key, item]) => [key,
                    visit(item, key === "metadata" ? (nodeMedia ? "media" : nodeText ? "text" : "") : key === "videoTrimSource" ? "media" : ""),
                ]));
                const normalize = (field: string, storageField: string, derived = false) => {
                    const original = input[field];
                    if (typeof original !== "string" || !original.trim()) return;
                    const own = resource(original);
                    const stable = resource(input[storageField]);
                    if (own) next[field] = fileUrl(own);
                    else if (stable && (transient(original) || /^https?:/i.test(original.trim()))) next[field] = fileUrl(stable);
                    else if (transient(original)) {
                        if (derived) delete next[field];
                        else if (strict) throw new Error("画布媒体尚未持久化，原有草稿已保留，请完成上传后重试");
                    }
                };
                if (media) {
                    for (const key of ["content", "url", "dataUrl"]) normalize(key, "storageKey");
                    // A pending generation preview can differ from the saved original.
                    // Never point it at the old media file just to make it persistent.
                    if (transient(input.previewContent)) delete next.previewContent;
                }
                for (const [field, storageField] of Object.entries(derivedPairs)) normalize(field, storageField, true);
                result = Object.keys(next).length !== Object.keys(input).length
                    || Object.keys(next).some(key => next[key] !== input[key]) ? next : value;
            }
            const entries = cache.get(value) || new Map<string, unknown>();
            entries.set(inherited, result);
            cache.set(value, entries);
            return result;
        } finally { parents.delete(value); }
    }
    return visit(document) as T;
}
