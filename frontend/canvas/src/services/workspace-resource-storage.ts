import { resourceIdFromStorageKey } from "@/services/api/resources";

/**
 * Python/MySQL/MinIO own durable resources. IndexedDB remains the source editor's
 * recoverable draft/cache, independent of its local-workspace feature branch.
 */
export function usesBrowserLocalResourceStore() {
    return false;
}

export function usesNativeLocalResourceStore() {
    return false;
}

const WORKSPACE_MEDIA_KINDS = new Set(["image", "video", "audio", "model"]);

export function isWorkspaceMediaAssetKind(kind: string) {
    return WORKSPACE_MEDIA_KINDS.has(kind);
}

/** Desktop/hosted media confirm only resource-backed rows; browser-local IndexedDB is the product store. */
export function isCanonicalWorkspaceMediaPersistSource(input: { kind?: string; storageKey?: string; pendingRemoteUpload?: boolean }) {
    if (input.kind && !isWorkspaceMediaAssetKind(input.kind)) return true;
    if (input.pendingRemoteUpload) return false;
    if (usesBrowserLocalResourceStore()) return Boolean(input.storageKey?.trim());
    return Boolean(resourceIdFromStorageKey(input.storageKey));
}

export function workspaceAssetMediaStorageKey(asset: { kind: string; data: object }) {
    const storageKey = "storageKey" in asset.data ? asset.data.storageKey : undefined;
    return typeof storageKey === "string" ? storageKey : undefined;
}

export function workspaceAssetHasCanonicalMediaPersist(asset: { kind: string; data: object; pendingRemoteUpload?: boolean }) {
    return isCanonicalWorkspaceMediaPersistSource({
        kind: asset.kind,
        storageKey: workspaceAssetMediaStorageKey(asset),
        pendingRemoteUpload: asset.pendingRemoteUpload,
    });
}
