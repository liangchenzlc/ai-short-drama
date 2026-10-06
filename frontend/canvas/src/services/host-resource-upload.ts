const uploadIdentities = new WeakMap<Blob, string>();

/** Capture once before awaiting: navigation cannot re-home an in-flight upload. */
export function canvasKeyFromPath(pathname: string): string | null {
    const match = /^\/canvas-app\/canvas\/([^/]+)\/?$/.exec(pathname);
    if (!match) return null;
    try { return decodeURIComponent(match[1]); } catch { return null; }
}

export function resourceUploadIdentity(file: Blob, preferred?: string): string {
    if (preferred?.trim()) return preferred.trim();
    const existing = uploadIdentities.get(file);
    if (existing) return existing;
    const identity = `canvas-upload:${crypto.randomUUID()}`;
    uploadIdentities.set(file, identity);
    return identity;
}

export function resourceUploadHeaders(identity?: string, canvasKey?: string | null): Record<string, string> {
    return {
        ...(identity ? { "X-Idempotency-Key": identity } : {}),
        ...(canvasKey ? { "X-Canvas-Key": canvasKey } : {}),
    };
}
