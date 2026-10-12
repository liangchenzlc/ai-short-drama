import { canvasNodesMissingResourceAssetBinding } from "@/lib/canvas/canvas-node-asset";
import { assertUserScope, captureUserScope, isUserScopeAbandonedError, type CapturedUserScope } from "@/lib/user-scope-guard";
import { canvasResourceKeysEquivalent } from "@/services/host-resource-identities";
import type { CanvasNodeAssetResult } from "@/services/project-asset-sync";
import type { CanvasConnection, CanvasNodeData } from "@/types/canvas";

export class CanvasMediaPersistUnconfirmedError extends Error {
    constructor() {
        super("素材还没有保存完成，无法写入画布，请稍后重试");
        this.name = "CanvasMediaPersistUnconfirmedError";
    }
}

function throwIfAborted(signal?: AbortSignal) {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
}

function isPersistStop(error: unknown) {
    return isUserScopeAbandonedError(error) || (error instanceof Error && error.name === "AbortError") || error instanceof CanvasMediaPersistUnconfirmedError;
}

export type PersistOwnedCanvasMediaNodesInput = {
    canvasId: string;
    domainProjectId?: string;
    mediaNodes: CanvasNodeData[];
    mediaConnections?: CanvasConnection[];
    expectedScope?: CapturedUserScope;
    signal?: AbortSignal;
};

export type PersistOwnedCanvasMediaNodesStore = {
    nodes: CanvasNodeData[];
    connections: CanvasConnection[];
};

export type PersistOwnedCanvasMediaNodesDeps = {
    ensureCanvasNodeAsset: (options: {
        canvasId: string;
        domainProjectId?: string;
        node: CanvasNodeData;
        source: "canvas-manual";
        signal?: AbortSignal;
        expectedScope?: CapturedUserScope;
    }) => Promise<CanvasNodeAssetResult>;
    getStoredProject: (canvasId: string) => PersistOwnedCanvasMediaNodesStore | undefined;
    hasProject: (canvasId: string) => boolean;
    openProject?: (canvasId: string, expectedScope: CapturedUserScope) => Promise<unknown>;
    updateProject: (canvasId: string, patch: PersistOwnedCanvasMediaNodesStore) => void;
    flushPersistence: (expectedScope: CapturedUserScope) => Promise<void>;
    syncSnapshot?: (canvasId: string, patch: PersistOwnedCanvasMediaNodesStore, expectedScope: CapturedUserScope) => Promise<unknown>;
    readSavedProject?: (canvasId: string, expectedScope: CapturedUserScope) => Promise<{ nodes: CanvasNodeData[] } | undefined>;
    setNodes: (updater: (current: CanvasNodeData[]) => CanvasNodeData[]) => void;
    getLiveProjectId: () => string;
    getLiveNodes: () => CanvasNodeData[];
    getLiveConnections: () => CanvasConnection[];
    isLocalWorkspace: () => boolean;
    warn?: (message: string) => void;
    error?: (message: string) => void;
};

