import { assertUserScope, captureUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import { http } from "@/services/api/request";
import { createViewportWriter, type HostViewport } from "@/services/host-viewport-writer";

const writers = new Map<string, ReturnType<typeof createViewportWriter>>();

function writerFor(expected: CapturedUserScope) {
    const key = `${expected.userScope}\0${expected.epoch}`;
    let writer = writers.get(key);
    if (!writer) {
        writer = createViewportWriter(async (id, payload) => {
            assertUserScope(expected);
            return http.put<{ viewport: HostViewport }>(`/canvas-projects/${encodeURIComponent(id)}/viewport`, payload, { expectedScope: expected });
        });
        writers.set(key, writer);
    }
    return writer;
}

export function observeHostCanvasViewport(id: string, viewport: HostViewport, expected = captureUserScope()) {
    assertUserScope(expected);
    writerFor(expected).observe(id, viewport);
}

export async function saveHostCanvasViewport(id: string, viewport: HostViewport, expected = captureUserScope()) {
    assertUserScope(expected);
    await writerFor(expected).save(id, viewport);
}
