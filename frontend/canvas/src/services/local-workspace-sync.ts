import { assertUserScope, captureUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import type { CanvasHistoryRestore } from "@/services/api/workspace-data";
import { rebindInconsistentCanvasAssets, type CanvasAssetRebindResult } from "@/services/canvas-asset-repair";
import { loadLatestLocalCanvasProject, openLocalCanvasProject, openLocalCanvasProjectFromBackend, persistCanvasDocument, restoreLocalCanvasProjectFromHistory } from "@/services/local-workspace-repository";
import { flushAssetStorePersistence, useAssetStore } from "@/stores/use-asset-store";
import {
    loadWorkspaceAssetLibraryPage,
    loadWorkspaceAssetsForUse,
    type WorkspaceAssetLibraryPageOptions,
} from "@/services/workspace-asset-read";
import { flushCanvasStorePersistence, useCanvasStore, type CanvasProject } from "@/stores/canvas/use-canvas-store";

const canvasRefreshListeners = new Set<(project: CanvasProject, previous: CanvasProject | undefined, resourceMap?: Record<string, string>) => void>();

export async function loadCanvasProjectForEditing(
    id: string,
    options: { latest?: boolean; historyRestore?: CanvasHistoryRestore; onLoad?: (project: CanvasProject) => void } = {},
) {
    const expected = captureUserScope();
    assertUserScope(expected);
    if (options.historyRestore) {
        const restored = await restoreLocalCanvasProjectFromHistory(id, options.historyRestore, expected);
        assertUserScope(expected);
        options.onLoad?.(restored);
        return restored;
    }
    const project = options.latest ? await loadLatestLocalCanvasProject(id, expected) : await openLocalCanvasProjectFromBackend(id, expected);
    assertUserScope(expected);
    if (project) options.onLoad?.(project);
    return project || undefined;
}

// 画布内容被其他写入者（远端刷新、任务回写）替换后通知本地编辑器合并。
export function subscribeCanvasRefresh(listener: (project: CanvasProject, previous: CanvasProject | undefined, resourceMap?: Record<string, string>) => void) {
    canvasRefreshListeners.add(listener);
    return () => { canvasRefreshListeners.delete(listener); };
}

/** 外部写入已投影到本地存储后，把这次替换交给正在编辑的页面。 */
export function notifyCanvasRefresh(project: CanvasProject, previous: CanvasProject | undefined, resourceMap?: Record<string, string>) {
    for (const listener of [...canvasRefreshListeners]) listener(project, previous, resourceMap);
}

/** 编辑器外的画布修改也需保存文档，避免之后刷新时恢复旧内容。 */
export async function syncLocalCanvasSnapshot(id: string, patch: Partial<Pick<CanvasProject, "nodes" | "connections" | "chatSessions" | "activeChatId" | "appearance" | "backgroundMode" | "showImageInfo" | "viewport">>, expectedScope?: CapturedUserScope) {
    const expected = expectedScope ?? captureUserScope();
    assertUserScope(expected);
    const current = openLocalCanvasProject(id);
    if (!current) throw new Error("本地画布不存在");
    const { viewport, ...documentPatch } = patch;
    if (viewport) useCanvasStore.getState().updateProject(id, { viewport });
    if (Object.keys(documentPatch).length > 0) await persistCanvasDocument(id, documentPatch, expected);
    assertUserScope(expected);
    const saved = openLocalCanvasProject(id);
    if (!saved) throw new Error("本地画布不存在");
    return saved;
}

export type LocalAssetPageOptions = WorkspaceAssetLibraryPageOptions;

export function loadAssetLibraryPage(options: LocalAssetPageOptions) {
    return loadWorkspaceAssetLibraryPage(options);
}

export function loadAssetsForUse(ids: Iterable<string>, expectedScope?: WorkspaceAssetLibraryPageOptions["expectedScope"]) {
    return loadWorkspaceAssetsForUse(ids, expectedScope);
}

export async function repairCanvasAssetBindings(): Promise<CanvasAssetRebindResult> {
    const result = rebindInconsistentCanvasAssets(useAssetStore.getState().assets);
    await Promise.all([flushCanvasStorePersistence(), flushAssetStorePersistence()]);
    return result;
}
