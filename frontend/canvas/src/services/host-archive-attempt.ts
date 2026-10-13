export type ArchiveAttempt = {
    version: 1;
    digest: string;
    title: string;
    items: Array<{ sourceId: string; workspaceId: string; importedId?: string }>;
    folders: Array<{ sourceId: string; importedId?: string }>;
};

type AttemptStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** 只保存归档身份与原创建ID；内容及不可变请求仍由既有画布journal保存。 */
export function archiveAttemptStore(storage: AttemptStorage, accountId: string) {
    if (!/^[1-9]\d*$/.test(accountId)) throw new Error("当前账号身份无效");
    const key = `canvas-archive-attempt:v1:${accountId}`;
    const read = (): ArchiveAttempt | null => {
        const raw = storage.getItem(key);
        if (!raw) return null;
        let value: ArchiveAttempt;
        try { value = JSON.parse(raw) as ArchiveAttempt; }
        catch { throw new Error("导入核对记录损坏，请先在项目列表核对已有画布"); }
        if (!value || value.version !== 1 || !/^[a-f0-9]{64}$/.test(value.digest)
            || typeof value.title !== "string" || !Array.isArray(value.items) || !value.items.length
            || !Array.isArray(value.folders)
            || value.items.some(item => !item || typeof item.sourceId !== "string" || !item.sourceId
                || typeof item.workspaceId !== "string" || !item.workspaceId
                || (item.importedId !== undefined && (typeof item.importedId !== "string" || !item.importedId)))
            || value.folders.some(item => !item || typeof item.sourceId !== "string" || !item.sourceId
                || (item.importedId !== undefined && (typeof item.importedId !== "string" || !item.importedId)))
            || new Set(value.items.map(item => item.sourceId)).size !== value.items.length
            || new Set(value.folders.map(item => item.sourceId)).size !== value.folders.length) {
            throw new Error("导入核对记录损坏，请先在项目列表核对已有画布");
        }
        return value;
    };
    const write = (attempt: ArchiveAttempt) => {
        storage.setItem(key, JSON.stringify(attempt));
        if (storage.getItem(key) !== JSON.stringify(attempt)) throw new Error("无法保存导入核对记录，导入已停止");
    };
    return {
        read,
        prepare(attempt: ArchiveAttempt) {
            const pending = read();
            if (pending && pending.digest !== attempt.digest) throw new Error("上次导入尚未核对，请先核对或重新选择原ZIP文件");
            if (pending) return pending;
            write(attempt);
            return attempt;
        },
        write,
        clear(digest: string) {
            const pending = read();
            if (pending && pending.digest !== digest) throw new Error("导入核对记录已变化，未清除原记录");
            storage.removeItem(key);
        },
    };
}
