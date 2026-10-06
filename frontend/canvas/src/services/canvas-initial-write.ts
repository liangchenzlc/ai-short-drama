import type { CanvasProject } from "@/stores/canvas/use-canvas-store";
import type { CanvasInitialDrawingDto } from "@host/api/types/canvases";
import { canonicalizeCanvasMedia } from "./canvas-media-locators.ts";
import { collectCanvasResourceIds } from "./host-resource-normalization.ts";

export type CanvasInitialWrite = CanvasProject & { initialDrawingDocuments?: CanvasInitialDrawingDto[]; initialArchiveBindings?: boolean };

export function initialCanvasDocument(write: CanvasInitialWrite): CanvasProject {
    if (!write.initialDrawingDocuments && !write.initialArchiveBindings) return write;
    const { initialDrawingDocuments: _, initialArchiveBindings: _bindings, ...document } = write;
    return document;
}

export function initialCanvasResourceIds(write: CanvasInitialWrite) {
    const ids = new Set(collectCanvasResourceIds(initialCanvasDocument(write)));
    for (const drawing of write.initialDrawingDocuments || []) {
        const snapshot = drawing.snapshot as { files?: Record<string, { dataURL?: string }> } | null;
        const files = snapshot?.files && typeof snapshot.files === "object" ? Object.values(snapshot.files) : [];
        const references = { drawing, resourceId: drawing.previewResourceId, files: files.map(file => ({ url: file?.dataURL })) };
        collectCanvasResourceIds(references).forEach(id => ids.add(id));
    }
    return [...ids].sort();
}

type AttemptStorage = {
    getItem(key: string): string | null | Promise<string | null>;
    setItem(key: string, value: string): unknown;
    removeItem(key: string): unknown;
};

/** Only a durable original request authorizes creation recovery, never a failed GET. */
export async function readInitialCanvasWrite(storage: AttemptStorage, id: string): Promise<CanvasInitialWrite | null> {
    const previous = await storage.getItem(`canvas-initial-write:${id}`);
    if (previous === null) return null;
    let saved: CanvasInitialWrite;
    try {
        saved = JSON.parse(previous) as CanvasInitialWrite;
    } catch {
        throw new Error("首次保存记录无法恢复，原有草稿已保留");
    }
    if (!saved || saved.id !== id || saved.revision !== "0" || !Array.isArray(saved.nodes)) {
        throw new Error("首次保存记录无法恢复，原有草稿已保留");
    }
    return saved;
}

/** Persist the exact first request before dispatch; subsequent editing cannot replace it. */
export async function prepareInitialCanvasWrite(storage: AttemptStorage, project: CanvasInitialWrite): Promise<CanvasInitialWrite> {
    const previous = await readInitialCanvasWrite(storage, project.id);
    if (previous) return previous;
    const snapshot = canonicalizeCanvasMedia(structuredClone({ ...project, revision: "0" }));
    await storage.setItem(`canvas-initial-write:${project.id}`, JSON.stringify(snapshot));
    return snapshot;
}

export function clearInitialCanvasWrite(storage: AttemptStorage, id: string) {
    return storage.removeItem(`canvas-initial-write:${id}`);
}
