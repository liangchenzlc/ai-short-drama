import type { GenericAbortSignal } from "axios";

export type CanvasTaskAdmissionInput = {
    projectId?: string;
    type?: string;
    logicalModelId?: string;
    prompt: string;
    input?: Record<string, unknown>;
};

export type CanvasTaskAdmissionScope = { userScope: string; epoch: number };
type AdmissionStore = {
    getItem(key: string): string | null | Promise<string | null>;
    setItem(key: string, value: string): unknown | Promise<unknown>;
    removeItem(key: string): unknown | Promise<unknown>;
};

export function canvasTaskAdmissionIdentity(input: CanvasTaskAdmissionInput) {
    const metadata = input.input?.metadata;
    if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) throw new Error("生成请求缺少节点关联");
    const value = metadata as Record<string, unknown>;
    const nodeId = value.nodeId;
    const sourceNodeId = value.sourceNodeId;
    const operationId = value.clientOperationId;
    if (!input.projectId || typeof nodeId !== "string" || !nodeId || typeof sourceNodeId !== "string" || !sourceNodeId || typeof operationId !== "string" || !operationId) {
        throw new Error("生成请求缺少稳定画布、节点或操作身份");
    }
    if (!input.logicalModelId || !/^[1-9]\d*$/.test(input.logicalModelId)) throw new Error("请先选择当前账号的可用模型");
    return { projectId: input.projectId, nodeId, sourceNodeId, operationId };
}

function assertNoCredentials(value: unknown) {
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
        if (/^(apikey|secretkey|authorization|accesstoken|refreshtoken|password|credential)$/i.test(key.replaceAll("_", ""))) {
            throw new Error("画布生成请求不能包含客户端密钥");
        }
        assertNoCredentials(child);
    }
}

/** Save the source first; uncertain admission replays only the frozen request and its original key. */
export function createCanvasTaskAdmission<T extends { id: string }>(dependencies: {
    store(scope: CanvasTaskAdmissionScope): AdmissionStore;
    assertCurrent(scope: CanvasTaskAdmissionScope): void;
    saveSource(input: CanvasTaskAdmissionInput, scope: CanvasTaskAdmissionScope): Promise<void>;
    post(input: CanvasTaskAdmissionInput, operationId: string, scope: CanvasTaskAdmissionScope, signal?: GenericAbortSignal): Promise<T>;
    isUncertain(error: unknown): boolean;
}) {
    const running = new Map<string, { body: string; promise: Promise<T> }>();
    return {
        submit(input: CanvasTaskAdmissionInput, scope: CanvasTaskAdmissionScope, signal?: GenericAbortSignal): Promise<T> {
            dependencies.assertCurrent(scope);
            assertNoCredentials(input);
            const identity = canvasTaskAdmissionIdentity(input);
            const body = JSON.stringify(input);
            const slot = `canvas-task-admission:${identity.operationId}`;
            const lock = `${scope.userScope}\0${scope.epoch}\0${slot}`;
            const existing = running.get(lock);
            if (existing) {
                if (existing.body !== body) throw new Error("同一生成操作不能更改请求内容");
                return existing.promise;
            }
            const assertCurrent = () => {
                dependencies.assertCurrent(scope);
                if (signal?.aborted) throw new DOMException("请求已取消", "AbortError");
            };
            const promise = Promise.resolve().then(async () => {
                assertCurrent();
                const store = dependencies.store(scope);
                const saved = await store.getItem(slot);
                assertCurrent();
                if (saved && saved !== body) throw new Error("原生成请求受理情况未确认，不能用同一操作更改参数");
                const frozen = JSON.parse(saved ?? body) as CanvasTaskAdmissionInput;
                await dependencies.saveSource(frozen, scope);
                assertCurrent();
                await store.setItem(slot, saved ?? body);
                assertCurrent();
                let task: T;
                try {
                    try {
                        task = await dependencies.post(frozen, identity.operationId, scope, signal);
                    } catch (error) {
                        assertCurrent();
                        if (!dependencies.isUncertain(error)) throw error;
                        task = await dependencies.post(frozen, identity.operationId, scope, signal);
                    }
                } catch (error) {
                    assertCurrent();
                    if (!dependencies.isUncertain(error)) await store.removeItem(slot);
                    throw error;
                }
                assertCurrent();
                // Admission already persisted the private task binding. Journal cleanup is optional.
                try { await store.removeItem(slot); } catch { /* A stale identical journal remains safe to replay. */ }
                assertCurrent();
                return task;
            }).finally(() => { if (running.get(lock)?.promise === promise) running.delete(lock); });
            running.set(lock, { body, promise });
            return promise;
        },
    };
}

