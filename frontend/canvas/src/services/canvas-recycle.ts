import type { CanvasRecycleListDto, CanvasRecyclePurgeDto } from "@host/api/types/canvases";
import { assertUserScope, captureUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import { flushCanvasHistoryPersistence, useCanvasHistoryStore, type DeletedCanvasHistoryItem } from "@/stores/canvas/use-canvas-history-store";
import { http } from "@/services/api/request";
import { mergeRecycledProjects } from "@/services/canvas-recycle-state";

export async function refreshRecycledProjects(expected: CapturedUserScope = captureUserScope()) {
    const before = useCanvasHistoryStore.getState().deletedProjects;
    const result = await http.get<CanvasRecycleListDto>("/recycle-bin", { expectedScope: expected });
    assertUserScope(expected);
    // A restore, disposal or another refresh may have completed during this read.
    if (useCanvasHistoryStore.getState().deletedProjects !== before) return;
    const deletedProjects = mergeRecycledProjects(before, result.items);
    useCanvasHistoryStore.setState({ deletedProjects });
    await flushCanvasHistoryPersistence(expected.userScope);
    assertUserScope(expected);
}

export async function purgeRecycledProject(item: DeletedCanvasHistoryItem, expected: CapturedUserScope) {
    if (!item.project?.revision) throw new Error("回收站缺少删除版本，未删除作品");
    const archiveKey = item.archiveKey ?? `canvas-delete:${item.id}:${item.project.revision}`;
    const result = await http.post<CanvasRecyclePurgeDto>(`/canvas-projects/${encodeURIComponent(item.id)}/recycle-purge`,
        { revision: item.project.revision, archiveKey }, { expectedScope: expected });
    assertUserScope(expected);
    if (result.source_key !== item.id || result.archive_key !== archiveKey || result.state !== "purged") {
        throw new Error("删除回执与所选作品不一致，请刷新回收站");
    }
    await removeRecycledProject(item, expected);
    assertUserScope(expected);
}

/** Keep the exact receipt and snapshot available if the local removal cannot persist. */
export async function removeRecycledProject(item: DeletedCanvasHistoryItem, expected: CapturedUserScope) {
    assertUserScope(expected);
    const before = useCanvasHistoryStore.getState().deletedProjects;
    const current = before.find(entry => entry.id === item.id);
    if (!current) return;
    if (current.project?.revision !== item.project?.revision || current.archiveKey !== item.archiveKey) {
        throw new Error("回收记录已更新，请重新选择作品");
    }
    const deletedProjects = before.filter(entry => entry !== current);
    useCanvasHistoryStore.setState({ deletedProjects });
    try {
        await flushCanvasHistoryPersistence(expected.userScope);
    } catch (error) {
        assertUserScope(expected);
        if (useCanvasHistoryStore.getState().deletedProjects === deletedProjects) {
            useCanvasHistoryStore.setState({ deletedProjects: before });
            await flushCanvasHistoryPersistence(expected.userScope);
        }
        throw error;
    }
    assertUserScope(expected);
}
