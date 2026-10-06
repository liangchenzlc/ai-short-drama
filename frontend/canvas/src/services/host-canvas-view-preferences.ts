import { assertUserScope, captureUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import { http } from "@/services/api/request";
import { createViewPreferencesWriter, type ViewPreferences } from "@/services/host-view-preferences-writer";

const writers = new Map<string, ReturnType<typeof createViewPreferencesWriter>>();

function writerFor(expected: CapturedUserScope) {
    const key = `${expected.userScope}\0${expected.epoch}`;
    let writer = writers.get(key);
    if (!writer) {
        writer = createViewPreferencesWriter(async (id, payload) => {
            assertUserScope(expected);
            return http.put<{ preferences: ViewPreferences }>(`/canvas-projects/${encodeURIComponent(id)}/view-preferences`, payload, { expectedScope: expected });
        });
        writers.set(key, writer);
    }
    return writer;
}

export function observeHostCanvasViewPreferences(id: string, preferences: Partial<ViewPreferences>, expected = captureUserScope()) {
    assertUserScope(expected);
    writerFor(expected).observe(id, preferences);
}

export async function saveHostCanvasViewPreferences(id: string, preferences: Partial<ViewPreferences>, expected = captureUserScope()) {
    assertUserScope(expected);
    await writerFor(expected).save(id, preferences);
}
