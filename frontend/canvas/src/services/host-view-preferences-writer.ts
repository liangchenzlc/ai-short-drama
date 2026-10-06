import type { CanvasViewPreferencesDto, CanvasViewPreferencesRequestDto } from "@host/api/types/canvases";

export type ViewPreferences = CanvasViewPreferencesDto;
type Write = CanvasViewPreferencesRequestDto;
type Entry = {
    confirmed: ViewPreferences;
    desired: ViewPreferences;
    inFlight?: Write;
    running?: Promise<void>;
    conflict?: unknown;
};

/** Canonicalize optional custom colors without coupling persistence to React or the store. */
export function viewPreferences(value: Partial<ViewPreferences>): ViewPreferences {
    const custom = value.appearance?.custom;
    return {
        appearance: value.appearance ? { mode: value.appearance.mode, ...(custom ? { custom: {
            baseTheme: custom.baseTheme, backgroundColor: custom.backgroundColor,
            backgroundBrightness: custom.backgroundBrightness, gridColor: custom.gridColor,
            gridOpacity: custom.gridOpacity,
        } } : {}) } : null,
        backgroundMode: value.backgroundMode ?? "dots",
        showImageInfo: value.showImageInfo ?? false,
    };
}

const same = (a: ViewPreferences, b: ViewPreferences) => JSON.stringify(a) === JSON.stringify(b);

export function createViewPreferencesWriter(write: (id: string, payload: Write) => Promise<{ preferences: ViewPreferences }>) {
    const entries = new Map<string, Entry>();
    return {
        observe(id: string, value: Partial<ViewPreferences>) {
            if (!entries.has(id)) entries.set(id, { confirmed: viewPreferences(value), desired: viewPreferences(value) });
        },
        async save(id: string, value: Partial<ViewPreferences>): Promise<void> {
            const entry = entries.get(id);
            if (!entry) throw new Error("请先读取画布外观后再保存");
            entry.desired = viewPreferences(value);
            if (entry.conflict) throw entry.conflict;
            if (entry.running) return entry.running;
            const run = Promise.resolve().then(async () => {
                while (entry.inFlight || !same(entry.confirmed, entry.desired)) {
                    entry.inFlight ??= { expected_preferences: structuredClone(entry.confirmed), preferences: structuredClone(entry.desired) };
                    const attempted = entry.inFlight;
                    try {
                        const result = await write(id, attempted);
                        if (!same(viewPreferences(result.preferences), attempted.preferences)) throw new Error("外观保存回执不一致，请重新加载");
                        entry.confirmed = structuredClone(attempted.preferences);
                        entry.inFlight = undefined;
                    } catch (error) {
                        if ((error as { status?: number }).status === 409) entry.conflict = error;
                        throw error;
                    }
                }
            }).finally(() => { if (entry.running === run) entry.running = undefined; });
            entry.running = run;
            return run;
        },
    };
}
