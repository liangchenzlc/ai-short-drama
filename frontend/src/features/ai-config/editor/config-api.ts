import { aiModelConfigs as transport } from '@host/api/ai-config/management';
import { aiConfigTools as toolsTransport } from '@host/api/ai-config/tools';
import { ApiError } from '@host/api/http';
import { notifyAiConfigsChanged } from '@host/features/ai-config/catalog/config-events';
import { runtimeModelDiscoveryBody, serializeConfigDraft } from '@host/features/ai-config/editor/config-form-model';
import type { AiModelConfigDto, RuntimeModelDiscoveryRequestDto } from '@host/types/ai-config/api';
import type { AiConfig, ConfigDraft, ServiceType } from '@host/types/ai-config/editor/config-model';
import type { ModelTestRequestDto } from '@host/types/ai-config/tools';
function mapAiModelConfig(dto: AiModelConfigDto): AiConfig {
  return {
    id: dto.id,
    name: dto.name,
    provider: dto.provider,
    serviceType: dto.service_type,
    baseUrl: dto.base_url,
    modelKey: dto.model_key,
    enabled: dto.enabled === 1,
    isDefault: dto.is_default === 1,
    hasApiKey: dto.has_api_key,
    rowVersion: dto.row_version,
    hasSecretKey: dto.has_secret_key ?? false,
    headers: dto.headers ?? [],
    credentialSource: dto.credential_source ?? 'manual',
    runtimeProfile: dto.runtime_profile ?? null,
  };
}

export const aiModelConfigs = {
  ...transport,
  async discoverRuntimeModels(draft: ConfigDraft, existing: AiConfig | null, signal?: AbortSignal) {
    let body: RuntimeModelDiscoveryRequestDto;
    try {
      body = runtimeModelDiscoveryBody(draft, existing);
    } catch (cause) {
      throw new ApiError(cause instanceof Error ? cause.message : '请检查模型目录参数。', 'MODEL_CONFIG_DRAFT_INVALID');
    }
    const data = await transport.discoverRuntimeModels(body, signal);
    return { items: data.models, truncated: false };
  },
  async list(serviceType: ServiceType, offset: number, limit: number, signal?: AbortSignal) {
    const data = await transport.list(serviceType, offset, limit, signal);
    return { ...data, items: data.items.map(mapAiModelConfig) };
  },
  async get(id: string) {
    return mapAiModelConfig(await transport.get(id));
  },
  async create(draft: ConfigDraft) {
    const result = mapAiModelConfig(
      await transport.create({
        ...serializeConfigDraft(draft),
        service_type: draft.serviceType,
      }),
    );
    notifyAiConfigsChanged();
    return result;
  },
  async update(id: string, rowVersion: string, draft: ConfigDraft) {
    const result = mapAiModelConfig(
      await transport.update(id, {
        ...serializeConfigDraft(draft),
        row_version: rowVersion,
      }),
    );
    notifyAiConfigsChanged();
    return result;
  },
  async remove(id: string, rowVersion: string) {
    await transport.remove(id, rowVersion);
    notifyAiConfigsChanged();
  },
  async setDefault(id: string, rowVersion: string) {
    const result = mapAiModelConfig(await transport.setDefault(id, rowVersion));
    notifyAiConfigsChanged();
    return result;
  },
};

export const aiConfigTools = {
  ...toolsTransport,
  async startTest(config: AiConfig, operationId: string) {
    const profile = config.runtimeProfile;
    const protocol =
      profile?.protocol ??
      (
        {
          text: 'chat-completion',
          image: 'openai-image',
          video: 'newapi',
          audio: 'openai-audio',
        } as const
      )[config.serviceType];
    const body: ModelTestRequestDto = {
      channel: {
        id: `host-${config.id}`,
        name: config.name,
        baseUrl: `/api/v1/canvas-runtime/ai/models/${config.id}`,
        apiFormat: profile?.api_format ?? 'openai',
        scope: 'system',
        models: [config.modelKey],
        modelProfiles: [{ model: config.modelKey, capability: config.serviceType, protocol }],
      },
      model: config.modelKey,
      mode: config.serviceType,
      prompt: config.serviceType === 'text' ? '请只回复 OK。' : 'A simple blue circle on a white background.',
      config: {},
      textOptions: { stream: false, thinking: false },
      clientOperationId: operationId,
    };
    return toolsTransport.startTest(body, operationId);
  },
};
