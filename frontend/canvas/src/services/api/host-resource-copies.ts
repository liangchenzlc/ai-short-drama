import { assertUserScope, captureUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import { http } from "@/services/api/request";
import type { RemoteResource } from "@/services/api/resources";

/** Host contract: callers retain both the captured canvas and the same key on uncertain results. */
export type CanvasResourceCopyRequest = {
    source_resource_id: string;
    canvas_key: string;
};

export async function copyResourceToCanvas(
    input: CanvasResourceCopyRequest,
    options: { idempotencyKey: string; expectedScope?: CapturedUserScope; signal?: AbortSignal },
): Promise<RemoteResource> {
    if (!options.idempotencyKey.trim()) throw new Error("资源复制缺少稳定请求标识");
    const expectedScope = options.expectedScope ?? captureUserScope();
    assertUserScope(expectedScope);
    const result = await http.post<{ resource: RemoteResource }>("/resources/copies", input, {
        headers: { "X-Idempotency-Key": options.idempotencyKey },
        expectedScope,
        signal: options.signal,
        timeout: 0,
    });
    assertUserScope(expectedScope);
    return result.resource;
}
