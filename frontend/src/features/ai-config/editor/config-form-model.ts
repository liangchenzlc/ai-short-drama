import type { AiModelConfigCreateDto } from '@host/types/ai-config/api';
import type { AiConfig, ConfigDraft, ServiceType } from '@host/types/ai-config/editor/config-model';

export function createConfigDraft(existing: AiConfig | null, serviceType: ServiceType): ConfigDraft {
  return {
    name: existing?.name ?? '',
    provider: existing?.provider ?? '',
    serviceType,
    baseUrl: existing?.baseUrl ?? '',
    modelKey: existing?.modelKey ?? '',
    enabled: existing?.enabled ?? true,
    apiKey: '',
    clearApiKey: false,
    secretKey: '',
    clearSecretKey: false,
    runtime: {
      apiFormat: existing?.runtimeProfile?.api_format ?? 'auto',
      protocol: existing?.interfaceType ?? 'auto',
    },
  };
}

export function serializeConfigDraft(draft: ConfigDraft): Omit<AiModelConfigCreateDto, 'service_type'> {
  return {
    name: draft.name.trim() || draft.modelKey.trim(),
    provider: draft.provider.trim(),
    model_key: draft.modelKey.trim(),
    base_url: draft.baseUrl.trim(),
    enabled: draft.enabled ? 1 : 0,
    interface_type: draft.runtime.protocol,
    ...(draft.clearApiKey ? { apikey: null } : draft.apiKey ? { apikey: draft.apiKey } : {}),
    ...(draft.clearSecretKey ? { secret_key: null } : draft.secretKey ? { secret_key: draft.secretKey } : {}),
  };
}

export function safeCanvasReturn(value: string | null): string | null {
  if (!value || value.includes('\\') || /[\x00-\x20]/.test(value)) return null;
  const rawPath = value.split(/[?#]/, 1)[0];
  if (!/^\/canvas-app\/canvas\/[A-Za-z0-9_-]{1,64}$/.test(rawPath) || /%(?:2f|5c|2e)/i.test(rawPath)) return null;
  const url = new URL(value, 'https://host.invalid');
  return url.origin === 'https://host.invalid' && url.pathname === rawPath
    ? `${url.pathname}${url.search}${url.hash}`
    : null;
}
