import type { CanvasProject } from "@/stores/canvas/use-canvas-store";
import type { CanvasInitialDrawingDto } from "@host/api/types/canvases";
import { assertUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import { getCanvasDrawing } from "@/services/api/workspace-data";
import { resourceFileUrl } from "@/services/api/resources";
import { ApiError } from "@/services/api/request";

/** Capture each saved scene and its own derived media in a single server read. */
export async function prepareCanvasDrawingCopies(project: CanvasProject, expected: CapturedUserScope) {
    const nodes = structuredClone(project.nodes);
    const drawings = new Map<string, CanvasInitialDrawingDto>();
    for (const node of nodes) {
        if (node.type !== "drawing" || !node.metadata?.drawingId) continue;
        const id = node.metadata.drawingId;
        if (!drawings.has(id)) {
            assertUserScope(expected);
            try {
                const { drawing } = await getCanvasDrawing(project.id, id, { expectedScope: expected });
                assertUserScope(expected);
                if (drawing.snapshot === undefined) throw new Error("来源绘图缺少笔画，副本尚未创建");
                drawings.set(id, {
                    drawingId: id, engine: drawing.engine, revision: "0", snapshot: drawing.snapshot,
                    shapeCount: drawing.shapeCount, pageCount: 1,
                    ...(drawing.previewResourceId ? { previewResourceId: drawing.previewResourceId } : {}),
                    ...(drawing.render ? { render: drawing.render } : {}),
                });
            } catch (error) {
                assertUserScope(expected);
                if (!(error instanceof ApiError && error.status === 404 && (!node.metadata.drawingRevision || node.metadata.drawingRevision === "0") && !node.metadata.drawingShapeCount)) throw error;
                continue;
            }
        }
        const saved = drawings.get(id)!;
        node.metadata = {
            ...node.metadata, drawingEngine: saved.engine, drawingRevision: "1",
            drawingShapeCount: saved.shapeCount, drawingPageCount: 1,
            drawingPreviewUrl: saved.previewResourceId ? resourceFileUrl(saved.previewResourceId) : undefined,
        };
    }
    assertUserScope(expected);
    return { nodes, drawingDocuments: [...drawings.values()] };
}
