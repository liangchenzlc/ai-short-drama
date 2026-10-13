import type { CanvasResourceNormalizeDto } from "@host/api/types/canvases";
import { http as hostHttp } from "@host/api/http";
import { assertUserScope, captureUserScope } from "@/lib/user-scope-guard";
import { prepareCanvasDocumentResources } from "./host-resource-normalization";
import { persistHostCanvasResourceAliases } from "./host-canvas-resource-preparation";
import { hostCanvasAccess } from "./host-canvas-access";
import { createCanvasCharacterTransport, readCanvasCharacterReferenceCards } from "./host-character-catalog";

export async function readCanvasGenerationCharacters(canvasKey: string, sourceProjectId: string, references: Array<{ assetId: string; requestedVersionId?: string }>) {
    const scope = captureUserScope();
    const assertCurrent = () => {
        assertUserScope(scope);
        hostCanvasAccess.assert(scope, canvasKey);
    };
    const transport = createCanvasCharacterTransport(hostHttp, scope.userScope, assertCurrent);
    const cards = await readCanvasCharacterReferenceCards(transport, canvasKey, sourceProjectId, references, assertCurrent);
    assertCurrent();
    return cards.map(card => ({ asset: { id: card.id, title: card.title }, character: card.character }));
}


export async function normalizeCanvasCharacterGenerationResources(canvasKey: string, resourceIds: string[]) {
    const scope = captureUserScope();
    const assertCurrent = () => {
        assertUserScope(scope);
        hostCanvasAccess.assert(scope, canvasKey);
    };
    const prepared = await prepareCanvasDocumentResources({ resourceIds }, {
        assertActive: assertCurrent,
        normalize: async resource_ids => {
            const response = await hostHttp.post<CanvasResourceNormalizeDto>("/canvas-runtime/resources/normalize", {
                canvas_key: canvasKey, resource_ids,
            }, { headers: { "X-Canvas-Actor": scope.userScope }, timeout: 0 });
            assertCurrent();
            return response.data;
        },
    });
    assertCurrent();
    await persistHostCanvasResourceAliases(canvasKey, prepared.resourceAliases, scope, assertCurrent);
    assertCurrent();
    return prepared.resourceMap;
}
