import { assertUserScope, isUserScopeAbandonedError, type CapturedUserScope } from "@/lib/user-scope-guard";
import type { MediaConversionOperation } from "@/lib/media-conversion/contracts";
import { resourceFileUrl, resourceStorageKey, uploadResourceFile } from "@/services/api/resources";

export class MediaConversionArchiveError extends Error {
    constructor(message: string, cause?: unknown) {
        super(message, { cause });
        this.name = "MediaConversionArchiveError";
    }
}

export async function persistMediaConversionResult(
    result: { blob: Blob; width: number; height: number },
    options: {
        nodeId: string;
        sourceFingerprint: string;
        operation: MediaConversionOperation;
        hostCanvasKey: string | null;
        expectedScope: CapturedUserScope;
        assertActive: () => void;
    },
) {
    const assertActive = () => {
        assertUserScope(options.expectedScope);
        options.assertActive();
    };
    assertActive();
    if (!result.blob.size || result.blob.type !== "image/png"
        || !Number.isSafeInteger(result.width) || result.width <= 0
        || !Number.isSafeInteger(result.height) || result.height <= 0) {
        throw new MediaConversionArchiveError("转换结果保存失败：图片字节或尺寸无效");
    }
    const hex = async (bytes: BufferSource) => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)))
        .map(value => value.toString(16).padStart(2, "0")).join("");
    const checksum = await hex(await result.blob.arrayBuffer());
    assertActive();
    // 同一来源、操作及输出复用身份；响应不确定时不会因重新保存而创建第二份资源。
    const identity = await hex(new TextEncoder().encode(JSON.stringify([
        options.expectedScope.userScope, options.hostCanvasKey, options.nodeId,
        options.sourceFingerprint, options.operation, checksum, result.width, result.height,
    ])));
    assertActive();
    try {
        const resource = await uploadResourceFile(result.blob, "image", {
            width: result.width, height: result.height,
            fileName: `conversion-${options.operation}.png`,
            idempotencyKey: `media-conversion:${identity}`,
            hostCanvasKey: options.hostCanvasKey,
            expectedScope: options.expectedScope,
        });
        assertActive();
        const maximumId = "18446744073709551615";
        if (!/^[1-9][0-9]{0,19}$/.test(resource.id)
            || resource.id.length === maximumId.length && resource.id > maximumId
            || resource.status !== "ready" || resource.kind !== "image" || resource.mimeType !== "image/png"
            || resource.size !== result.blob.size || resource.width !== result.width || resource.height !== result.height) {
            throw new MediaConversionArchiveError("转换结果保存回执不完整或与图片不匹配");
        }
        return { storageKey: resourceStorageKey(resource.id), url: resourceFileUrl(resource.id) };
    } catch (error) {
        if (isUserScopeAbandonedError(error) || error instanceof MediaConversionArchiveError
            || error instanceof Error && error.name === "AbortError") throw error;
        throw new MediaConversionArchiveError(`转换结果保存失败：${error instanceof Error ? error.message : "未知错误"}`, error);
    }
}
