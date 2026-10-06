import type { CanvasRecycleItemDto } from "@host/api/types/canvases";
import type { DeletedCanvasHistoryItem } from "@/stores/canvas/use-canvas-history-store";
import type { CanvasProject } from "@/stores/canvas/use-canvas-store";

export function mergeRecycledProjects(local: DeletedCanvasHistoryItem[], items: CanvasRecycleItemDto[]): DeletedCanvasHistoryItem[] {
    const previous = new Map(local.map(item => [item.id, item]));
    return items.map(item => {
        const project = item.source_document as unknown as CanvasProject;
        if (project.id !== item.source_key || project.workspaceProjectId !== item.project_id
            || typeof project.revision !== "string" || !/^[1-9][0-9]*$/.test(project.revision)
            || !Array.isArray(project.nodes) || !item.archive_key || !Number.isFinite(Date.parse(item.deleted_at))) {
            throw new Error("回收站记录不完整，已有快照已保留");
        }
        const prior = previous.get(project.id);
        const same = prior?.project?.revision === project.revision;
        return {
            id: project.id, title: project.title, createdAt: project.createdAt,
            updatedAt: project.updatedAt, deletedAt: same ? prior.deletedAt : item.deleted_at,
            nodeCount: project.nodes.length, project: same ? prior.project : project,
            archiveKey: item.archive_key,
        };
    });
}

/** Presentation only: keep scoped resource URLs out of the restorable document. */
export function recycledProjectPreview(item: DeletedCanvasHistoryItem, resourceUrl: (id: string) => string): CanvasProject | undefined {
    if (!item.project) return undefined;
    const archiveKey = item.archiveKey ?? `canvas-delete:${item.id}:${item.project.revision}`;
    const preview = structuredClone(item.project);
    const url = (value: string | undefined, storageKey?: string) => {
        const id = /^resource:([0-9]+)$/.exec(storageKey ?? "")?.[1]
            ?? /\/resources\/([0-9]+)\/file(?:[?#]|$)/.exec(value ?? "")?.[1];
        if (!id) return value;
        return `${resourceUrl(id)}?recycle_source_key=${encodeURIComponent(item.id)}&recycle_archive_key=${encodeURIComponent(archiveKey)}`;
    };
    for (const node of preview.nodes) {
        if (!["image", "video"].includes(node.type) || !node.metadata) continue;
        const metadata = node.metadata;
        metadata.content = url(metadata.content, metadata.storageKey);
        metadata.previewContent = url(metadata.previewContent);
        if (/^resource:[0-9]+$/.test(metadata.storageKey ?? "")) metadata.storageKey = undefined;
        if (metadata.videoPreview) {
            metadata.videoPreview.content = url(metadata.videoPreview.content, metadata.videoPreview.storageKey) ?? "";
            metadata.videoPreview.storageKey = undefined;
        }
    }
    return preview;
}
