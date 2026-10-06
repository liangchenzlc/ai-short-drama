import { prepareBackendGenerationTask, type BackendGenerationResult, type runBackendGenerationTask } from "@/services/api/generation-task";
import { http, ApiError } from "@/services/api/request";
import { assertUserScope, captureUserScope } from "@/lib/user-scope-guard";
import { modelOptionName, resolveModelChannel } from "@/stores/use-config-store";

type Options = Parameters<typeof runBackendGenerationTask>[0];
export type HostModelTestRead = {
    id: string;
    status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
    result?: BackendGenerationResult;
    error?: string;
    errorCode?: string;
    canCancel: boolean;
};

const pending = new Map<string, { id: string; body: Record<string, unknown>; scope: ReturnType<typeof captureUserScope> }>();
const privateConfigFields = new Set(["baseUrl", "apiKey", "secretKey", "headers", "credentialRef", "channelId", "interfaceType", "apiFormat"]);

function check(options: Options, scope: ReturnType<typeof captureUserScope>) {
    if (options.signal?.aborted) throw new DOMException("测试观察已取消", "AbortError");
    try { assertUserScope(scope); }
    catch (error) {
        for (const [key, item] of pending) if (item.scope.userScope === scope.userScope && item.scope.epoch === scope.epoch) pending.delete(key);
        throw error;
    }
}

function confirmedModelTest(value: HostModelTestRead, expectedId?: string): HostModelTestRead {
    if (!value || typeof value.id !== "string" || !/^[1-9]\d*$/.test(value.id) || (expectedId && value.id !== expectedId) || !["queued", "running", "succeeded", "failed", "cancelled"].includes(value.status)) {
        throw new Error("模型测试回执无效，请核对原任务后再试");
    }
    return value;
}

/** 模型测试只创建本人独立任务；离开观察不会取消已受理的供应商执行。 */
export const runHostModelTest: typeof runBackendGenerationTask = async options => {
    const scope = options.expectedScope || captureUserScope();
    check(options, scope);
    const channel = structuredClone(resolveModelChannel(options.config, options.config.model));
    const prepared = await prepareBackendGenerationTask(options);
    check(options, scope);
    const input = prepared.input as { config: Record<string, unknown>; textOptions?: Record<string, unknown> };
    const fields = {
        channel,
        model: modelOptionName(options.config.model),
        mode: options.mode,
        prompt: options.prompt,
        config: Object.fromEntries(Object.entries(input.config).filter(([key]) => !privateConfigFields.has(key))),
        textOptions: input.textOptions || { stream: false, thinking: false },
    };
    for (const [key, item] of pending) if (item.scope.userScope !== scope.userScope || item.scope.epoch !== scope.epoch) pending.delete(key);
    const identity = JSON.stringify([scope.userScope, scope.epoch, fields]);
    let admission = pending.get(identity);
    if (!admission) {
        const id = crypto.randomUUID();
        admission = { id, body: { ...fields, clientOperationId: id }, scope };
        pending.set(identity, admission);
    }
    let task: HostModelTestRead | undefined;
    for (let attempt = 0; attempt < 2; attempt++) {
        check(options, scope);
        try {
            task = confirmedModelTest(await http.post<HostModelTestRead>("/model-tests", admission.body, { expectedScope: scope, signal: options.signal, headers: { "Idempotency-Key": admission.id } }));
            check(options, scope);
            pending.delete(identity);
            break;
        } catch (error) {
            check(options, scope);
            const uncertain = error instanceof ApiError && (error.status === undefined || error.status === 408 || error.status >= 500);
            if (!uncertain) pending.delete(identity);
            if (!uncertain || attempt === 1) throw error;
        }
    }
    if (!task) throw new Error("模型测试受理尚未确认，请稍后核对原任务");
    while (task.status === "queued" || task.status === "running") {
        await new Promise(resolve => setTimeout(resolve, 1000));
        check(options, scope);
        task = confirmedModelTest(await http.get<HostModelTestRead>(`/model-tests/${encodeURIComponent(task.id)}`, { expectedScope: scope, signal: options.signal }), task.id);
        check(options, scope);
    }
    if (task.status !== "succeeded" || !task.result) throw new Error(task.error || (task.status === "cancelled" ? "模型测试已取消" : "模型测试失败"));
    return task.result;
};
