import { isLocalWorkspaceMode } from "@/services/workspace-mode";
import { createLocalCanvasProject, deleteLocalCanvasProjects } from "@/services/local-workspace-repository";
import { useCanvasStore, type CanvasProject } from "@/stores/canvas/use-canvas-store";

import type { CanvasSourceOrigin } from "@/services/canvas-initial-write";

type InitialCanvasContent = Partial<Pick<CanvasProject, "nodes" | "connections" | "chatSessions" | "activeChatId">>;

/**
 * Single project-storage boundary for workspace UI code.
 * Local and hosted modes both use the workspace repository.
 */
export async function createWorkspaceCanvasProject(title: string, initialContent?: InitialCanvasContent, workspaceProjectId?: string, sourceCanvas?: CanvasSourceOrigin) {
    if (isLocalWorkspaceMode()) {
        return createLocalCanvasProject(title, initialContent, workspaceProjectId, sourceCanvas);
    }
    const created = await createLocalCanvasProject(title, initialContent);
    if (workspaceProjectId) useCanvasStore.getState().updateProject(created.id, { workspaceProjectId });
    return created;
}

export async function deleteWorkspaceCanvasProjects(ids: readonly string[]) {
    return deleteLocalCanvasProjects(ids);
}
