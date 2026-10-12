import { createElement, useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { App, Button } from "antd";
import type { SetURLSearchParams } from "react-router";

import type { InsertAssetPayload } from "@/components/canvas/asset-picker-modal";
import { canvasAssetHandoffIds, consumeCanvasAssetHandoff } from "@/lib/canvas/canvas-asset-handoff";
import { isUserScopeAbandonedError } from "@/lib/user-scope-guard";
import { queryGenerationTask, type GenerationTask } from "@/services/api/task-center";
import { loadAssetsForUse } from "@/services/local-workspace-sync";
import { isLocalWorkspaceMode } from "@/services/workspace-mode";
import { ensureCanvasNodeAsset } from "@/services/project-asset-sync";
import { persistCanvasDocument } from "@/services/local-workspace-repository";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import type { Asset } from "@/stores/use-asset-store";
import { CanvasNodeType, type CanvasNodeData, type Position } from "@/types/canvas";

import { readOwnedCanvasNodes, runOwnedCanvasEnsureQueue, runOwnedCanvasPageCommit, useCanvasOwnerLifetime } from "./canvas-owner-epoch";
import { applyArchivedCanvasNodeAssets, CANVAS_HANDOFF_PERSIST_FAILED_MESSAGE, commitOwnedCanvasAssetHandoff, rebaseCreatedCanvasNodes } from "./canvas-resource-handoff-commit";
import { planCanvasHandoffEffect, resolveCanvasAssetHandoffPlan } from "./canvas-resource-handoff-plan";

type UseCanvasResourceHandoffOptions = {
    projectId: string;
    linkedProjectId: string;
    refetchLinkedProject: () => Promise<unknown>;
    projectLoaded: boolean;
    assets: Asset[];
    assetsHydrated: boolean;
    nodesRef: { current: CanvasNodeData[] };
    searchParams: URLSearchParams;
    setSearchParams: SetURLSearchParams;
    setNodes: Dispatch<SetStateAction<CanvasNodeData[]>>;
    getCanvasCenter: () => Position;
    createHandoffNodes: (payloads: InsertAssetPayload[], origin: Position) => Promise<CanvasNodeData[]>;
    applyGenerationTaskResult: (nodeId: string, task: GenerationTask) => Promise<void>;
};

export function useCanvasResourceHandoff({
    projectId,
    linkedProjectId,
    refetchLinkedProject,
    projectLoaded,
    assets,
    assetsHydrated,
    nodesRef,
    searchParams,
    setSearchParams,
    setNodes,
    getCanvasCenter,
    createHandoffNodes,
    applyGenerationTaskResult,
}: UseCanvasResourceHandoffOptions) {
    const { message } = App.useApp();
    const assetHandoffRef = useRef("");
    const handoffOwnershipRef = useRef<string | null>(null);
    const failedAttemptRef = useRef("");
    const nonceAtFailRef = useRef(0);
    const [handoffRetryNonce, setHandoffRetryNonce] = useState(0);
    const projectIdRef = useRef(projectId);
    projectIdRef.current = projectId;
    const { lifetime, mountedRef, userScope } = useCanvasOwnerLifetime(projectId);
    useEffect(() => () => message.destroy(`canvas-handoff:${projectId}`), [message, projectId, userScope]);
    const archiveNodesToLinkedFolder = useCallback(
        (folder: CanvasNodeData, droppedNodes: CanvasNodeData[]) => {
            const folderId = folder.metadata?.folder?.assetFolderId;
            const domainProjectId = folder.metadata?.folder?.projectId || linkedProjectId;
            if (!folderId || !domainProjectId || !droppedNodes.length) return;
            const owner = lifetime.capture(projectId);
            const folderTitle = folder.title;
            void runOwnedCanvasPageCommit({
                owner,
                getLiveCanvasId: () => projectIdRef.current,
                getLiveLifetime: () => lifetime.current(),
                work: () => runOwnedCanvasEnsureQueue({
                    owner,
                    items: droppedNodes,
                    ensure: (node) => ensureCanvasNodeAsset({ canvasId: owner.canvasId, domainProjectId, folderId, node, source: "canvas-manual" }),
                }),
                onCommit: (results) => {
                    const archivedByNodeId = new Map(droppedNodes.flatMap((node, index) => {
                        const result = results[index];
                        if (!result?.confirmed) return [];
                        return [[node.id, { assetId: result.assetId, content: node.metadata?.content, previousAssetId: node.metadata?.assetId }] as const];
                    }));
                    setNodes((current) => applyArchivedCanvasNodeAssets(current, archivedByNodeId));
                    void refetchLinkedProject();
                    if (results.some((result) => !result?.confirmed)) {
                        message.warning("部分素材文件尚未保存到工作区，未完成归档，修改已保留在本机");
                    } else {
                        message.success(`已归档到“${folderTitle}”`);
                    }
                },
            }).catch((error) => {
                if (!lifetime.matches(owner, projectIdRef.current)) return;
                message.error(error instanceof Error ? error.message : "素材归档失败");
            });
        },
        [lifetime, linkedProjectId, message, projectId, refetchLinkedProject, setNodes],
    );

    useEffect(() => {
        if (!projectLoaded || searchParams.get("mode") !== "handoff") return;
        void loadAssetsForUse(canvasAssetHandoffIds(searchParams)).catch((error) => message.error(error instanceof Error ? error.message : "转入素材读取失败"));
    }, [projectLoaded, searchParams, message]);

    const requestHandoffRetry = useCallback(() => {
        if (assetHandoffRef.current) return;
        if (handoffOwnershipRef.current && handoffOwnershipRef.current !== userScope) return;
        message.destroy(`canvas-handoff:${projectId}`);
        failedAttemptRef.current = "";
        assetHandoffRef.current = "";
        setHandoffRetryNonce((nonce) => nonce + 1);
    }, [message, projectId, userScope]);

    const consumeForeignHandoff = useCallback((nextSearchParams: URLSearchParams) => {
        handoffOwnershipRef.current = null;
        assetHandoffRef.current = "";
        failedAttemptRef.current = "";
        setSearchParams(nextSearchParams, { replace: true });
    }, [setSearchParams]);

    useEffect(() => {
        const plan = resolveCanvasAssetHandoffPlan({
            projectLoaded,
            assetsHydrated,
            mode: searchParams.get("mode"),
            projectId,
            assets,
            searchParams,
            currentKey: assetHandoffRef.current,
            nodes: nodesRef.current,
        });
        const decision = planCanvasHandoffEffect({
            plan,
            searchParams,
            ownerUserScope: handoffOwnershipRef.current,
            liveUserScope: userScope,
            currentAttemptKey: assetHandoffRef.current,
            failedKey: failedAttemptRef.current,
            retryNonce: handoffRetryNonce,
            nonceAtFail: nonceAtFailRef.current,
        });
        if (decision.kind === "idle") return;
        if (decision.kind === "consume-foreign") {
            consumeForeignHandoff(decision.searchParams);
            return;
        }
        if (decision.kind === "blocked-until-retry") return;
        if (!handoffOwnershipRef.current) handoffOwnershipRef.current = userScope;
        assetHandoffRef.current = decision.key;
        if (decision.kind === "wait") return;
        const attemptKey = decision.key;
        const owner = lifetime.capture(projectId);
        const resetIfCurrentAttempt = () => {
            if (assetHandoffRef.current === attemptKey) assetHandoffRef.current = "";
        };
        void (async () => {
            try {
                const createdNodes = decision.payloads.length ? await createHandoffNodes(decision.payloads, getCanvasCenter()) : [];
                const result = await commitOwnedCanvasAssetHandoff({
                    owner,
                    getLiveCanvasId: () => projectIdRef.current,
                    getLiveLifetime: () => lifetime.current(),
                    stillOwnsPage: () => mountedRef.current && lifetime.matches(owner, projectIdRef.current),
                    attemptKey,
                    getAttemptKey: () => assetHandoffRef.current,
                    searchParams,
                    createdNodes,
                    readLiveNodes: () => readOwnedCanvasNodes({
                        owner,
                        liveCanvasId: projectIdRef.current,
                        liveLifetime: lifetime.current(),
                        pageNodes: nodesRef.current,
                        storedNodes: useCanvasStore.getState().openProject(owner.canvasId)?.nodes,
                    }),
                    persist: async (nextNodes) => {
                        await persistCanvasDocument(owner.canvasId, { nodes: nextNodes });
                    },
                    applyCreated: (created) => {
                        setNodes((current) => rebaseCreatedCanvasNodes(current, created));
                    },
                    consumeUrl: (nextSearchParams) => {
                        handoffOwnershipRef.current = null;
                        failedAttemptRef.current = "";
                        setSearchParams(nextSearchParams, { replace: true });
                    },
                    resetAttempt: resetIfCurrentAttempt,
                    onPersistError: () => {
                        if (assetHandoffRef.current !== attemptKey && assetHandoffRef.current !== "") return;
                        if (!lifetime.userMatches(owner)) {
                            consumeForeignHandoff(consumeCanvasAssetHandoff(searchParams));
                            return;
                        }
                        if (!mountedRef.current) return;
                        failedAttemptRef.current = attemptKey;
                        nonceAtFailRef.current = handoffRetryNonce;
                        message.error({
                            key: `canvas-handoff:${projectId}`,
                            content: createElement("span", null, CANVAS_HANDOFF_PERSIST_FAILED_MESSAGE, " ",
                                createElement(Button, { type: "link", size: "small", onClick: requestHandoffRetry }, "重试")),
                            duration: 0,
                        });
                    },
                });
                if (result === "abandoned" && !lifetime.userMatches(owner)) {
                    consumeForeignHandoff(consumeCanvasAssetHandoff(searchParams));
                    return;
                }
                if (result === "committed") {
                    handoffOwnershipRef.current = null;
                    failedAttemptRef.current = "";
                }
                if (result === "committed" && createdNodes.length && lifetime.matches(owner, projectIdRef.current)) {
                    message.success(`已引入 ${createdNodes.length} 项项目资产`);
                }
            } catch (error) {
                resetIfCurrentAttempt();
                if (!lifetime.userMatches(owner) || isUserScopeAbandonedError(error)) {
                    if (!lifetime.userMatches(owner)) consumeForeignHandoff(consumeCanvasAssetHandoff(searchParams));
                    return;
                }
                if (!mountedRef.current) return;
                message.error(error instanceof Error ? error.message : "项目资产引入失败");
            }
        })();
    }, [assets, assetsHydrated, consumeForeignHandoff, createHandoffNodes, getCanvasCenter, handoffRetryNonce, lifetime, message, mountedRef, nodesRef, projectId, projectLoaded, requestHandoffRetry, searchParams, setNodes, setSearchParams, userScope]);

    const reloadCanvasNodeResource = useCallback(
        async (node: CanvasNodeData) => {
            const taskId = node.metadata?.taskId;
            if (!taskId || !node.metadata?.resourceReloadAvailable) return;
            if (isLocalWorkspaceMode() || import.meta.env.VITE_CANVAS_LOCAL_MODE !== "false") {
                message.info("本地工作区不会从云端重新加载任务资源，请直接在画布中重新生成");
                return;
            }
            const owner = lifetime.capture(projectId);
            const targetNodeId = node.id;
            setNodes((current) => current.map((item) => (item.id === targetNodeId ? { ...item, metadata: { ...item.metadata, status: "loading", taskStage: "正在重新加载资源", errorDetails: undefined } } : item)));
            try {
                const task = await queryGenerationTask(taskId);
                if (!lifetime.matches(owner, projectIdRef.current)) return;
                if (task.status !== "succeeded") throw new Error("原生成任务尚未成功，无法重新加载资源");
                await applyGenerationTaskResult(targetNodeId, task);
            } catch (error) {
                if (!lifetime.matches(owner, projectIdRef.current)) return;
                setNodes((current) =>
                    current.map((item) => (item.id === targetNodeId ? { ...item, metadata: { ...item.metadata, status: "error", errorDetails: error instanceof Error ? error.message : "资源重新加载失败", resourceReloadAvailable: true } } : item)),
                );
            }
        },
        [applyGenerationTaskResult, lifetime, message, projectId, setNodes],
    );

    return {
        archiveNodesToLinkedFolder,
        reloadCanvasNodeResource,
    };
}
