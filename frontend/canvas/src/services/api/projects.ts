import type { AssetCategory } from "@/lib/asset-category";

/** Media record used by the canvas timeline; storage remains private to the current user. */
export type ProjectAsset = {
    id: string;
    title: string;
    mediaType: string;
    category: AssetCategory;
    status: string;
    primaryVersionId?: string;
    versionCount: number;
    usages: string[];
    folderId?: string;
    position: number;
    storageKey?: string;
    durationMs?: number;
    previewText?: string;
    updatedAt: string;
    source?: string;
};
