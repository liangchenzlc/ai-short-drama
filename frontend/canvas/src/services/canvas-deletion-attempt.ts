import type { CanvasProject } from "@/stores/canvas/use-canvas-store";
import type { CanvasRecycleStatusDto } from "@host/api/types/canvases";

export const CANVAS_DELETION_PREFIX = "canvas-deletion-attempt:";
type Storage = {
    getItem(key: string): string | null | Promise<string | null>;
    setItem(key: string, value: string): unknown;
    removeItem(key: string): unknown;
};

export type CanvasDeletionAttempt = {
    userScope: string;
    project: CanvasProject;
    deletedAt: string;
    archiveKey: string;
};

export async function readCanvasDeletion(storage: Storage, scope: string, id: string): Promise<CanvasDeletionAttempt | null> {
    const raw = await storage.getItem(CANVAS_DELETION_PREFIX + id);
    if (raw === null) return null;
    let saved: CanvasDeletionAttempt;
    try { saved = JSON.parse(raw); }
    catch { throw new Error("删除记录损坏，原有草稿已保留"); }
    if (!saved || saved.userScope !== scope || saved.project?.id !== id
        || typeof saved.project.revision !== "string" || !/^[1-9][0-9]*$/.test(saved.project.revision)
        || !Array.isArray(saved.project.nodes) || !saved.project.workspaceProjectId
        || saved.archiveKey !== `canvas-delete:${id}:${saved.project.revision}`
        || !Number.isFinite(Date.parse(saved.deletedAt))) {
        throw new Error("删除记录无法恢复，原有草稿已保留");
    }
    return saved;
}

/** Freeze the original request before dispatch; a later draft cannot change its identity. */
export async function prepareCanvasDeletion(storage: Storage, scope: string, project: CanvasProject): Promise<CanvasDeletionAttempt> {
    const previous = await readCanvasDeletion(storage, scope, project.id);
    if (previous) return previous;
    if (typeof project.revision !== "string" || !/^[1-9][0-9]*$/.test(project.revision) || !project.workspaceProjectId) {
        throw new Error("请先完成画布保存，再移入回收站");
    }
    const attempt = { userScope: scope, project: structuredClone(project), deletedAt: new Date().toISOString(),
        archiveKey: `canvas-delete:${project.id}:${project.revision}` };
    await storage.setItem(CANVAS_DELETION_PREFIX + project.id, JSON.stringify(attempt));
    return attempt;
}

export function validateCanvasDeletionStatus(attempt: CanvasDeletionAttempt, status: CanvasRecycleStatusDto) {
    if (status.source_key !== attempt.project.id || status.project_id !== attempt.project.workspaceProjectId
        || status.archive_key !== attempt.archiveKey || status.expected_row_version !== attempt.project.revision
        || status.committed_row_version !== String(BigInt(attempt.project.revision!) + BigInt(1))
        || !["archived", "superseded", "purged"].includes(status.state)) {
        throw new Error("删除回执与原请求不一致，原有草稿已保留");
    }
    return status.state;
}

export function clearCanvasDeletion(storage: Storage, id: string) {
    return storage.removeItem(CANVAS_DELETION_PREFIX + id);
}
