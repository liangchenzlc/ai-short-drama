import type { CanvasCharacterImagesDto, CanvasCharacterImageSlotDto } from "@host/api/types/canvas-character-images";

export type CanvasCharacterImageExpectation = {
    canvas_key: string;
    canvas_row_version: string;
    source_project_id: string;
    asset_id: string;
    version_id: string;
    version_policy?: "current" | "pinned";
    representations: CanvasCharacterImageSlotDto[];
};

export type CanvasCharacterImageTransport = {
    get(path: string, options: { params: { expected_version_id: string; version_policy?: "current" | "pinned" } }): Promise<unknown>;
};

const maximumId = "18446744073709551615";
const identifier = (value: unknown): value is string => typeof value === "string" && /^[1-9][0-9]{0,19}$/.test(value) && (value.length < maximumId.length || value <= maximumId);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const slotKeys = ["reference_id", "role", "ordinal", "media_id"] as const;
const roles = ["primary", "front", "side", "back", "turnaround_sheet", "expression_sheet"];
const slotValid = (value: unknown): value is CanvasCharacterImageSlotDto & Record<string, unknown> => record(value)
    && identifier(value.reference_id) && identifier(value.media_id)
    && typeof value.role === "string" && roles.includes(value.role)
    && Number.isSafeInteger(value.ordinal) && (value.ordinal as number) >= 0;
const exactKeys = (value: Record<string, unknown>, keys: readonly string[]) => Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const nonnegativeInteger = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0;

function validResponse(value: unknown, expected: CanvasCharacterImageExpectation): value is CanvasCharacterImagesDto {
    if (!record(value)
        || !exactKeys(value, ["canvas_key", "canvas_row_version", "source_project_id", "asset_id", "version_id", "images"])
        || value.canvas_key !== expected.canvas_key
        || value.canvas_row_version !== expected.canvas_row_version
        || value.source_project_id !== expected.source_project_id
        || value.asset_id !== expected.asset_id || value.version_id !== expected.version_id
        || !Array.isArray(value.images) || value.images.length !== expected.representations.length) return false;
    return value.images.every((image, index) => {
        if (!slotValid(image)
            || !exactKeys(image, [...slotKeys, "resource_key", "file_path", "requires_canvas_copy", "resource"])
            || !slotKeys.every(key => image[key] === expected.representations[index][key])) return false;
        const resource = image.resource;
        return image.resource_key === `resource:${image.media_id}`
            && image.file_path === `/api/v1/canvas-runtime/resources/${image.media_id}/file`
            && typeof image.requires_canvas_copy === "boolean" && record(resource)
            && exactKeys(resource, ["id", "userId", "kind", "status", "provider", "endpoint", "bucket", "objectKey", "publicUrl", "mimeType", "size", "width", "height", "durationMs", "etag", "createdAt", "updatedAt"])
            && resource.id === image.media_id && identifier(resource.userId)
            && resource.kind === "image" && resource.status === "ready" && resource.provider === "minio"
            && [resource.endpoint, resource.bucket, resource.objectKey, resource.publicUrl].every(field => field === "")
            && typeof resource.mimeType === "string" && /^image\/[a-zA-Z0-9!#$&^_.+\-]+$/.test(resource.mimeType)
            && nonnegativeInteger(resource.size) && (resource.size as number) > 0
            && [resource.width, resource.height, resource.durationMs].every(nonnegativeInteger)
            && typeof resource.etag === "string"
            && [resource.createdAt, resource.updatedAt].every(date => typeof date === "string" && Number.isFinite(Date.parse(date)));
    });
}

export async function readCanvasCharacterImages(transport: CanvasCharacterImageTransport, expected: CanvasCharacterImageExpectation, assertCurrent: () => void): Promise<CanvasCharacterImagesDto> {
    assertCurrent();
    if (typeof expected.canvas_key !== "string" || Array.from(expected.canvas_key).length > 64
        || !/^[\p{Alphabetic}\p{Mark}\p{Decimal_Number}\p{Connector_Punctuation}\u200C\u200D-]+$/u.test(expected.canvas_key)
        || !identifier(expected.canvas_row_version) || !identifier(expected.source_project_id)
        || !identifier(expected.asset_id) || !identifier(expected.version_id)
        || expected.version_policy !== undefined && !["current", "pinned"].includes(expected.version_policy)
        || !Array.isArray(expected.representations) || !expected.representations.every(slotValid)
        || new Set(expected.representations.map(slot => slot.reference_id)).size !== expected.representations.length) throw new Error("角色图片读取参数无效");
    const captured = structuredClone(expected);
    const response = await transport.get(
        `/canvas-runtime/canvas-projects/${encodeURIComponent(captured.canvas_key)}/character-catalog/${captured.asset_id}/image-resources`,
        { params: { expected_version_id: captured.version_id, ...(captured.version_policy === "pinned" ? { version_policy: captured.version_policy } : {}) } },
    );
    assertCurrent();
    if (JSON.stringify(expected) !== JSON.stringify(captured)) throw new Error("角色图片读取上下文已变化");
    if (!validResponse(response, captured)) throw new Error("角色图片解析响应与当前角色、版本或表示槽位不匹配");
    return response;
}
