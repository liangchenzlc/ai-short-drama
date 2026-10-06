import type { CanvasCreationDto, CanvasCreationRequestDto, CanvasInitialDrawingDto, CanvasDrawingHeadsDto, CanvasReadDto, CanvasRecycleRestoreRequestDto, CanvasRevisionDto, CanvasSummaryDto, CanvasWorkspaceDto } from "@host/api/types/canvases";

export type CanvasTransportRequest = {
    method: string;
    url: string;
    data?: unknown;
    params?: Record<string, unknown>;
    headers?: Record<string, string>;
};
export type CanvasTransport = <T>(request: CanvasTransportRequest) => Promise<T>;

/** Only this boundary translates the source contract; editor components stay unchanged. */
export function createHostCanvasContract(transport: CanvasTransport, observeAliases: (aliases: Record<string, string[]>) => void = () => {}) {
    const resolve = (key: string) => transport<CanvasSummaryDto>({ method: "get", url: `/canvas-workspace/resolve/${encodeURIComponent(key)}` });
    const path = (canvas: CanvasSummaryDto) => `/projects/${canvas.project_id}/canvases/${canvas.id}`;
    const read = async (key: string) => {
        const canvas = await resolve(key);
        return transport<CanvasReadDto>({ method: "get", url: `${path(canvas)}/my-document` });
    };
    const summary = (canvas: CanvasSummaryDto) => ({
        id: canvas.source_key, workspaceProjectId: canvas.project_id,
        title: canvas.title, revision: canvas.row_version,
        createdAt: canvas.created_at, updatedAt: canvas.updated_at,
    });
    const history = (entry: CanvasRevisionDto, sourceKey: string) => ({
        id: entry.id, canvasId: sourceKey, revision: entry.row_version,
        title: entry.title, nodeCount: entry.node_count, connectionCount: entry.connection_count,
        payloadBytes: entry.payload_bytes, reason: entry.reason,
        createdAt: entry.created_at, contentUpdatedAt: entry.content_updated_at,
    });
    const write = <T>(method: string, url: string, data: unknown, key: string) =>
        transport<T>({ method, url, data, headers: { "Idempotency-Key": key } });

    return async (method: string, url: string, data?: unknown, params?: Record<string, unknown>): Promise<unknown> => {
        if (method === "get" && url === "/canvas-projects") {
            const page = Math.max(1, Math.floor(Number(params?.page) || 1));
            const pageSize = Math.max(1, Math.min(100, Math.floor(Number(params?.pageSize) || 50)));
            const result = await transport<CanvasWorkspaceDto>({ method: "get", url: "/canvas-workspace", params: {
                page, page_size: pageSize, q: params?.q, project_id: params?.projectId,
                sort: params?.sort === "created" ? "created" : "updated", include_documents: params?.includeDocuments === true,
            } });
            for (const item of result.items) {
                if (item.source_document) observeAliases(item.resource_aliases ?? {});
            }
            return {
                projects: result.items.map(item => ({ ...summary(item), canvasTitle: item.canvas_title, folderId: item.folder_id || undefined,
                    nodeCount: item.node_count, previewNodes: item.source_document?.nodes ?? item.preview_nodes,
                    ...(item.source_document ? { document: item.source_document } : {}),
                })),
                page: result.page, pageSize: result.page_size, total: result.total, hasMore: result.has_more,
            };
        }

        if (method === "post" && url === "/ops/canvas.document.commit") {
            const input = data as { opId: string; params: { canvasId: string; expectedRevision: string; document: unknown } };
            const canvas = await resolve(input.params.canvasId);
            const saved = await write<CanvasSummaryDto>("post", `${path(canvas)}/commits`, {
                expected_row_version: input.params.expectedRevision, source_document: input.params.document,
            }, input.opId);
            return { op: "canvas.document.commit", opId: input.opId, replayed: false,
                result: { canvasId: saved.source_key, revision: saved.row_version, title: saved.title, updatedAt: saved.updated_at }, revision: saved.row_version };
        }

        const recycleRoute = /^\/canvas-projects\/([^/]+)\/recycle-(restore|purge)$/.exec(url);
        if (method === "post" && recycleRoute) {
            const key = decodeURIComponent(recycleRoute[1]);
            const { revision, archiveKey } = data as { revision: string; archiveKey?: string };
            if (!/^[1-9][0-9]*$/.test(revision)) throw new Error("回收站缺少已删除版本，未恢复画布");
            const payload: CanvasRecycleRestoreRequestDto = { archive_key: archiveKey ?? `canvas-delete:${key}:${revision}` };
            if (recycleRoute[2] === "purge") return write("post", `/canvas-runtime${url}`, payload, `canvas-recycle-purge:${key}:${revision}`);
            const saved = await write<CanvasSummaryDto>("post", `/canvas-runtime${url}`, payload, `canvas-recycle:${key}:${revision}`);
            return { project: summary(saved) };
        }

        const personalRoute = /^\/canvas-projects\/([^/]+)\/(viewport|view-preferences)$/.exec(url);
        if (method === "put" && personalRoute) {
            const canvas = await resolve(decodeURIComponent(personalRoute[1]));
            return transport({ method, url: `${path(canvas)}/${personalRoute[2]}`, data });
        }

        const route = /^\/canvas-projects\/([^/]+)(?:\/history(?:\/([^/]+)(\/restore)?)?)?$/.exec(url);
        if (route) {
            const key = decodeURIComponent(route[1]);
            const snapshotId = route[2] && decodeURIComponent(route[2]);
            if (method === "get" && !url.includes("/history")) {
                const result = await read(key);
                observeAliases(result.resource_aliases ?? {});
                return { project: result.source_document };
            }
            if (method === "put" && !url.includes("/history")) {
                const { project, drawingDocuments } = data as { project: Record<string, unknown>; drawingDocuments?: CanvasInitialDrawingDto[] };
                let projectId = String(project.workspaceProjectId || key);
                // Source multi-canvas duplication groups following canvases by
                // the first copy's source key. Resolve its canonical owner without
                // changing the source's immutable initial-write document.
                if (projectId !== key && !/^[1-9][0-9]*$/.test(projectId)) projectId = (await resolve(projectId)).project_id;
                const document = projectId === key || projectId === project.workspaceProjectId ? project : { ...project, workspaceProjectId: projectId };
                const payload: CanvasCreationRequestDto = { title: typeof project.title === "string" ? project.title : undefined, source_key: key, source_document: document,
                    ...(drawingDocuments?.length ? { drawing_documents: drawingDocuments } : {}),
                };
                const endpoint = projectId === key ? "/canvas-workspace" : `/projects/${encodeURIComponent(projectId)}/canvases`;
                const saved = await write<CanvasCreationDto>("post", endpoint, payload, `canvas-create:${key}`);
                return { project: { ...summary(saved), resource_map: saved.resource_map, resource_aliases: saved.resource_aliases } };
            }
            if (method === "delete") {
                const input = data as { revision: string };
                if (!input?.revision) throw new Error("请先读取画布版本再删除");
                const operationKey = `canvas-delete:${key}:${input.revision}`;
                // Last-canvas deletion archives its project; recover the durable receipt first.
                try {
                    const receipt = await transport<{ operation_kind: string }>({ method: "get", url: `/canvas-write-receipts/${operationKey}` });
                    if (receipt.operation_kind === "canvas.archive") return { id: key };
                } catch (error) {
                    if ((error as { status?: number }).status !== 404) throw error;
                }
                const canvas = await resolve(key);
                await write("delete", path(canvas), { expected_row_version: input.revision }, operationKey);
                return { id: key };
            }
            const canvas = await resolve(key);
            if (method === "get" && !snapshotId) {
                const result = await transport<{ items: CanvasRevisionDto[]; row_version: string; drawing_heads?: CanvasDrawingHeadsDto }>({ method: "get", url: `${path(canvas)}/revisions` });
                return { snapshots: result.items.map(item => history(item, key)), currentRevision: result.row_version, drawingHeads: result.drawing_heads };
            }
            if (method === "get" && snapshotId) {
                const result = await transport<{ revision: CanvasRevisionDto; source_document: unknown; resource_aliases?: Record<string, string[]> }>({ method: "get", url: `${path(canvas)}/revisions/${encodeURIComponent(snapshotId)}` });
                observeAliases(result.resource_aliases ?? {});
                return { snapshot: history(result.revision, key), project: result.source_document };
            }
            if (method === "post" && snapshotId && route[3]) {
                const { revision, drawingHeads } = data as { revision: string; drawingHeads?: CanvasDrawingHeadsDto };
                const saved = await write<CanvasSummaryDto>("post", `${path(canvas)}/revisions/${encodeURIComponent(snapshotId)}/restore`,
                    { expected_row_version: revision, ...(drawingHeads === undefined ? {} : { expected_drawing_heads: drawingHeads }) }, `canvas-restore:${key}:${snapshotId}:${revision}`);
                return { project: summary(saved) };
            }
        }
        // Source /projects and /assets have different meanings from standard-mode routes.
        // Dedicated Python runtime endpoints own their compatibility contracts.
        return transport({ method, url: `/canvas-runtime${url}`, data, params });
    };
}
