import type { AiConfig, ServiceType } from './config-model';

/** An empty selection follows the current default; explicit choices stay unchanged. */
export function resolveConfigSelection(
  items: readonly AiConfig[], kind: ServiceType, value: string | undefined, useDefault: boolean,
): string | undefined {
  if (value) return value;
  if (!useDefault) return undefined;
  return items.find((item) => item.serviceType === kind && item.enabled && item.isDefault)?.id;
}

/** Local demo names are not server configuration IDs. Empty means follow defaults. */
export function savedConfigId(value: string): string | undefined {
  if (!/^[0-9]+$/.test(value)) return undefined;
  const id = BigInt(value);
  return id > 0n && id <= 18446744073709551615n ? value : undefined;
}
