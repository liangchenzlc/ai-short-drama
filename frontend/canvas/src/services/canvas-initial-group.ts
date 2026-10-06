import type { CanvasInitialWrite } from "./canvas-initial-write.ts";

export const INITIAL_CANVAS_GROUP_PREFIX = "canvas-initial-group:";

type GroupStorage = {
    getItem(key: string): string | null | Promise<string | null>;
    setItem(key: string, value: string): unknown;
    removeItem(key: string): unknown;
};

export type InitialCanvasGroup = { version: 1; rootId: string; projects: CanvasInitialWrite[] };

function validateGroup(value: unknown, rootId: string): InitialCanvasGroup {
    const group = value as InitialCanvasGroup | null;
    if (!group || group.version !== 1 || group.rootId !== rootId || !Array.isArray(group.projects)
        || !group.projects.length || group.projects[0]?.id !== rootId
        || new Set(group.projects.map(project => project?.id)).size !== group.projects.length
        || group.projects.some(project => !project || typeof project.id !== "string" || !project.id
            || project.revision !== "0" || project.workspaceProjectId !== rootId || !Array.isArray(project.nodes))) {
        throw new Error("项目复制记录无法恢复，原有草稿已保留");
    }
    return group;
}

/** One atomic storage item owns all immutable copies before any individual request is sent. */
export async function prepareInitialCanvasGroup(storage: GroupStorage, projects: readonly CanvasInitialWrite[]) {
    const rootId = projects[0]?.id;
    if (!rootId) throw new Error("复制项目没有画布");
    const previous = await readInitialCanvasGroup(storage, rootId);
    if (previous) return previous;
    const group = validateGroup(structuredClone({ version: 1, rootId, projects }), rootId);
    await storage.setItem(`${INITIAL_CANVAS_GROUP_PREFIX}${rootId}`, JSON.stringify(group));
    return group;
}

export async function readInitialCanvasGroup(storage: GroupStorage, rootId: string) {
    const raw = await storage.getItem(`${INITIAL_CANVAS_GROUP_PREFIX}${rootId}`);
    if (raw === null) return null;
    let group: unknown;
    try {
        group = JSON.parse(raw);
    } catch {
        throw new Error("项目复制记录无法恢复，原有草稿已保留");
    }
    return validateGroup(group, rootId);
}

/** Only called after every child request and the local graph are durably materialized. */
export function clearInitialCanvasGroup(storage: GroupStorage, rootId: string) {
    return storage.removeItem(`${INITIAL_CANVAS_GROUP_PREFIX}${rootId}`);
}
