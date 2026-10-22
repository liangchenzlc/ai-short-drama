import { useMemo } from "react";
import { AssetLibraryPickerModal, type AssetLibraryPickerItem } from "@/components/assets/asset-library-picker-modal";
import { useExternalAssetSources } from "@/hooks/use-external-asset-sources";
import { assetPickerItemsToInsertPayloads, type InsertAssetPayload } from "./asset-picker-modal";
import { ASSET_CATEGORY_LABELS, normalizeAssetCategory } from "@/lib/asset-category";
import { assertUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import { useAssetStore } from "@/stores/use-asset-store";

export function CanvasProjectAssetModal({ open, initialCategory = "all", initialFolderId = "all", onClose, onInsert, onInsertFolder }: {
    open: boolean;
    initialCategory?: string;
    initialFolderId?: string;
    onClose: () => void;
    onInsert: (payloads: InsertAssetPayload[], expectedScope: CapturedUserScope) => Promise<void> | void;
    onInsertFolder?: (folderId: string, expectedScope: CapturedUserScope) => Promise<void> | void;
}) {
    const assets = useAssetStore(state => state.assets);
    const external = useExternalAssetSources(open);
    const items = useMemo<AssetLibraryPickerItem[]>(() => [
        ...assets.filter(asset => ["text", "image", "video", "audio"].includes(asset.kind) && asset.status !== "archived").map(asset => ({
            id: asset.id, title: asset.title, category: normalizeAssetCategory(asset.category), folderId: asset.folderId,
            mediaKind: asset.kind as "text" | "image" | "video" | "audio", asset,
            kindLabel: asset.kind === "image" ? "图片" : asset.kind === "video" ? "视频" : asset.kind === "audio" ? "音频" : "文本",
            searchText: (asset.tags || []).join(" "),
        })), ...external.items,
    ], [assets, external.items]);
    return <AssetLibraryPickerModal
        remoteLibrary open={open} mediaKinds={["image", "video", "audio", "text"]} items={items}
        categoryLabels={{ all: "全部素材", ...ASSET_CATEGORY_LABELS, ...external.categoryLabels }}
        initialCategory={initialCategory} initialFolderId={initialFolderId} folders={external.folders}
        folderActionSource="local" title="画布素材" confirmLabel={count => `插入已选素材${count ? `（${count}）` : ""}`}
        emptyTitle="此分类暂无素材" emptyDescription="可先上传图片、视频、音频或文本。" footerNote={external.error || undefined}
        onFolderAction={onInsertFolder ? async (folderId, scope) => { await onInsertFolder(folderId, scope); assertUserScope(scope); onClose(); } : undefined}
        onClose={onClose} onConfirm={async (ids, scope) => { await onInsert(assetPickerItemsToInsertPayloads(ids, items), scope); assertUserScope(scope); onClose(); }}
    />;
}