export function canvasRecoveryTaskForNode<T extends { id: string; projectId?: string; clientOperationId?: string; clientContext?: { nodeId?: string } }>(
    node: { id: string; metadata?: { taskId?: string; taskClientOperationId?: string } },
    projectId: string,
    tasks: readonly T[],
) {
    const candidates = tasks.filter((task) => task.projectId === projectId && task.clientContext?.nodeId === node.id);
    if (node.metadata?.taskId) return candidates.find((task) => task.id === node.metadata?.taskId);
    if (node.metadata?.taskClientOperationId) return candidates.find((task) => task.clientOperationId === node.metadata?.taskClientOperationId);
    return candidates[0];
}

export function canvasTaskCanUpdateNode(
    node: { metadata?: { taskId?: string; taskClientOperationId?: string } },
    task: { id: string; clientOperationId?: string },
) {
    if (node.metadata?.taskId && node.metadata.taskId !== task.id) return false;
    return !node.metadata?.taskClientOperationId || !task.clientOperationId || node.metadata.taskClientOperationId === task.clientOperationId;
}

export function canvasGenerationOperationBase(operationId: string, metadata?: Record<string, unknown>) {
    const index = metadata?.batchIndex;
    const count = metadata?.batchCount;
    return typeof index === "number" && typeof count === "number" && count > 1 && operationId.endsWith(`:${index}`)
        ? operationId.slice(0, -String(index).length - 1)
        : operationId;
}

type RecoveryNode = {
    id: string;
    metadata?: {
        status?: "idle" | "success" | "loading" | "error";
        content?: string;
        taskId?: string;
        taskClientOperationId?: string;
        generatedFromNodeId?: string;
        errorDetails?: string;
    };
};
type RecoverySourceTask = {
    id: string; projectId?: string; type: string; status: string; clientOperationId?: string;
    clientContext?: { nodeId?: string; sourceNodeId?: string };
    inputJson?: string; createdAt?: string; error?: string;
};

/** Config nodes own a task group, never a child's task identity. */
export function canvasConfigGenerationState(source: RecoveryNode, projectId: string, nodes: readonly RecoveryNode[], tasks: readonly RecoverySourceTask[], settledTaskIds: ReadonlySet<string> = new Set()) {
    const sourceTasks = tasks.filter((task) => task.projectId === projectId && task.clientContext?.sourceNodeId === source.id && task.clientContext.nodeId !== source.id)
        .sort((left, right) => (right.createdAt || "").localeCompare(left.createdAt || ""));
    const baseForTask = (task: RecoverySourceTask) => {
        let metadata: Record<string, unknown> | undefined;
        try { metadata = JSON.parse(task.inputJson || "{}").metadata; } catch { /* Legacy source task snapshots can omit inputJson. */ }
        return canvasGenerationOperationBase(task.clientOperationId || "", metadata);
    };
    const operation = source.metadata?.taskClientOperationId || (sourceTasks[0] ? baseForTask(sourceTasks[0]) : "");
    const belongsToOperation = (value?: string) => !operation || value === operation || Boolean(value?.startsWith(`${operation}:`) && /^\d+$/.test(value.slice(operation.length + 1)));
    const group = sourceTasks.filter((task) => belongsToOperation(task.clientOperationId));
    const targetIds = new Set(group.map((task) => task.clientContext?.nodeId));
    const targets = nodes.filter((node) => node.id !== source.id && ((targetIds.has(node.id) && (!node.metadata?.taskClientOperationId || belongsToOperation(node.metadata.taskClientOperationId))) || (node.metadata?.generatedFromNodeId === source.id && belongsToOperation(node.metadata.taskClientOperationId))));
    const succeeded = targets.filter((node) => node.metadata?.status === "success" && Boolean(node.metadata.content));
    const imageGroup = group.some((task) => task.type === "canvas_image");
    if ((imageGroup && succeeded.length) || (targets.length && succeeded.length === targets.length)) return { status: "success" as const, errorDetails: undefined };
    if (group.some((task) => task.status === "queued" || task.status === "running")) return { status: "loading" as const, errorDetails: undefined };
    if (targets.some((node) => node.metadata?.status === "loading") && group.some((task) => task.status === "succeeded" && !settledTaskIds.has(task.id))) return { status: "loading" as const, errorDetails: undefined };
    return { status: "error" as const, errorDetails: targets.find((node) => node.metadata?.errorDetails)?.metadata?.errorDetails || group.find((task) => task.error)?.error || (group.some((task) => task.status === "succeeded") ? "生成结果已保留，画布尚未更新，请重新打开画布恢复。" : "页面刷新后找不到对应任务，请重新生成。") };
}
