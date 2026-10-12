import { useCallback, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { http as hostHttp } from "@host/api/http";
import { assertUserScope, captureUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import { hostCanvasAccess } from "@/services/host-canvas-access";
import { createCanvasSourceProjectTransport, readCanvasSourceEpisode, readCanvasSourceProject, type CanvasSourceProjectContext } from "@/services/host-source-project";
import { useCanvasOwnerLifetime } from "./canvas-owner-epoch";

type Options = { canvasKey: string; owningProjectId: string; sourceProjectId: string; rowVersion: string; projectLoaded: boolean };
const textError = (error: unknown) => error instanceof Error ? error.message : "来源项目读取失败，请重试";

export function useCanvasSourceProject({ canvasKey, owningProjectId, sourceProjectId, rowVersion, projectLoaded }: Options) {
    const scope = captureUserScope();
    const context: CanvasSourceProjectContext = { canvas_key: canvasKey, canvas_row_version: rowVersion, owning_project_id: owningProjectId, source_project_id: sourceProjectId };
    const { lifetime, mountedRef } = useCanvasOwnerLifetime(canvasKey);
    const live = useRef({ context, scope });
    live.current = { context, scope };
    const contextKey = JSON.stringify({ context, scope });
    const ready = projectLoaded && Boolean(owningProjectId && sourceProjectId) && /^[1-9][0-9]*$/.test(rowVersion);
    const beginRead = useCallback((expectedScope?: CapturedUserScope) => {
        if (JSON.stringify(live.current) !== contextKey) throw new Error("画布或来源项目读取上下文已变化，请重新读取");
        const captured = structuredClone(live.current);
        const owner = lifetime.capture(captured.context.canvas_key);
        const assertCurrent = () => {
            assertUserScope(captured.scope);
            if (expectedScope) assertUserScope(expectedScope);
            if (!mountedRef.current || !lifetime.matches(owner, live.current.context.canvas_key)
                || JSON.stringify(live.current) !== JSON.stringify(captured)) throw new Error("画布或来源项目读取上下文已变化，请重新读取");
            hostCanvasAccess.assert(captured.scope, captured.context.canvas_key);
        };
        assertCurrent();
        return { ...captured, assertCurrent, transport: createCanvasSourceProjectTransport(hostHttp, captured.scope.userScope, assertCurrent) };
    }, [contextKey, lifetime, mountedRef]);
    const query = useQuery({
        queryKey: ["canvas-source-project", contextKey], enabled: ready, retry: false,
        queryFn: async () => {
            const read = beginRead();
            return { overview: await readCanvasSourceProject(read.transport, read.context, read.assertCurrent), contextKey };
        },
    });
    const overview = query.data?.contextKey === contextKey && !query.isError ? query.data.overview : undefined;
    const readEpisode = useCallback(async (episodeId: string, contentVersion: string, expectedScope?: CapturedUserScope, signal?: AbortSignal) => {
        if (expectedScope) assertUserScope(expectedScope);
        const selected = overview?.episodes.find(episode => episode.id === episodeId && episode.content_version === contentVersion);
        if (!selected) throw new Error("分集目录尚未读取或所选版本已变化，请刷新后重新选择");
        const read = beginRead(expectedScope);
        return readCanvasSourceEpisode(read.transport, read.context, selected, read.assertCurrent, signal);
    }, [beginRead, overview]);
    const refreshSourceProject = useCallback(() => query.refetch(), [query.refetch]);
    return {
        overview, contextKey, readEpisode, refreshSourceProject,
        loading: ready && (query.isPending || query.isFetching),
        error: projectLoaded && sourceProjectId && !ready ? "画布来源信息尚未确认，请重新加载画布" : query.isError ? textError(query.error) : "",
    };
}

export type CanvasSourceProjectReader = ReturnType<typeof useCanvasSourceProject>;
