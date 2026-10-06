import type { AiModelConfigCreateDto, AiRuntimeProfileDto, ConfigJson, ConfigJsonObject } from '../../api/types/ai-model-configs';
import type { AiConfig, ConfigDraft, RuntimeDraft, ServiceType } from './config-model';

const secretFields = new Set(['apikey', 'secretkey', 'authorization', 'cookie', 'password', 'token']);
const blockedHeaders = new Set(['authorization', 'proxy-authorization', 'proxy-authenticate', 'cookie', 'set-cookie', 'host', 'content-length', 'content-type', 'accept', 'connection', 'proxy-connection', 'keep-alive', 'transfer-encoding', 'te', 'trailer', 'upgrade', 'forwarded', 'x-goog-api-key']);
const jsonText = (value: ConfigJson | undefined) => value == null ? '' : JSON.stringify(value, null, 2);

export function runtimeDraft(profile: AiRuntimeProfileDto | null | undefined): RuntimeDraft {
  return { enabled: !!profile, apiFormat: profile?.api_format ?? 'openai', protocol: profile?.protocol ?? '',
    referenceAssetOrigin: profile?.reference_asset_origin ?? '', capabilityConfig: jsonText(profile?.capability_config),
    defaultOptions: jsonText(profile?.default_options), logicalCapabilitySpec: jsonText(profile?.logical_capability_spec),
    logicalCapabilityProfiles: jsonText(profile?.logical_capability_profiles), videoCapabilitiesVersion: profile?.video_capabilities_version ?? '',
    concurrencyLimit: profile?.concurrency_limit == null ? '' : String(profile.concurrency_limit) };
}

export function createConfigDraft(existing: AiConfig | null, serviceType: ServiceType): ConfigDraft {
  return { name: existing?.name ?? '', provider: existing?.provider ?? '', serviceType,
    baseUrl: existing?.baseUrl ?? '', modelKey: existing?.modelKey ?? '', enabled: existing?.enabled ?? true,
    apiKey: '', clearApiKey: false, secretKey: '', clearSecretKey: false,
    headers: (existing?.headers ?? []).map(header => ({ name: header.name, value: '', hasValue: header.has_value })),
    headersChanged: false, runtime: runtimeDraft(existing?.runtimeProfile) };
}

function validatePublicJson(value: ConfigJson, depth = 0): void {
  if (depth > 24) throw new Error('高级参数嵌套过深，请简化后保存。');
  if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('高级参数只能使用有限数字。');
  if (!value || typeof value !== 'object') return;
  for (const [key, item] of Object.entries(value)) {
    if (secretFields.has(key.toLowerCase().replace(/[^a-z]/g, ''))) throw new Error('密钥与认证信息请填写在凭据区域，不要放入高级 JSON。');
    validatePublicJson(item, depth + 1);
  }
}

function parseJson(value: string, label: string, array = false): ConfigJsonObject | ConfigJsonObject[] | undefined {
  if (!value.trim()) return undefined;
  if (new TextEncoder().encode(value).length > 262144) throw new Error(`${label}不能超过 256 KiB。`);
  let parsed: ConfigJson;
  try { parsed = JSON.parse(value) as ConfigJson; }
  catch { throw new Error(`${label}不是有效 JSON，请检查括号和引号。`); }
  const object = (item: ConfigJson) => item !== null && typeof item === 'object' && !Array.isArray(item);
  if (array ? !Array.isArray(parsed) || !parsed.every(object) : !object(parsed)) {
    throw new Error(`${label}必须是${array ? 'JSON 对象数组' : 'JSON 对象'}。`);
  }
  validatePublicJson(parsed);
  return parsed as ConfigJsonObject | ConfigJsonObject[];
}

export function parseRuntimeDraft(draft: RuntimeDraft): AiRuntimeProfileDto | null {
  if (!draft.enabled) return null;
  if (!draft.protocol.trim()) throw new Error('请选择或填写请求协议。');
  const limit = draft.concurrencyLimit.trim();
  if (limit && (!/^[1-9]\d*$/.test(limit) || Number(limit) > 1024)) throw new Error('并发上限必须是 1–1024 的整数。');
  const origin = draft.referenceAssetOrigin.trim();
  if (origin) {
    let url: URL;
    try { url = new URL(origin); } catch { throw new Error('参考资源源站必须是完整的 HTTP(S) 地址。'); }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) {
      throw new Error('参考资源源站只能包含 HTTP(S) 协议、域名和端口。');
    }
  }
  const capability = parseJson(draft.capabilityConfig, '能力配置') as ConfigJsonObject | undefined;
  const defaults = parseJson(draft.defaultOptions, '默认参数') as ConfigJsonObject | undefined;
  const spec = parseJson(draft.logicalCapabilitySpec, '逻辑能力规范') as ConfigJsonObject | undefined;
  const profiles = parseJson(draft.logicalCapabilityProfiles, '逻辑能力配置集', true) as ConfigJsonObject[] | undefined;
  return { version: 1, api_format: draft.apiFormat, protocol: draft.protocol.trim(),
    ...(origin ? { reference_asset_origin: origin } : {}), ...(capability ? { capability_config: capability } : {}),
    ...(defaults ? { default_options: defaults } : {}), ...(spec ? { logical_capability_spec: spec } : {}),
    ...(profiles ? { logical_capability_profiles: profiles } : {}),
    ...(draft.videoCapabilitiesVersion.trim() ? { video_capabilities_version: draft.videoCapabilitiesVersion.trim() } : {}),
    ...(limit ? { concurrency_limit: Number(limit) } : {}) };
}

