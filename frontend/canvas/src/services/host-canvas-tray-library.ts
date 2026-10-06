import { assertUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import { loadWorkspaceAssetLibraryPage, usesWorkspaceAssetLibraryApi } from "@/services/workspace-asset-read";
import { readAssetStoreDrafts, runAssetStoreProjection, useAssetStore } from "@/stores/use-asset-store";

/** 原托盘对完整图片库搜索；逐页恢复规范记录，不把缓存缺失当作空库。 */
export async function loadCanvasTrayLibrary(expected: CapturedUserScope, signal: AbortSignal) {
    if (!usesWorkspaceAssetLibraryApi()) return;
    assertUserScope(expected);
    const before = new Map(useAssetStore.getState().assets.map(asset => [asset.id, asset]));
    const found = new Set<string>();
    for (let page = 1; ; page += 1) {
        const result = await loadWorkspaceAssetLibraryPage({ page, pageSize: 100, kind: "image", status: "active", expectedScope: expected, signal });
        assertUserScope(expected);
        signal.throwIfAborted();
        if (result.page !== page) throw new Error("图片素材分页读取异常，请重试");
        for (const asset of result.assets) found.add(asset.id);
        if (!result.canonicalHasMore) break;
    }
    // 完整读取成功后才移除已消失的旧缓存；保留读取期间的本机编辑和新增上传。
    const drafts = readAssetStoreDrafts(expected);
    const pending = new Set(drafts.upserts.map(draft => draft.id));
    runAssetStoreProjection(() => {
        useAssetStore.setState(state => ({
            assets: state.assets.filter(asset => asset.kind !== "image" || asset.status === "archived"
                || found.has(asset.id) || pending.has(asset.id) || before.get(asset.id) !== asset),
        }));
    });
}
