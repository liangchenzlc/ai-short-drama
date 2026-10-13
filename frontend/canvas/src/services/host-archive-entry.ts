import { establishCanvasSession } from "./host-session";
import { setActiveUserScope } from "@/lib/user-scope";
import { assertUserScope, captureUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import { archiveAttemptStore, type ArchiveAttempt } from "./host-archive-attempt";
import type { CanvasProject } from "@/stores/canvas/use-canvas-store";

export type HostArchiveResult = { count: number; projectIds: string[]; remaining: number };
export type HostArchiveProgress = { message: string; completed: number; total: number };
let operationActive = false;

export function pendingArchive(accountId: string): { title: string; count: number } | null {
    const pending = archiveAttemptStore(sessionStorage, accountId).read();
    return pending ? { title: pending.title, count: pending.items.length } : null;
}

async function operate<T>(accountId: string, signal: AbortSignal, run: (scope: CapturedUserScope) => Promise<T>): Promise<T> {
    if (operationActive) throw new Error("已有画布导入导出正在进行");
    signal.throwIfAborted();
    operationActive = true;
    const abandon = () => setActiveUserScope(null);
    signal.addEventListener("abort", abandon, { once: true });
    window.addEventListener("session-expired", abandon);
    try {
        const bootstrap = await establishCanvasSession();
        signal.throwIfAborted();
        if (bootstrap.user.id !== accountId) {
            abandon();
            throw new Error("账号已切换，本次操作已停止");
        }
        const scope = captureUserScope();
        const result = await run(scope);
        assertUserScope(scope);
        signal.throwIfAborted();
        return result;
    } finally {
        signal.removeEventListener("abort", abandon);
        window.removeEventListener("session-expired", abandon);
        operationActive = false;
    }
}

async function archiveRuntime(scope: CapturedUserScope) {
    // 验证真实登录后才导入持久store，避免把旧浏览器身份当作认证。
    const [store, repository, archives, exporter] = await Promise.all([
        import("@/stores/canvas/use-canvas-store"), import("./local-workspace-repository"),
        import("@/lib/canvas/canvas-archive-restore"), import("@/lib/canvas/canvas-export"),
    ]);
    assertUserScope(scope);
    const persistence = store.useCanvasStore.persist;
    const options = persistence.getOptions();
    let hydrationError: unknown;
    persistence.setOptions({ onRehydrateStorage: state => {
        const finish = options.onRehydrateStorage?.(state);
        return (hydrated, error) => { hydrationError = error; finish?.(hydrated, error); };
    } });
    try { await persistence.rehydrate(); }
    finally { persistence.setOptions({ onRehydrateStorage: options.onRehydrateStorage }); }
    assertUserScope(scope);
    if (hydrationError) throw new Error("读取画布草稿失败，导入导出已停止，请稍后重试", { cause: hydrationError });
    return { ...store, ...repository, ...archives, ...exporter };
}

type ArchiveRuntime = Awaited<ReturnType<typeof archiveRuntime>>;

async function recoverCreated(attempt: ArchiveAttempt, runtime: ArchiveRuntime, scope: CapturedUserScope) {
    const [{ readInitialCanvasWrite }, { localForageStorageForScope }] = await Promise.all([
        import("./canvas-initial-write"), import("@/lib/localforage-storage"),
    ]);
    for (const item of attempt.items.filter(item => item.importedId)) {
        const id = item.importedId!;
        if (!runtime.useCanvasStore.getState().openProject(id)) throw new Error("导入草稿缺失，请先在项目列表核对已有画布");
        await runtime.openLocalCanvasProjectFromBackend(id, scope);
        assertUserScope(scope);
        if (await readInitialCanvasWrite(localForageStorageForScope(scope.userScope), id)) {
            throw new Error("原导入回执仍未确认，请稍后核对同一次导入");
        }
        const saved = await runtime.readLocalCanvasProjectFromBackend(id, scope);
        if (!saved.revision || saved.revision === "0") throw new Error("原导入尚未保存，请稍后核对");
    }
}

function result(attempt: ArchiveAttempt): HostArchiveResult {
    const projectIds = attempt.items.flatMap(item => item.importedId ? [item.importedId] : []);
    return { count: projectIds.length, projectIds, remaining: attempt.items.length - projectIds.length };
}

export function recoverArchive(accountId: string, signal: AbortSignal): Promise<HostArchiveResult> {
    return operate(accountId, signal, async scope => {
        const attempts = archiveAttemptStore(sessionStorage, accountId);
        const attempt = attempts.read();
        if (!attempt) throw new Error("没有待核对的导入");
        const runtime = await archiveRuntime(scope);
        await recoverCreated(attempt, runtime, scope);
        const recovered = result(attempt);
        if (!recovered.remaining) attempts.clear(attempt.digest);
        return recovered;
    });
}

export function importArchive(file: File, accountId: string, signal: AbortSignal, onProgress: (progress: HostArchiveProgress) => void): Promise<HostArchiveResult> {
    return operate(accountId, signal, async scope => {
        const runtime = await archiveRuntime(scope);
        const archive = await runtime.openCanvasArchive(file);
        assertUserScope(scope);
        if (!archive.data.projects.length) throw new Error("备份中没有画布");
        const { canvasWorkspaceProjectId } = await import("@/lib/canvas/canvas-workspace-project");
        const attempts = archiveAttemptStore(sessionStorage, accountId);
        const attempt = attempts.prepare({ version: 1, digest: await runtime.archiveContentDigest(file), title: file.name,
            items: archive.data.projects.map(item => ({ sourceId: item.project.id, workspaceId: canvasWorkspaceProjectId(item.project) })),
            folders: (archive.data.folders || []).map(folder => ({ sourceId: folder.id })),
        });
        await recoverCreated(attempt, runtime, scope);
        const remaining = archive.data.projects.filter(project => !attempt.items.find(item => item.sourceId === project.project.id)?.importedId);
        const base = runtime.createCanvasArchiveRestoreHost({}, scope);
        let folderIndex = 0;
        try {
            if (remaining.length) await runtime.restoreCanvasArchive({ ...archive, data: { ...archive.data, projects: remaining } }, {
                createFolder: async name => {
                    const folder = attempt.folders[folderIndex++];
                    if (folder.importedId) return folder.importedId;
                    folder.importedId = await base.createFolder(name);
                    attempts.write(attempt);
                    return folder.importedId;
                },
                deleteFolder: async id => {
                    await base.deleteFolder(id);
                    const folder = attempt.folders.find(item => item.importedId === id);
                    if (folder) { delete folder.importedId; attempts.write(attempt); }
                },
                importProject: (project, workspaceId) => {
                    const planned = attempt.items.find(item => item.sourceId === project.id);
                    if (!planned || planned.importedId) throw new Error("导入身份已变化，未重新创建画布");
                    const previous = attempt.items.find(item => item.workspaceId === planned.workspaceId && item.importedId);
                    const group = previous?.importedId ? runtime.useCanvasStore.getState().openProject(previous.importedId)?.workspaceProjectId : undefined;
                    const id = base.importProject(project, group || workspaceId);
                    planned.importedId = id;
                    try { attempts.write(attempt); }
                    catch (error) { runtime.useCanvasStore.getState().deleteProjects([id]); delete planned.importedId; throw error; }
                    return id;
                },
                onProjectProgress: (_, progress) => {
                    assertUserScope(scope);
                    if (progress) onProgress({ message: progress.message, completed: progress.completed, total: progress.total });
                },
            });
            assertUserScope(scope);
            const imported = result(attempt);
            attempts.clear(attempt.digest);
            return imported;
        } catch (error) {
            if (!attempt.items.some(item => item.importedId) && !attempt.folders.some(item => item.importedId)) attempts.clear(attempt.digest);
            throw error;
        }
    });
}

export function exportArchive(projectId: string, title: string, accountId: string, signal: AbortSignal): Promise<void> {
    return operate(accountId, signal, async scope => {
        const runtime = await archiveRuntime(scope);
        const { http } = await import("./api/request");
        const projects: CanvasProject[] = [];
        let page = 1;
        let hasMore = true;
        while (hasMore) {
            const response = await http.get<{ projects: Array<{ document: CanvasProject }>; hasMore: boolean }>("/canvas-projects", {
                params: { page, pageSize: 50, projectId, includeDocuments: true }, expectedScope: scope,
            });
            assertUserScope(scope);
            for (const entry of response.projects) {
                if (!entry.document || entry.document.workspaceProjectId !== projectId) throw new Error("画布来源与项目不一致，导出已停止");
                projects.push(entry.document);
            }
            hasMore = response.hasMore;
            page += 1;
        }
        if (!projects.length) throw new Error("项目没有可导出的画布");
        const saved = await runtime.exportCanvasProjects(projects, title);
        if (saved !== "saved") throw new Error("画布导出未完成");
    });
}
