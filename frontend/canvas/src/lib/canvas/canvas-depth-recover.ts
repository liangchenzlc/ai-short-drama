import { fitNodeSize, VIDEO_NODE_MAX_SIZE } from "@/lib/canvas/canvas-node-size";
import { mediaResultMetadata } from "@/lib/canvas/canvas-node-semantics";
import { generationErrorMessage } from "@/lib/generation-error";
import {
    assertLocalExecutorSession,
    attachLocalExecutorResult,
    beginLocalExecutorSession,
    isLocalExecutorSessionStop,
    observeLocalExecutorTask,
    type LocalExecutorSession,
} from "@/lib/plugins/builtin/editor/local-executor-session";
import { assertUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import { type DepthCaptureResult } from "@/services/api/depth-capture";
import { getResource, resourceFileUrl, resourceStorageKey } from "@/services/api/resources";
import type { CanvasConnection, CanvasNodeData } from "@/types/canvas";

const NODE_STATUS_ERROR = "error" as const;
const NODE_STATUS_LOADING = "loading" as const;
const NODE_STATUS_SUCCESS = "success" as const;

export type RecoverOwnedDepthCapturePersist = (
    nodes: CanvasNodeData[],
    connections?: CanvasConnection[],
    options?: { expectedScope?: CapturedUserScope; signal?: AbortSignal },
) => Promise<unknown>;

export async function recoverOwnedDepthCaptureNode(input: {
    node: CanvasNodeData;
    session: LocalExecutorSession;
    persist: RecoverOwnedDepthCapturePersist;
    setNodes: (updater: (current: CanvasNodeData[]) => CanvasNodeData[]) => void;
    getLiveNode: (nodeId: string) => CanvasNodeData | undefined;
}): Promise<void> {
    const { node, session } = input;
    const taskId = node.metadata?.taskId;
    const sameTask = (current: CanvasNodeData | undefined) => current?.id === node.id
        && current.type === node.type
        && current.metadata?.taskId === taskId
        && current.metadata?.taskClientOperationId === node.metadata?.taskClientOperationId
        && current.metadata?.taskClientOperationInput === node.metadata?.taskClientOperationInput
        && current.metadata?.depthSourceNodeId === node.metadata?.depthSourceNodeId;
    const pendingTask = (current: CanvasNodeData | undefined) => sameTask(current)
        && current?.metadata?.status === NODE_STATUS_LOADING
        && current.metadata.storageKey === node.metadata?.storageKey;
    let attachedStorageKey: string | undefined;
    try {
        assertLocalExecutorSession(session);
        if (!pendingTask(input.getLiveNode(node.id))) return;
        if (!taskId) {
            input.setNodes((current) => current.map((item) => pendingTask(item) ? {
                ...item,
                metadata: { ...item.metadata, status: NODE_STATUS_ERROR, errorDetails: "深度任务未成功提交，请重新生成" },
            } : item));
            return;
        }
        const completed = await observeLocalExecutorTask(taskId, session, {
            intervalMs: 1000,
            onTaskUpdate: (task) => {
                input.setNodes((current) => current.map((item) => pendingTask(item) ? {
                    ...item,
                    metadata: {
                        ...item.metadata,
                        taskStatus: task.status,
                        taskStage: task.stage,
                        processingLabel: task.stage || "正在生成深度视频",
                        taskProgress: task.progress,
                    },
                } : item));
            },
        });
        assertLocalExecutorSession(session);
        if (!pendingTask(input.getLiveNode(node.id))) return;
        const result = JSON.parse(completed.resultJson || "{}") as DepthCaptureResult;
        if (!result.resourceId) throw new Error("任务完成但没有返回深度视频资源");
        const resource = await getResource(result.resourceId, {
            signal: session.controller.signal,
            expectedScope: session.expectedScope,
        });
        assertLocalExecutorSession(session);
        const liveNode = input.getLiveNode(node.id);
        if (!liveNode || !pendingTask(liveNode)) return;
        const size = fitNodeSize(resource.width || result.width || 1920, resource.height || result.height || 1080, VIDEO_NODE_MAX_SIZE.width, VIDEO_NODE_MAX_SIZE.height);
        const completedNode: CanvasNodeData = {
            ...liveNode,
            width: size.width,
            height: size.height,
            metadata: mediaResultMetadata("derived", {
                ...liveNode.metadata,
                content: resourceFileUrl(result.resourceId),
                storageKey: resourceStorageKey(result.resourceId),
                mimeType: resource.mimeType || "video/mp4",
                bytes: resource.size || result.size,
                naturalWidth: resource.width || result.width || 1920,
                naturalHeight: resource.height || result.height || 1080,
                durationMs: resource.durationMs || result.durationMs,
                status: NODE_STATUS_SUCCESS,
                videoPreview: undefined,
                taskId: completed.id,
                taskStatus: completed.status,
                taskStage: completed.stage,
                taskProgress: 100,
            }),
        };
        await attachLocalExecutorResult(session, async () => {
            attachedStorageKey = completedNode.metadata?.storageKey;
            input.setNodes((current) => current.map((item) => pendingTask(item) ? completedNode : item));
            await input.persist([completedNode], undefined, {
                expectedScope: session.expectedScope,
                signal: session.controller.signal,
            });
        });
    } catch (error) {
        if (isLocalExecutorSessionStop(error)) return;
        try {
            assertLocalExecutorSession(session);
        } catch (stop) {
            if (isLocalExecutorSessionStop(stop)) return;
            throw stop;
        }
        input.setNodes((current) => current.map((item) => (pendingTask(item) || (attachedStorageKey && sameTask(item) && item.metadata?.storageKey === attachedStorageKey)) ? {
            ...item,
            metadata: { ...item.metadata, status: NODE_STATUS_ERROR, taskStatus: "failed", errorDetails: generationErrorMessage(error) },
        } : item));
    }
}

export function recoverOwnedDepthCaptureNodes(input: {
    nodes: CanvasNodeData[];
    signal: AbortSignal;
    expectedScope: CapturedUserScope;
    projectId: string;
    getLiveProjectId: () => string;
    observers: Set<AbortController>;
    persist: RecoverOwnedDepthCapturePersist;
    setNodes: (updater: (current: CanvasNodeData[]) => CanvasNodeData[]) => void;
    getLiveNode: (nodeId: string) => CanvasNodeData | undefined;
}): void {
    if (input.signal.aborted) return;
    try {
        assertUserScope(input.expectedScope);
    } catch (error) {
        if (isLocalExecutorSessionStop(error)) return;
        throw error;
    }
    for (const node of input.nodes) {
        if (!node.metadata?.depthSourceNodeId || node.metadata.status !== NODE_STATUS_LOADING) continue;
        const observer = new AbortController();
        input.observers.add(observer);
        const onAbort = () => observer.abort();
        input.signal.addEventListener("abort", onAbort);
        const session = beginLocalExecutorSession(input.projectId, {
            controller: observer,
            getLiveProjectId: input.getLiveProjectId,
            expectedScope: input.expectedScope,
        });
        void recoverOwnedDepthCaptureNode({
            node,
            session,
            persist: input.persist,
            setNodes: input.setNodes,
            getLiveNode: input.getLiveNode,
        }).finally(() => {
            input.signal.removeEventListener("abort", onAbort);
            input.observers.delete(observer);
        });
    }
}
