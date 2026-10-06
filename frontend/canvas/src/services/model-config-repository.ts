import { getLocalModelConfig, saveLocalModelConfig, type LocalModelConfigPayload } from "@/services/api/workspace";
import { normalizeConfigSnapshot, useConfigStore, type AiConfig } from "@/stores/use-config-store";
import { reconcileHostModelConfigAck } from "@/services/host-model-config-ack";
import { ApiError } from "@/services/api/request";
import { assertUserScope, captureUserScope } from "@/lib/user-scope-guard";

export type ModelConfigPersistenceState = {
    status: "idle" | "hydrating" | "saving" | "saved" | "error";
    revision: string;
    dirty: boolean;
    error: string;
};

type ModelConfigRepositoryDependencies = {
    read: () => Promise<LocalModelConfigPayload>;
    write: (config: AiConfig, expectedRevision: string) => Promise<{ saved: boolean; revision: string; config?: AiConfig }>;
    acknowledge?: (submitted: AiConfig, current: AiConfig, durable: AiConfig) => AiConfig;
    applyRefresh?: (config: AiConfig) => void;
};

export function createModelConfigRepository(dependencies: ModelConfigRepositoryDependencies) {
    let state: ModelConfigPersistenceState = { status: "idle", revision: "0", dirty: false, error: "" };
    let hydrated = false;
    let latestConfig: AiConfig | null = null;
    let generation = 0;
    let drainPromise: Promise<void> | null = null;
    let conflictRevision: string | null = null;
    let resolveHydration: (() => void) | null = null;
    const hydrationBarrier = new Promise<void>((resolve) => {
        resolveHydration = resolve;
    });
    const listeners = new Set<(next: ModelConfigPersistenceState) => void>();

    const publish = (patch: Partial<ModelConfigPersistenceState>) => {
        state = { ...state, ...patch };
        listeners.forEach((listener) => listener(state));
    };

    const hydrate = async () => {
        publish({ status: "hydrating", error: "" });
        try {
            const result = await dependencies.read();
            state = { status: "idle", revision: result.revision, dirty: Boolean(latestConfig), error: "" };
            if (conflictRevision !== result.revision) conflictRevision = null;
            hydrated = true;
            resolveHydration?.();
            resolveHydration = null;
            if (latestConfig) void scheduleDrain();
            return result;
        } catch (error) {
            publish({ status: "error", error: error instanceof Error ? error.message : "读取模型配置失败" });
            throw error;

        }
    };

    const runDrain = async () => {
        while (hydrated && latestConfig && state.dirty) {
            const config = latestConfig;
            const savingGeneration = generation;
            publish({ status: "saving", error: "" });
            try {
                const result = await dependencies.write(config, state.revision);
                if (result.config && latestConfig && dependencies.acknowledge) latestConfig = dependencies.acknowledge(config, latestConfig, result.config);
                state = { status: "saved", revision: result.revision, dirty: generation !== savingGeneration, error: "" };
            } catch (error) {
                if (error instanceof ApiError && error.status === 409) conflictRevision = state.revision;
                publish({ status: "error", dirty: true, error: error instanceof Error ? error.message : "保存模型配置失败" });
                return;
            }
        }
        listeners.forEach((listener) => listener(state));
    };

    const scheduleDrain = (): Promise<void> => {
        if (!hydrated) return hydrationBarrier.then(scheduleDrain);
        if (conflictRevision === state.revision) return Promise.resolve();
        if (!drainPromise) {
            drainPromise = runDrain().finally(() => {
                drainPromise = null;
            });
        }
        return drainPromise;
    };

    const commit = (config: AiConfig) => {
        latestConfig = config;
        generation += 1;
        publish({ dirty: true });
        return scheduleDrain();
    };

    const refresh = async (merge: (durable: AiConfig) => AiConfig = config => config, assertActive: () => void = () => {}) => {
        const observedGeneration = generation;
        assertActive();
        if (drainPromise) await drainPromise;
        assertActive();
        const assertClean = () => {
            if (!hydrated || state.dirty || conflictRevision !== null || generation !== observedGeneration) {
                throw new Error("模型配置仍有未保存输入或冲突，请先处理草稿后更新企业目录");
            }
        };
        assertClean();
        const result = await dependencies.read();
        assertActive();
        assertClean();
        const config = merge(result.config);
        dependencies.applyRefresh?.(config);
        latestConfig = config;
        generation += 1;
        publish({ status: "saved", revision: result.revision, dirty: false, error: "" });
        return { ...result, config };
    };

    return {
        hydrate,
        refresh,
        commit,
        flush: scheduleDrain,
        getState: () => state,
        subscribe: (listener: (next: ModelConfigPersistenceState) => void) => {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
    };
}

function omitManagedBeefAPISecrets(config: AiConfig): AiConfig {
    return {
        ...config,
        channels: config.channels.map((channel) => {
            if (channel.id !== "beefapi" || !channel.pinned) return channel;
            return { ...channel, apiKey: "", secretKey: "" };
        }),
    };
}

let applyingAcknowledgement = false;
function applyAcknowledgedConfig(config: AiConfig) {
    const normalized = normalizeConfigSnapshot({ config }).config;
    applyingAcknowledgement = true;
    try { useConfigStore.getState().replaceConfig(normalized); }
    finally { applyingAcknowledgement = false; }
    return normalized;
}
const repository = createModelConfigRepository({
    read: getLocalModelConfig,
    write: (config, expectedRevision) => saveLocalModelConfig(omitManagedBeefAPISecrets(config), expectedRevision),
    acknowledge: (submitted, current, durable) => applyAcknowledgedConfig(reconcileHostModelConfigAck(submitted, current, durable)),
    applyRefresh: applyAcknowledgedConfig,
});

export function modelConfigAcknowledgementInProgress() { return applyingAcknowledgement; }

export const hydrateModelConfig = repository.hydrate;
export function refreshModelConfig(merge: (durable: AiConfig) => AiConfig) {
    const expected = captureUserScope();
    return repository.refresh(merge, () => assertUserScope(expected));
}
export const commitModelConfig = repository.commit;
export const flushModelConfig = repository.flush;
export const getModelConfigPersistenceState = repository.getState;
export const subscribeModelConfigPersistence = repository.subscribe;
