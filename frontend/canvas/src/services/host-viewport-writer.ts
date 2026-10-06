export type HostViewport = { x: number; y: number; k: number };
export type ViewportWrite = { expected_viewport: HostViewport; viewport: HostViewport };

type Entry = {
    confirmed: HostViewport;
    desired: HostViewport;
    inFlight?: ViewportWrite;
    running?: Promise<void>;
    conflict?: unknown;
};

const same = (a: HostViewport, b: HostViewport) => a.x === b.x && a.y === b.y && a.k === b.k;

/** A viewport has its own CAS baseline; graph acknowledgements cannot replace it. */
export function createViewportWriter(write: (id: string, payload: ViewportWrite) => Promise<{ viewport: HostViewport }>) {
    const entries = new Map<string, Entry>();
    return {
        observe(id: string, viewport: HostViewport) {
            // Subsequent reads can arrive after a newer save. Only the initial read establishes
            // the baseline; successful writes advance it. Another window produces an explicit CAS conflict.
            if (!entries.has(id)) entries.set(id, { confirmed: { ...viewport }, desired: { ...viewport } });
        },
        async save(id: string, viewport: HostViewport): Promise<void> {
            const entry = entries.get(id);
            if (!entry) throw new Error("请先读取画布视口后再保存");
            entry.desired = { ...viewport };
            if (entry.conflict) throw entry.conflict;
            if (entry.running) return entry.running;
            const run = Promise.resolve().then(async () => {
                while (entry.inFlight || !same(entry.confirmed, entry.desired)) {
                    entry.inFlight ??= { expected_viewport: { ...entry.confirmed }, viewport: { ...entry.desired } };
                    const attempted = entry.inFlight;
                    try {
                        const result = await write(id, attempted);
                        if (!same(result.viewport, attempted.viewport)) throw new Error("视口保存回执不一致，请重新加载");
                        entry.confirmed = { ...attempted.viewport };
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
