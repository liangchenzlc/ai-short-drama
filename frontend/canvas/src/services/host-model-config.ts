import { defaultConfig, normalizeConfigSnapshot, type AiConfig, type ModelChannel, type RunningHubWorkflow, type WorkflowFieldMapping } from "@/stores/use-config-store";

type ModelProfile = NonNullable<ModelChannel["modelProfiles"]>[number];
export type HostRuntimeProfile = {
    version: number;
    api_format: ModelChannel["apiFormat"];
    protocol: string;
    reference_asset_origin?: string;
    capability_config?: ModelProfile["capabilityConfig"];
    default_options?: ModelProfile["defaultOptions"];
    logical_capability_spec?: ModelProfile["logicalCapabilitySpec"];
    logical_capability_profiles?: ModelProfile["logicalCapabilityProfiles"];
    video_capabilities_version?: string;
    concurrency_limit?: number;
    runninghub_workflow?: { kind: "workflow" | "app"; workflow_id?: string; webapp_id?: string; title?: string; fields?: WorkflowFieldMapping[]; workflow_json?: Record<string, unknown> };
};
export type HostModelConfig = {
    row_version: string;
    preferences: Partial<AiConfig>;
    models: Array<{
        id: string; name: string; model_key: string; provider: string;
        service_type: "text" | "image" | "video" | "audio";
        enabled: boolean; has_api_key: boolean; is_default?: boolean;
        runtime_profile?: HostRuntimeProfile | null;
        default_capability_config?: ModelProfile["capabilityConfig"] | null;
        credential_source?: "manual";
        has_secret_key?: boolean;
        headers?: Array<{ name: string; has_value: boolean }>;
    }>;
};

const preferenceKeys = ["model", "imageModel", "videoModel", "textModel", "audioModel", "assistantModel", "audioVoice", "audioFormat", "audioSpeed", "audioPitch", "audioVolume", "audioInstructions", "videoSeconds", "vquality", "videoGenerateAudio", "videoWatermark", "videoArkPrivateAssetUpload", "quality", "size", "transparentBackground", "count", "canvasImageCount", "taskWorkflowProvider"] as const;
/** 宿主模型是唯一目录，画布只回写个人偏好。 */
export function sourceModelConfig(value: HostModelConfig): AiConfig {
    const isWorkflow = (model: HostModelConfig["models"][number]) => /^runninghub-workflow-(image|video|audio)$/.test(model.runtime_profile?.protocol || "");
    const workflows: RunningHubWorkflow[] = value.models.filter(isWorkflow).map(model => {
        const saved = model.runtime_profile?.runninghub_workflow;
        const kind = saved?.kind === "app" ? "app" : "workflow";
        return { kind, capability: model.service_type as RunningHubWorkflow["capability"],
            logicalModelId: model.id, hasApiKey: model.has_api_key && model.enabled,
            workflowId: (kind === "app" ? saved?.webapp_id || saved?.workflow_id : saved?.workflow_id) || model.model_key,
            webappId: kind === "app" ? saved?.webapp_id || saved?.workflow_id || model.model_key : "",
            title: saved?.title || model.name, fields: saved?.fields || [], workflowJson: saved?.workflow_json || {} };
    });
    const channels: ModelChannel[] = value.models.filter(model => !isWorkflow(model)).map(model => {
        const runtime = model.runtime_profile;
        return {
            id: `host-${model.id}`, name: model.name, baseUrl: `/api/v1/canvas-runtime/ai/models/${model.id}`,
            apiKey: "", secretKey: "", apiFormat: runtime?.api_format || "openai", enabled: model.enabled,
            scope: "system", credentialRef: `host:${model.id}`, hasApiKey: model.has_api_key,
            credentialSource: model.credential_source,
            hasSecretKey: model.has_secret_key,
            headers: model.headers?.map(header => ({ name: header.name, value: "" })),
            referenceAssetOrigin: runtime?.reference_asset_origin, concurrencyLimit: runtime?.concurrency_limit,
            models: [model.model_key],
            modelProfiles: [{ model: model.model_key, displayName: model.name, capability: model.service_type,
                logicalModelId: model.id, protocol: runtime?.protocol,
                capabilityConfig: runtime?.capability_config ?? model.default_capability_config ?? undefined, defaultOptions: runtime?.default_options,
                logicalCapabilitySpec: runtime?.logical_capability_spec,
                logicalCapabilityProfiles: runtime?.logical_capability_profiles,
                videoCapabilitiesVersion: runtime?.video_capabilities_version,
            }],
        };
    });
    const defaults: Partial<AiConfig> = {};
    for (const capability of ["text", "image", "video", "audio"] as const) {
        const model = value.models.find(item => !isWorkflow(item) && item.service_type === capability && item.enabled && item.is_default);
        defaults[`${capability}Model`] = model ? `host-${model.id}::${model.model_key}` : "";
    }
    const preferences = { ...defaults, ...value.preferences };
    return normalizeConfigSnapshot({ config: { ...defaultConfig, ...preferences,
        model: preferences.model ?? preferences.imageModel ?? preferences.textModel ?? "",
        hostModelDirectory: true, channels,
        runningHub: { ...defaultConfig.runningHub, enabled: workflows.some(item => item.hasApiKey),
            baseUrl: "", apiKey: "", walletApiKey: "", uploadApiKey: "", workflows,
            workflowId: workflows[0]?.workflowId || "", selectedKind: workflows[0]?.kind || "workflow",
            selectedLogicalModelId: workflows[0]?.logicalModelId, capability: workflows[0]?.capability || "image" },
    } }).config;
}

export function modelPreferences(config: AiConfig) {
    return Object.fromEntries(preferenceKeys.filter(key => config[key] !== undefined).map(key => [key, config[key]]));
}
