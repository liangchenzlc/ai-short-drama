import { collectCanvasResourceIds } from "./host-resource-normalization.ts";

/** Only Excalidraw file entries are embedded; text and exact shape coordinates stay untouched. */
export async function portableDrawingSnapshot(snapshot: unknown, loadResource: (id: string) => Promise<Blob>) {
    const copy = structuredClone(snapshot) as { files?: Record<string, { dataURL?: string }> } | null;
    if (!copy?.files || typeof copy.files !== "object") return copy;
    const embedded = new Map<string, Promise<string>>();
    for (const file of Object.values(copy.files)) {
        if (!file || typeof file.dataURL !== "string") continue;
        const [id] = collectCanvasResourceIds({ url: file.dataURL });
        if (!id) continue;
        let pending = embedded.get(id);
        if (!pending) {
            pending = loadResource(id).then(async blob => {
                if (!blob.size || !blob.type.startsWith("image/")) throw new Error("画板引用图片无法打包，未生成不完整备份");
                const bytes = new Uint8Array(await blob.arrayBuffer());
                let binary = "";
                for (let offset = 0; offset < bytes.length; offset += 8192) binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
                return `data:${blob.type};base64,${btoa(binary)}`;
            });
            embedded.set(id, pending);
        }
        file.dataURL = await pending;
    }
    return copy;
}