export async function persistOwnedCanvasMediaNodes(
    input: PersistOwnedCanvasMediaNodesInput,
    deps: PersistOwnedCanvasMediaNodesDeps,
): Promise<Map<string, string>> {
    const expected = input.expectedScope ?? captureUserScope();
    const originalProjectId = input.canvasId;
    const mediaConnections = input.mediaConnections ?? [];
    const assetIds = new Map<string, string>();
    const bindingNodes = new Map<string, CanvasNodeData>();
    const observedLiveNodeIds = new Set(deps.getLiveNodes().map((node) => node.id));
    const observedLiveConnectionIds = new Set(deps.getLiveConnections().map((connection) => connection.id));

    const sameMedia = (current: CanvasNodeData, frozen: CanvasNodeData) => {
        if (current.type !== frozen.type) return false;
        const currentKey = current.metadata?.storageKey;
        const frozenKey = frozen.metadata?.storageKey;
        return currentKey && frozenKey
            ? canvasResourceKeysEquivalent(currentKey, frozenKey)
            : currentKey === frozenKey && current.metadata?.content === frozen.metadata?.content;
    };

    const assertPersistOwnership = () => {
        throwIfAborted(input.signal);
        assertUserScope(expected);
        if (deps.getLiveProjectId() !== originalProjectId) {
            throw new DOMException("Aborted", "AbortError");
        }
    };

    const persistAsset = async (node: CanvasNodeData, required: boolean) => {
        assertPersistOwnership();
        bindingNodes.set(node.id, { ...node, metadata: { ...node.metadata } });
        try {
            const result = await deps.ensureCanvasNodeAsset({
                canvasId: originalProjectId,
                domainProjectId: input.domainProjectId,
                node,
                source: "canvas-manual",
                signal: input.signal,
                expectedScope: expected,
            });
            assertPersistOwnership();
            if (!result.confirmed) throw new CanvasMediaPersistUnconfirmedError();
            assetIds.set(node.id, result.assetId);
            return result.assetId;
        } catch (error) {
            if (isPersistStop(error)) throw error;
            assertPersistOwnership();
            const details = error instanceof Error ? error.message : "未知错误";
            if (required) {
                deps.warn?.(`已有媒体节点尚未完成素材库绑定，无法保存本次结果：${details}`);
                throw error;
            }
            deps.warn?.(`媒体节点已创建，但素材库写入失败：${details}`);
            return undefined;
        }
    };

    for (const mediaNode of input.mediaNodes) {
        await persistAsset(mediaNode, false);
    }

    const readLatestNodes = () => {
        assertPersistOwnership();
        const liveNodes = deps.getLiveNodes();
        for (const node of liveNodes) observedLiveNodeIds.add(node.id);
        const latestNodes = new Map(liveNodes.map((node) => [node.id, node]));
        for (const node of deps.getStoredProject(originalProjectId)?.nodes || []) {
            if (!latestNodes.has(node.id) && !observedLiveNodeIds.has(node.id)) latestNodes.set(node.id, node);
        }
        for (const mediaNode of input.mediaNodes) {
            if (!latestNodes.has(mediaNode.id) && !observedLiveNodeIds.has(mediaNode.id)) latestNodes.set(mediaNode.id, mediaNode);
        }
        for (const [id, frozen] of bindingNodes) {
            const current = latestNodes.get(id);
            if (!current || !sameMedia(current, frozen)) {
                throw new DOMException("媒体节点已删除或资源已变化，停止写入旧媒体结果", "AbortError");
            }
            const assetId = assetIds.get(id);
            if (assetId) latestNodes.set(id, { ...current, metadata: { ...current.metadata, assetId } });
        }
        return latestNodes;
    };

    for (const node of canvasNodesMissingResourceAssetBinding([...readLatestNodes().values()])) {
        const current = readLatestNodes().get(node.id);
        if (current) await persistAsset(current, true);
    }

    assertPersistOwnership();
    if (!deps.hasProject(originalProjectId) && deps.isLocalWorkspace()) {
        await deps.openProject?.(originalProjectId, expected);
        assertPersistOwnership();
    }
    if (!deps.hasProject(originalProjectId)) {
        throw new Error("当前画布未载入本地项目库，无法保存媒体结果");
    }

    const readLatestSnapshot = () => {
        const latestNodes = readLatestNodes();
        const liveConnections = deps.getLiveConnections();
        for (const connection of liveConnections) observedLiveConnectionIds.add(connection.id);
        const latestConnections = new Map(liveConnections.map((connection) => [connection.id, connection]));
        for (const connection of [...(deps.getStoredProject(originalProjectId)?.connections || []), ...mediaConnections]) {
            if (!latestConnections.has(connection.id) && !observedLiveConnectionIds.has(connection.id)) latestConnections.set(connection.id, connection);
        }
        return {
            nodes: [...latestNodes.values()],
            connections: [...latestConnections.values()].filter((connection) => latestNodes.has(connection.fromNodeId) && latestNodes.has(connection.toNodeId)),
        };
    };

    const snapshot = readLatestSnapshot();
    if (assetIds.size > 0) {
        deps.setNodes((current) => current.map((item) => {
            const assetId = assetIds.get(item.id);
            const frozen = bindingNodes.get(item.id);
            return assetId && frozen && sameMedia(item, frozen) ? { ...item, metadata: { ...item.metadata, assetId } } : item;
        }));
    }
    deps.updateProject(originalProjectId, snapshot);
    try {
        assertPersistOwnership();
        await deps.flushPersistence(expected);
        assertPersistOwnership();
        if (deps.isLocalWorkspace()) {
            await deps.syncSnapshot?.(originalProjectId, readLatestSnapshot(), expected);
            assertPersistOwnership();
            const saved = await deps.readSavedProject?.(originalProjectId, expected);
            assertPersistOwnership();
            if (!saved || input.mediaNodes.some((node) => !saved.nodes.some((savedNode) => savedNode.id === node.id))) {
                throw new Error("本地项目库回读校验未包含刚生成的媒体节点");
            }
        }
    } catch (error) {
        if (isPersistStop(error)) throw error;
        assertPersistOwnership();
        deps.error?.(`媒体结果已生成，但本地画布保存失败：${error instanceof Error ? error.message : "未知错误"}`);
        throw error;
    }
    return assetIds;
}