export function serializeConfigDraft(draft: ConfigDraft): Omit<AiModelConfigCreateDto, 'service_type'> {
  const names = new Set<string>();
  let total = 0;
  if (draft.headers.length > 32) throw new Error('自定义请求头最多支持 32 项。');
  const headers = draft.headers.filter(header => header.name.trim() || header.value).map(header => {
    const name = header.name.trim();
    const lower = name.toLowerCase();
    if (name.length > 128 || !/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name) || blockedHeaders.has(lower) || lower.startsWith('x-canvas-') || lower.startsWith('x-forwarded-')) {
      throw new Error('请求头名称无效或属于受保护的认证、传输字段。');
    }
    if (names.has(lower)) throw new Error('请求头名称不能重复（不区分大小写）。');
    names.add(lower);
    const bytes = new TextEncoder().encode(header.value).length;
    total += name.length + bytes;
    if (/[\x00-\x08\x0a-\x1f\x7f]/.test(header.value) || bytes > 4096 || total > 16384) throw new Error('请求头值不能含换行；每项最多 4 KiB，合计最多 16 KiB。');
    return { name, value: header.value };
  });
  return { name: draft.name.trim(), provider: draft.provider.trim(), model_key: draft.modelKey.trim(),
    base_url: draft.baseUrl.trim(), enabled: draft.enabled ? 1 : 0, runtime_profile: parseRuntimeDraft(draft.runtime),
    ...(draft.clearApiKey ? { apikey: null } : draft.apiKey ? { apikey: draft.apiKey } : {}),
    ...(draft.clearSecretKey ? { secret_key: null } : draft.secretKey ? { secret_key: draft.secretKey } : {}),
    ...(draft.headersChanged ? { headers } : {}) };
}

export function usesRuntimeModelDiscovery(draft: ConfigDraft): boolean {
  return draft.runtime.enabled || draft.headersChanged || draft.headers.length > 0;
}

export function runtimeModelDiscoveryBody(draft: ConfigDraft, existing: AiConfig | null) {
  const fields = serializeConfigDraft({ ...draft, headersChanged: true });
  const sameAddress = existing?.baseUrl.trim().replace(/\/+$/, '') === fields.base_url.replace(/\/+$/, '');
  if (existing && !sameAddress && draft.headers.some(header => header.hasValue && !header.value)) {
    throw new Error('服务地址已修改，请重新填写该地址的请求头值或删除不再使用的请求头。');
  }
  return { baseUrl: fields.base_url, apiFormat: draft.runtime.enabled ? draft.runtime.apiFormat : 'openai',
    ...(existing && sameAddress ? { channelId: `host-${existing.id}`, credentialRef: `host:${existing.id}` } : {}),
    ...(fields.apikey !== undefined ? { apiKey: fields.apikey } : {}), headers: fields.headers };
}

export function safeCanvasReturn(value: string | null): string | null {
  if (!value || value.includes('\\') || /[\x00-\x20]/.test(value)) return null;
  const rawPath = value.split(/[?#]/, 1)[0];
  if (!/^\/canvas-app\/canvas\/[A-Za-z0-9_-]{1,64}$/.test(rawPath) || /%(?:2f|5c|2e)/i.test(rawPath)) return null;
  const url = new URL(value, 'https://host.invalid');
  return url.origin === 'https://host.invalid' && url.pathname === rawPath ? `${url.pathname}${url.search}${url.hash}` : null;
}

export function beefAPITestOrigin(mode: string, value: string): string {
  if (mode !== 'test') throw new Error('BeefAPI 本机测试源站仅可用于显式 test 模式。');
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.origin !== value) {
    throw new Error('BeefAPI 测试源站必须是纯本机 HTTP origin。');
  }
  return url.origin;
}

export function trustedBeefAPIPage(value: string, kind: 'authorization' | 'wallet', test?: { mode: string; origin: string }): string {
  const origin = test ? beefAPITestOrigin(test.mode, test.origin) : 'https://enterprise.beefapi.com';
  const url = new URL(value);
  const rawPath = /^[a-z][a-z\d+.-]*:\/\/[^/?#]*([^?#]*)/i.exec(value)?.[1];
  const allowed = kind === 'wallet' ? url.pathname === '/console/topup' && !value.includes('?') : url.pathname === '/desktop-auth' || url.pathname.startsWith('/desktop-auth/');
  if (url.origin !== origin || url.username || url.password || value.includes('#') || value.includes('\\') || !rawPath || rawPath.includes('%') || rawPath.split('/').some(part => part === '.' || part === '..') || !allowed) {
    throw new Error('BeefAPI 返回的企业页面地址无法核验，请重新连接。');
  }
  return url.href;
}
