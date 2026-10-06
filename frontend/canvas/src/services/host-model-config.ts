import { defaultConfig, normalizeConfigSnapshot, type AiConfig, type ModelChannel } from "@/stores/use-config-store";

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
};
export type HostModelConfig = {
    row_version: string;
    preferences: Partial<AiConfig>;
    models: Array<{
        id: string; name: string; model_key: string; provider: string;
        service_type: "text" | "image" | "video" | "audio";
        enabled: boolean; has_api_key: boolean; is_default?: boolean;
        runtime_profile?: HostRuntimeProfile | null;
        credential_source?: "manual" | "beefapi";
        has_secret_key?: boolean;
        headers?: Array<{ name: string; has_value: boolean }>;
        selection_aliases?: string[];
    }>;
};

const preferenceKeys = ["model", "imageModel", "videoModel", "textModel", "audioModel", "assistantModel", "audioVoice", "audioFormat", "audioSpeed", "audioPitch", "audioVolume", "audioInstructions", "videoSeconds", "vquality", "videoGenerateAudio", "videoWatermark", "videoArkPrivateAssetUpload", "systemPrompt", "quality", "size", "transparentBackground", "count", "canvasImageCount", "taskWorkflowProvider"] as const;
/** 宿主模型是唯一目录；源渠道标识只用于恢复旧选择，不能作为可编辑目录回写。 */
export function sourceModelConfig(value: HostModelConfig): AiConfig {
    const channels: ModelChannel[] = value.models.map(model => {
        const runtime = model.runtime_profile;
        return {
            id: `host-${model.id}`, name: model.name, baseUrl: `/api/v1/canvas-runtime/ai/models/${model.id}`,
            apiKey: "", secretKey: "", apiFormat: runtime?.api_format || "openai", enabled: model.enabled,
            scope: "system", credentialRef: `host:${model.id}`, hasApiKey: model.has_api_key,
            credentialSource: model.credential_source,
            hasSecretKey: model.has_secret_key, selectionAliases: model.selection_aliases || [],
            headers: model.headers?.map(header => ({ name: header.name, value: "" })),
            referenceAssetOrigin: runtime?.reference_asset_origin, concurrencyLimit: runtime?.concurrency_limit,
            models: [model.model_key],
            modelProfiles: [{ model: model.model_key, displayName: model.name, capability: model.service_type,
                logicalModelId: model.id, protocol: runtime?.protocol,
                capabilityConfig: runtime?.capability_config, defaultOptions: runtime?.default_options,
                logicalCapabilitySpec: runtime?.logical_capability_spec,
                logicalCapabilityProfiles: runtime?.logical_capability_profiles,
                videoCapabilitiesVersion: runtime?.video_capabilities_version,
            }],
        };
    });
    const defaults: Partial<AiConfig> = {};
    for (const capability of ["text", "image", "video", "audio"] as const) {
        const model = value.models.find(item => item.service_type === capability && item.enabled && item.is_default);
        defaults[`${capability}Model`] = model ? `host-${model.id}::${model.model_key}` : "";
    }
    const preferences = { ...defaults, ...value.preferences };
    return normalizeConfigSnapshot({ config: { ...defaultConfig, ...preferences,
        model: preferences.model ?? preferences.imageModel ?? preferences.textModel ?? "",
        hostModelDirectory: true, channels,
    } }).config;
}

export function modelPreferences(config: AiConfig) {
    return Object.fromEntries(preferenceKeys.filter(key => config[key] !== undefined).map(key => [key, config[key]]));
}
