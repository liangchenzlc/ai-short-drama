import { useCallback, useEffect, useRef, type Dispatch, type SetStateAction } from "react";
import { useQuery } from "@tanstack/react-query";
import { http as hostHttp } from "@host/api/http";
import { assertUserScope, captureUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import { refreshCanvasCharacterReferenceNodes } from "@/lib/canvas/canvas-character-reference";
import { ownedResourceIdFromMediaRef } from "@/services/api/resources";
import { canvasResourceKeysEquivalent } from "@/services/host-resource-identities";
import { hostCanvasAccess } from "@/services/host-canvas-access";
import { createCanvasCharacterTransport, readCanvasCharacterCatalog, readCanvasCharacterDetail, type CanvasCharacterContext } from "@/services/host-character-catalog";
import type { CanvasNodeData } from "@/types/canvas";
import { useCanvasOwnerLifetime } from "./canvas-owner-epoch";

type Options = {
    canvasKey: string;
    sourceProjectId: string;
    rowVersion: string;
    projectLoaded: boolean;
    detailNode: CanvasNodeData | null;
    nodesRef: { current: CanvasNodeData[] };
    setNodes: Dispatch<SetStateAction<CanvasNodeData[]>>;
};

const coverEquivalent = (left?: string, right?: string) => {
    if (left === right) return true;
    const leftId = ownedResourceIdFromMediaRef(undefined, left);
    const rightId = ownedResourceIdFromMediaRef(undefined, right);
    return Boolean(leftId && rightId && canvasResourceKeysEquivalent(`resource:${leftId}`, `resource:${rightId}`));
};
const textError = (error: unknown) => error instanceof Error ? error.message : "角色读取失败，请重试";

export function useCanvasCharacterCatalog({ canvasKey, sourceProjectId, rowVersion, projectLoaded, detailNode, nodesRef, setNodes }: Options) {
    const scope = captureUserScope();
    const context: CanvasCharacterContext = { canvas_key: canvasKey, canvas_row_version: rowVersion, source_project_id: sourceProjectId };
    const { lifetime, mountedRef } = useCanvasOwnerLifetime(canvasKey);
    const live = useRef({ context, scope });
    live.current = { context, scope };
    const contextKey = JSON.stringify({ context, scope });
    const ready = projectLoaded && Boolean(sourceProjectId) && /^[1-9][0-9]*$/.test(rowVersion);

    const beginRead = useCallback((expectedScope?: CapturedUserScope) => {
        if (JSON.stringify(live.current) !== contextKey) throw new Error("画布或角色读取上下文已变化，请重新读取");
        const captured = structuredClone(live.current);
        const owner = lifetime.capture(captured.context.canvas_key);
        const assertCurrent = () => {
            assertUserScope(captured.scope);
            if (expectedScope) assertUserScope(expectedScope);
            if (!mountedRef.current || !lifetime.matches(owner, live.current.context.canvas_key)
                || JSON.stringify(live.current) !== JSON.stringify(captured)) throw new Error("画布或角色读取上下文已变化，请重新读取");
            hostCanvasAccess.assert(captured.scope, captured.context.canvas_key);
        };
        assertCurrent();
        return { ...captured, assertCurrent, transport: createCanvasCharacterTransport(hostHttp, captured.scope.userScope, assertCurrent), startingNodes: structuredClone(nodesRef.current.filter(node => node.metadata?.workflowKind === "character" && node.metadata.characterAssetId)) };
    }, [contextKey, lifetime, mountedRef, nodesRef]);

    const catalog = useQuery({
        queryKey: ["canvas-character-catalog", contextKey], enabled: ready, retry: false,
        queryFn: async () => {
            const read = beginRead();
            const cards = await readCanvasCharacterCatalog(read.transport, read.context, read.assertCurrent);
            return { cards, startingNodes: read.startingNodes, contextKey };
        },
    });
    const detailAssetId = detailNode?.metadata?.characterAssetId || "";
    const pinnedVersion = detailNode?.metadata?.characterVersionPolicy === "pinned" ? detailNode.metadata.characterVersionId : undefined;
    const detailSupported = detailNode?.metadata?.characterVersionPolicy !== "pinned" || Boolean(pinnedVersion);
    const detail = useQuery({
        queryKey: ["canvas-character-detail", contextKey, detailNode?.id || "", detailAssetId, pinnedVersion || "current"],
        enabled: ready && Boolean(detailAssetId) && detailSupported, retry: false,
        queryFn: async () => {
            const read = beginRead();
            const card = await readCanvasCharacterDetail(read.transport, read.context, detailAssetId, read.assertCurrent, undefined, pinnedVersion);
            return { cards: [card], startingNodes: read.startingNodes, contextKey };
        },
    });

    useEffect(() => {
        const result = catalog.data;
        if (!result || result.contextKey !== contextKey || catalog.isError) return;
        const expectedNodes = new Map(result.startingNodes.map(node => [node.id, node]));
        setNodes(current => {
            try { beginRead().assertCurrent(); } catch { return current; }
            return refreshCanvasCharacterReferenceNodes(current, result.cards, { expectedNodes, coverEquivalent, preservePrivateVoice: true, preservePrivatePrompt: true });
        });
    }, [beginRead, catalog.data, catalog.isError, contextKey, setNodes]);
    useEffect(() => {
        const result = detail.data;
        if (!result || result.contextKey !== contextKey || detail.isError || !detailNode) return;
        const expectedNodes = new Map(result.startingNodes.filter(node => node.id === detailNode.id).map(node => [node.id, node]));
        setNodes(current => {
            try { beginRead().assertCurrent(); } catch { return current; }
            return refreshCanvasCharacterReferenceNodes(current, result.cards, { expectedNodes, coverEquivalent, preservePrivateVoice: true, preservePrivatePrompt: true, refreshPinned: Boolean(pinnedVersion) });
        });
    }, [beginRead, contextKey, pinnedVersion, detail.data, detail.isError, detailNode?.id, setNodes]);

    const readSelectedCharacter = useCallback(async (assetId: string, expectedScope: CapturedUserScope) => {
        assertUserScope(expectedScope);
        const selected = catalog.data?.contextKey === contextKey ? catalog.data.cards.find(card => card.id === assetId) : undefined;
        if (!selected || catalog.isError) throw new Error("角色目录尚未完成读取，请刷新后重新选择");
        const read = beginRead(expectedScope);
        return readCanvasCharacterDetail(read.transport, read.context, assetId, read.assertCurrent, selected.character.versionId);
    }, [beginRead, catalog.data, catalog.isError, contextKey]);
    const refreshCharacters = useCallback(() => { void catalog.refetch(); }, [catalog.refetch]);
    const retryDetail = useCallback(() => { void detail.refetch(); }, [detail.refetch]);
    const sourceError = projectLoaded && !sourceProjectId ? "此画布尚未关联可读取的项目角色" : "";
    return {
        characters: catalog.data?.contextKey === contextKey && !catalog.isError ? catalog.data.cards : [],
        loading: ready && (catalog.isPending || catalog.isFetching),
        error: sourceError || (catalog.isError ? textError(catalog.error) : ""),
        refreshCharacters, readSelectedCharacter,
        detailLoading: ready && Boolean(detailAssetId) && detailSupported && (detail.isPending || detail.isFetching),
        detailError: sourceError || (!detailSupported ? "固定版本角色缺少有效版本，请重新选择角色" : detail.isError ? textError(detail.error) : ""),
        retryDetail,
    };
}
