import type { CanvasResourceNormalizeDto } from "@host/api/types/canvases";
import { assertUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import { http } from "@/services/api/request";
import { hostCanvasAccess } from "@/services/host-canvas-access";
import { observeCanvasResourceAliases } from "@/services/host-resource-identities";
import { prepareCanvasDocumentResources } from "@/services/host-resource-normalization";
import { updateCanvasOperationJournal } from "@/services/canvas-operation-journal";
import type { CanvasProject } from "@/stores/canvas/use-canvas-store";

export async function prepareHostCanvasResources(project: CanvasProject, expectedScope: CapturedUserScope, assertWritable: () => void) {
    const assertActive = () => {
        assertUserScope(expectedScope);
        hostCanvasAccess.assert(expectedScope, project.id);
        assertWritable();
    };
    const prepared = await prepareCanvasDocumentResources(project, {
        assertActive,
        normalize: resource_ids => http.post<CanvasResourceNormalizeDto>("/resources/normalize", {
            canvas_key: project.id, resource_ids,
        }, { expectedScope, timeout: 0 }),
    });
    assertActive();
    await persistHostCanvasResourceAliases(project.id, prepared.resourceAliases, expectedScope, assertActive);
    return prepared;
}

export async function persistHostCanvasResourceAliases(id: string, resourceAliases: Record<string, string[]>, expectedScope: CapturedUserScope, assertActive: () => void) {
    assertActive();
    if (Object.keys(resourceAliases).length) {
        // Persist private provenance before projecting copied IDs into the draft.
        // A reload after a failed graph commit must still match the original asset.
        await updateCanvasOperationJournal(id, expectedScope.userScope, current => ({
            ...current, resourceAliases: { ...current.resourceAliases, ...resourceAliases },
        }));
        assertActive();
    }
    observeCanvasResourceAliases(expectedScope, resourceAliases);
}
