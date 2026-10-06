import { compareCanvasRevisions, isCanvasRevision } from "./canvas-server-revision.ts";

/** 旧本机绘图可使用安全整数；服务端版本始终保留十进制字符串。 */
export function drawingRevision(value: unknown): string {
    if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) return String(value);
    if (isCanvasRevision(value)) return value;
    throw new Error("绘图版本无效，请保留草稿并重新读取");
}

export function compareDrawingRevisions(left: unknown, right: unknown) {
    return compareCanvasRevisions(drawingRevision(left), drawingRevision(right));
}

export function nextDrawingRevision(value: unknown) {
    return drawingRevision(String(BigInt(drawingRevision(value)) + BigInt(1)));
}
