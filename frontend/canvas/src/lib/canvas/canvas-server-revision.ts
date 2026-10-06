const UINT64_MAX = "18446744073709551615";

export function isCanvasRevision(value: unknown): value is string {
    return typeof value === "string" && /^(0|[1-9][0-9]*)$/.test(value)
        && (value.length < UINT64_MAX.length || value.length === UINT64_MAX.length && value <= UINT64_MAX);
}

export function compareCanvasRevisions(left: string, right: string): number {
    if (!isCanvasRevision(left) || !isCanvasRevision(right)) throw new Error("画布版本无效，请保留草稿并重新读取");
    return left.length === right.length ? left === right ? 0 : left < right ? -1 : 1 : left.length < right.length ? -1 : 1;
}

export function maxCanvasRevision(left: string, right: string): string {
    return compareCanvasRevisions(left, right) >= 0 ? left : right;
}
