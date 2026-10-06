import { defaultConfig, normalizeConfigSnapshot, type AiConfig, type ModelChannel } from "@/stores/use-config-store";

export type HostModelConfig = {
    row_version: string;
    preferences: Partial<AiConfig>;
    channels?: ModelChannel[];
    models: Array<{ id: string; name: string; model_key: string; provider: string; service_type: "text" | "image" | "video" | "audio"; enabled: boolean; has_api_key: boolean }>;
};

const preferenceKeys = ["model", "imageModel", "videoModel", "textModel", "audioModel", "assistantModel", "audioVoice", "audioFormat", "audioSpeed", "audioPitch", "audioVolume", "audioInstructions", "videoSeconds", "vquality", "videoGenerateAudio", "videoWatermark", "videoArkPrivateAssetUpload", "systemPrompt", "quality", "size", "transparentBackground", "count", "canvasImageCount", "taskWorkflowProvider"] as const;
let catalogSnapshot = "";
let managedChannels = new Map<string, string>();

export function sourceModelConfig(value: HostModelConfig): AiConfig {
    const channels: ModelChannel[] = value.models.map(model => ({
        id: `host-${model.id}`, name: model.name, baseUrl: `/api/v1/canvas-runtime/ai/models/${model.id}`,
        apiKey: "", apiFormat: "openai", enabled: model.enabled, scope: "system",
        credentialRef: `host:${model.id}`, hasApiKey: model.has_api_key,
        models: [model.model_key],
        modelProfiles: [{ model: model.model_key, displayName: model.name, capability: model.service_type, logicalModelId: model.id }],
    }));
    const config = normalizeConfigSnapshot({ config: { ...defaultConfig, ...value.preferences, channels: [...channels, ...(value.channels || [])] } }).config;
    catalogSnapshot = JSON.stringify(config.channels.filter(channel => channel.id.startsWith("host-")));
    managedChannels = new Map(config.channels.filter(channel => channel.id === "beefapi").map(channel => [channel.id, JSON.stringify(channel)]));
    return config;
}

export function modelPreferences(config: AiConfig) {
    if (JSON.stringify(config.channels.filter(channel => channel.id.startsWith("host-"))) !== catalogSnapshot) {
        throw new Error("模型目录变更尚未保存，请在当前项目的模型配置中维护后重新加载画布");
    }
    return Object.fromEntries(preferenceKeys.filter(key => config[key] !== undefined).map(key => [key, config[key]]));
}

export function customModelChannels(config: AiConfig) {
    return config.channels.filter(channel => channel.scope !== "system" && !channel.id.startsWith("host-")).flatMap(channel => {
        if (channel.id !== "beefapi") return [channel];
        const snapshot = managedChannels.get(channel.id);
        if (!snapshot) throw new Error("内置 BeefAPI 目录尚未加载，请重新加载画布");
        const managed: ModelChannel = JSON.parse(snapshot);
        if (channel.enabled === managed.enabled && JSON.stringify(channel.headers || []) === JSON.stringify(managed.headers || [])) return [];
        return [{ ...managed, enabled: channel.enabled, headers: channel.headers || [] }];
    });
}
