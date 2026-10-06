import { create } from "zustand";
import { persist, type PersistStorage } from "zustand/middleware";
import { localForageStorageForScope } from "@/lib/localforage-storage";
import type { CanvasProject } from "@/stores/canvas/use-canvas-store";
import { getActiveUserScope } from "@/lib/user-scope";

export type DeletedCanvasHistoryItem = {
    id: string;
    title: string;
    createdAt: string;
    updatedAt: string;
    deletedAt: string;
    nodeCount: number;
    coverUrl?: string;
    archiveKey?: string;
    /** 完整快照仅用于本地软删除恢复；旧历史记录可没有该字段。 */
    project?: CanvasProject;
};

type CanvasHistoryStore = {
    deletedProjects: DeletedCanvasHistoryItem[];
    recordDeletedProjects: (projects: CanvasProject[], deletedAt?: string) => void;
    removeDeletedHistoryItem: (id: string) => void;
    clearDeletedHistory: () => void;
};

export const CANVAS_HISTORY_STORE_KEY = "infinite-canvas:deleted_history_store";
const historyWrites = new Map<string, Promise<void>>();

function queueHistoryWrite(scope: string, action: () => Promise<void>) {
    const run = (historyWrites.get(scope) ?? Promise.resolve()).catch(() => undefined).then(action);
    historyWrites.set(scope, run);
    // Retain a failed tail for explicit flush; a later full snapshot may supersede it.
    void run.then(() => { if (historyWrites.get(scope) === run) historyWrites.delete(scope); }, () => undefined);
    return run;
}

export async function flushCanvasHistoryPersistence(scope = getActiveUserScope()) {
    while (historyWrites.has(scope)) await historyWrites.get(scope);
}

const historyStorage: PersistStorage<CanvasHistoryStore> = {
    getItem: async (name) => {
        const value = await localForageStorageForScope(getActiveUserScope()).getItem(name);
        return value ? JSON.parse(value) : null;
    },
    setItem: (name, value) => {
        const scope = getActiveUserScope();
        const serialized = JSON.stringify(value);
        return queueHistoryWrite(scope, async () => { await localForageStorageForScope(scope).setItem(name, serialized); });
    },
    removeItem: (name) => {
        const scope = getActiveUserScope();
        return queueHistoryWrite(scope, async () => { await localForageStorageForScope(scope).removeItem(name); });
    },
};

export const useCanvasHistoryStore = create<CanvasHistoryStore>()(
    persist(
        (set) => ({
            deletedProjects: [],
            recordDeletedProjects: (projects, deletedAt) => {
                const now = deletedAt ?? new Date().toISOString();
                const newItems: DeletedCanvasHistoryItem[] = projects.map((p) => ({
                    id: p.id,
                    title: p.title || "未命名画布",
                    createdAt: p.createdAt || p.updatedAt || now,
                    updatedAt: p.updatedAt || now,
                    deletedAt: now,
                    nodeCount: p.nodes?.length || 0,
                    project: p,
                }));
                set((state) => {
                    const existingIds = new Set(newItems.map((item) => item.id));
                    const filtered = state.deletedProjects.filter((item) => !existingIds.has(item.id));
                    return {
                        deletedProjects: [...newItems, ...filtered].slice(0, 200),
                    };
                });
            },
            removeDeletedHistoryItem: (id) =>
                set((state) => ({
                    deletedProjects: state.deletedProjects.filter((item) => item.id !== id),
                })),
            clearDeletedHistory: () => set({ deletedProjects: [] }),
        }),
        {
            name: CANVAS_HISTORY_STORE_KEY,
            storage: historyStorage,
        },
    ),
);
