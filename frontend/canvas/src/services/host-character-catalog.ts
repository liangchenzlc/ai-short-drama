import type { CanvasCharacterCatalogDetailRead, CanvasCharacterCatalogItemRead, CanvasCharacterCatalogRead } from "@host/api/types/canvas-character-catalog";
import type { CharacterCardSummary } from "@/services/api/projects";
import { readCanvasCharacterImages } from "./host-character-images.ts";

export type CanvasCharacterContext = {
    canvas_key: string;
    canvas_row_version: string;
    source_project_id: string;
};

export type CanvasProjectCharacterCard = {
    id: string;
    title: string;
    category: "character";
    position: number;
    folderId?: string;
    character: CharacterCardSummary;
};

export type CanvasCharacterReadParams = {
    expected_version_id?: string;
    requested_version_id?: string;
    version_policy?: "current" | "pinned";
};
export type CanvasCharacterTransport = {
    get(path: string, options?: { params?: CanvasCharacterReadParams }): Promise<unknown>;
};
type HostCharacterHttp = {
    get(path: string, options: { params?: CanvasCharacterReadParams; headers: { "X-Canvas-Actor": string } }): Promise<{ data: unknown }>;
};

/** Host HTTP already owns envelope/error handling; never pass these paths through the source prefixer. */
export function createCanvasCharacterTransport(host: HostCharacterHttp, actor: string, assertCurrent: () => void): CanvasCharacterTransport {
    return {
        async get(path, options) {
            assertCurrent();
            if (!path.startsWith("/canvas-runtime/canvas-projects/") || path.includes("/canvas-runtime/canvas-runtime/")) throw new Error("角色读取路径无效");
            const response = await host.get(path, { ...options, headers: { "X-Canvas-Actor": actor } });
            assertCurrent();
            return response.data;
        },
    };
}

const maximumId = "18446744073709551615";
const identifier = (value: unknown): value is string => typeof value === "string" && /^[1-9][0-9]{0,19}$/.test(value) && (value.length < maximumId.length || value <= maximumId);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const exactKeys = (value: Record<string, unknown>, keys: readonly string[]) => Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const integer = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0;
const roles = ["primary", "front", "side", "back", "turnaround_sheet", "expression_sheet"];

function json(value: unknown, parents = new Set<object>()): boolean {
    if (value === null || typeof value === "string" || typeof value === "boolean") return true;
    if (typeof value === "number") return Number.isFinite(value);
    if (!value || typeof value !== "object" || parents.has(value) || parents.size >= 64) return false;
    parents.add(value);
    const valid = (Array.isArray(value) ? value : Object.values(value)).every(item => json(item, parents));
    parents.delete(value);
    return valid;
}

function contextValid(context: CanvasCharacterContext) {
    return typeof context.canvas_key === "string" && Array.from(context.canvas_key).length <= 64
        && /^[\p{Alphabetic}\p{Mark}\p{Decimal_Number}\p{Connector_Punctuation}\u200C\u200D-]+$/u.test(context.canvas_key)
        && identifier(context.canvas_row_version) && identifier(context.source_project_id);
}

