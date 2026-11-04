import { aiModelConfigs as transport } from '@host/api/ai-config/management';
import { aiConfigTools as toolsTransport } from '@host/api/ai-config/tools';
import { notifyAiConfigsChanged } from '@host/features/ai-config/catalog/config-events';
import { serializeConfigDraft } from '@host/features/ai-config/editor/config-form-model';
import type { AiModelConfigDto } from '@host/types/ai-config/api';
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
    interfaceType: dto.interface_type,
  };
}

export const aiModelConfigs = {
  ...transport,
  async list(serviceType: ServiceType, offset: number, limit: number, signal?: AbortSignal, createKey?: string) {
    const data = await transport.list(serviceType, offset, limit, signal, createKey);
    return { ...data, items: data.items.map(mapAiModelConfig) };
  },
  async get(id: string) {
    return mapAiModelConfig(await transport.get(id));
  },
  async create(draft: ConfigDraft, key: string) {
    const result = mapAiModelConfig(
      await transport.create({
        ...serializeConfigDraft(draft),
        service_type: draft.serviceType,
      }, key),
    );
    notifyAiConfigsChanged();
    return result;
  },
  async update(existing: AiConfig, draft: ConfigDraft) {
    const { interface_type, ...fields } = serializeConfigDraft(draft);
    const result = mapAiModelConfig(
      await transport.update(existing.id, {
        ...fields,
        // 未切换接口时保留原协议身份及隐藏运行参数。
        ...(interface_type !== existing.interfaceType ? { interface_type } : {}),
        row_version: existing.rowVersion,
      }),
    );
    notifyAiConfigsChanged();
    return result;
  },
  async remove(id: string, rowVersion: string) {
    await transport.remove(id, rowVersion);
    notifyAiConfigsChanged();
  },
  async setEnabled(id: string, rowVersion: string, enabled: boolean) {
    const result = await transport.update(id, { row_version: rowVersion, enabled: enabled ? 1 : 0 });
    notifyAiConfigsChanged();
    return mapAiModelConfig(result);
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
    const body: ModelTestRequestDto = {
      config_id: config.id,
      prompt: config.serviceType === 'text' ? '请只回复 OK。' : 'A simple blue circle on a white background.',
      clientOperationId: operationId,
    };
    return toolsTransport.startTest(body, operationId);
  },
};