function itemValid(value: unknown): value is CanvasCharacterCatalogItemRead {
    if (!record(value) || !exactKeys(value, ["asset_id", "asset_row_version", "current_version_id", "version_id", "version", "position", "name", "label", "description", "prompt", "tags", "definition", "definition_origin", "representations", "voice_status", "voice_sample"])
        || ![value.asset_id, value.asset_row_version, value.current_version_id, value.version_id, value.version].every(identifier)
        || !integer(value.position) || ![value.name, value.label, value.description, value.prompt].every(item => typeof item === "string")
        || !Array.isArray(value.tags) || !value.tags.every(tag => typeof tag === "string")
        || !record(value.definition) || !json(value.definition)
        || !["legacy_current", "structured"].includes(value.definition_origin as string) || !Array.isArray(value.representations)) return false;
    if (!["missing", "ready", "unavailable"].includes(value.voice_status as string)) return false;
    const voice = value.voice_sample;
    if (value.voice_status !== "ready" && voice !== null) return false;
    if (value.voice_status === "ready") {
        if (!record(voice) || !exactKeys(voice, ["binding_version", "media_id", "checksum_sha256", "duration_ms", "resource"])
            || !identifier(voice.binding_version) || !identifier(voice.media_id)
            || typeof voice.checksum_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(voice.checksum_sha256)
            || !integer(voice.duration_ms) || (voice.duration_ms as number) < 3000 || (voice.duration_ms as number) > 7500) return false;
        const resource = voice.resource;
        if (!record(resource) || !exactKeys(resource, ["id", "userId", "kind", "status", "provider", "endpoint", "bucket", "objectKey", "publicUrl", "mimeType", "size", "width", "height", "durationMs", "etag", "createdAt", "updatedAt"])
            || resource.id !== voice.media_id || !identifier(resource.userId) || resource.kind !== "audio"
            || resource.status !== "ready" || resource.provider !== "minio" || resource.durationMs !== voice.duration_ms
            || resource.etag !== voice.checksum_sha256 || ![resource.endpoint, resource.bucket, resource.objectKey, resource.publicUrl].every(value => value === "")
            || typeof resource.mimeType !== "string" || !/^audio\/[a-zA-Z0-9!#$&^_.+\-]+$/.test(resource.mimeType)
            || !integer(resource.size) || (resource.size as number) <= 0 || (resource.size as number) > 10 * 1024**2
            || ![resource.width, resource.height].every(integer)
            || ![resource.createdAt, resource.updatedAt].every(value => typeof value === "string" && Number.isFinite(Date.parse(value)))) return false;
    }
    const slots = value.representations;
    return slots.every(slot => record(slot) && exactKeys(slot, ["reference_id", "role", "ordinal", "media_id", "format_code", "mime_type", "original_name", "byte_size", "width", "height"])
        && identifier(slot.reference_id) && identifier(slot.media_id) && roles.includes(slot.role as string) && integer(slot.ordinal)
        && typeof slot.format_code === "string" && (slot.mime_type === null || typeof slot.mime_type === "string") && typeof slot.original_name === "string"
        && [slot.byte_size, slot.width, slot.height].every(number => number === null || integer(number)))
        && new Set(slots.map(slot => slot.reference_id)).size === slots.length;
}

function sameContext(value: Record<string, unknown>, expected: CanvasCharacterContext) {
    return value.canvas_key === expected.canvas_key && value.canvas_row_version === expected.canvas_row_version && value.source_project_id === expected.source_project_id;
}

function capture(context: CanvasCharacterContext, assertCurrent: () => void) {
    assertCurrent();
    if (!contextValid(context)) throw new Error("画布尚未绑定可读取的项目角色来源");
    return structuredClone(context);
}

async function card(transport: CanvasCharacterTransport, context: CanvasCharacterContext, item: CanvasCharacterCatalogItemRead, assertCurrent: () => void, versionPolicy: "current" | "pinned" = "current"): Promise<CanvasProjectCharacterCard> {
    const images = await readCanvasCharacterImages(transport, { ...context, asset_id: item.asset_id, version_id: item.version_id, ...(versionPolicy === "pinned" ? { version_policy: versionPolicy } : {}), representations: item.representations.map(({ reference_id, role, ordinal, media_id }) => ({ reference_id, role, ordinal, media_id })) }, assertCurrent);
    const represented = new Set<string>(images.images.map(image => image.role));
    const visualStatus = represented.has("turnaround_sheet") || ["front", "side", "back"].every(role => represented.has(role)) ? "ready" : images.images.length ? "partial" : "missing";
    return {
        id: item.asset_id, title: item.name, category: "character", position: item.position,
        character: {
            versionId: item.version_id, version: item.version,
            definition: structuredClone(item.definition),
            representations: images.images.map(image => ({ id: image.reference_id, resourceId: image.resource.id, mediaType: "image", role: image.role })),
            visualStatus, voiceStatus: item.voice_status,
            ...(item.voice_sample ? { voice: { profile: {
                id: item.voice_sample.media_id, name: `${item.name} · 已采用样音`, provider: "已采用样音",
                voiceKey: "", bindingVersion: item.voice_sample.binding_version,
                language: typeof item.definition.voiceLanguage === "string" ? item.definition.voiceLanguage : "",
                timbre: typeof item.definition.voiceTimbre === "string" ? item.definition.voiceTimbre : "",
                sampleResourceId: item.voice_sample.media_id, compatibleModels: [], status: "ready",
            }, instructions: "" } } : {}),
        },
    };
}

export async function readCanvasCharacterCatalog(transport: CanvasCharacterTransport, context: CanvasCharacterContext, assertCurrent: () => void): Promise<CanvasProjectCharacterCard[]> {
    const expected = capture(context, assertCurrent);
    const response = await transport.get(`/canvas-runtime/canvas-projects/${encodeURIComponent(expected.canvas_key)}/character-catalog`);
    assertCurrent();
    if (JSON.stringify(context) !== JSON.stringify(expected) || !record(response)
        || !exactKeys(response, ["canvas_key", "canvas_row_version", "source_project_id", "items"]) || !sameContext(response, expected)
        || !Array.isArray(response.items) || !response.items.every(item => itemValid(item) && item.version_id === item.current_version_id) || new Set(response.items.map(item => item.asset_id)).size !== response.items.length) throw new Error("角色目录与当前画布来源或版本不匹配，请重新读取");
    const catalog = structuredClone(response) as unknown as CanvasCharacterCatalogRead;
    const result: CanvasProjectCharacterCard[] = [];
    for (const item of catalog.items) {
        assertCurrent();
        result.push(await card(transport, expected, item, assertCurrent));
    }
    assertCurrent();
    if (JSON.stringify(context) !== JSON.stringify(expected)) throw new Error("角色读取上下文已变化");
    return result;
}

export async function readCanvasCharacterDetail(transport: CanvasCharacterTransport, context: CanvasCharacterContext, assetId: string, assertCurrent: () => void, selectedVersion?: string, requestedVersionId?: string): Promise<CanvasProjectCharacterCard> {
    const expected = capture(context, assertCurrent);
    if (!identifier(assetId) || [selectedVersion, requestedVersionId].some(version => version !== undefined && !identifier(version))) throw new Error("角色读取标识无效");
    const response = await transport.get(`/canvas-runtime/canvas-projects/${encodeURIComponent(expected.canvas_key)}/character-catalog/${assetId}`, requestedVersionId ? { params: { requested_version_id: requestedVersionId } } : undefined);
    assertCurrent();
    if (JSON.stringify(context) !== JSON.stringify(expected) || !record(response)
        || !exactKeys(response, ["canvas_key", "canvas_row_version", "source_project_id", "item"]) || !sameContext(response, expected)
        || !itemValid(response.item) || response.item.asset_id !== assetId
        || requestedVersionId !== undefined && response.item.version_id !== requestedVersionId
        || requestedVersionId === undefined && response.item.version_id !== response.item.current_version_id
        || selectedVersion !== undefined && response.item.version_id !== selectedVersion) throw new Error("所选角色当前版本已变化，请刷新角色后重新选择");
    const result = await card(transport, expected, structuredClone((response as unknown as CanvasCharacterCatalogDetailRead).item), assertCurrent, requestedVersionId ? "pinned" : "current");
    assertCurrent();
    if (JSON.stringify(context) !== JSON.stringify(expected)) throw new Error("角色读取上下文已变化");
    return result;
}


export async function readCanvasCharacterReferenceCards(transport: CanvasCharacterTransport, canvasKey: string, sourceProjectId: string, references: Array<{ assetId: string; requestedVersionId?: string }>, assertCurrent: () => void): Promise<CanvasProjectCharacterCard[]> {
    assertCurrent();
    if (!contextValid({ canvas_key: canvasKey, canvas_row_version: "1", source_project_id: sourceProjectId })
        || !references.every(reference => identifier(reference.assetId) && (reference.requestedVersionId === undefined || identifier(reference.requestedVersionId)))) throw new Error("角色生成引用身份无效");
    const captured = structuredClone(references);
    const response = await transport.get(`/canvas-runtime/canvas-projects/${encodeURIComponent(canvasKey)}/character-catalog`);
    assertCurrent();
    if (!record(response) || !exactKeys(response, ["canvas_key", "canvas_row_version", "source_project_id", "items"])
        || response.canvas_key !== canvasKey || response.source_project_id !== sourceProjectId || !identifier(response.canvas_row_version)
        || !Array.isArray(response.items) || !response.items.every(item => itemValid(item) && item.version_id === item.current_version_id)
        || new Set(response.items.map(item => item.asset_id)).size !== response.items.length) throw new Error("角色生成引用来源已变化，请重新读取");
    const context = { canvas_key: canvasKey, canvas_row_version: response.canvas_row_version, source_project_id: sourceProjectId };
    const cards: CanvasProjectCharacterCard[] = [];
    for (const reference of captured) {
        assertCurrent();
        if (JSON.stringify(references) !== JSON.stringify(captured)) throw new Error("角色生成引用已变化");
        const selected = response.items.find(item => item.asset_id === reference.assetId);
        if (!selected) throw new Error("角色未关联到当前画布的可信来源");
        cards.push(await readCanvasCharacterDetail(transport, context, reference.assetId, assertCurrent,
            reference.requestedVersionId || selected.version_id, reference.requestedVersionId));
    }
    assertCurrent();
    if (JSON.stringify(references) !== JSON.stringify(captured)) throw new Error("角色生成引用已变化");
    return cards;
}
