import type { AiConfig, ServiceType } from '@host/types/ai-config/editor/config-model';

export interface CatalogPage {
  items: AiConfig[];
  total: number;
}

export interface CatalogSnapshot {
  items: AiConfig[];
  loading: boolean;
  error: string;
}

export type CatalogLoader = (
  kind: ServiceType,
  offset: number,
  limit: number,
  signal: AbortSignal,
) => Promise<CatalogPage>;

export interface Entry {
  snapshot: CatalogSnapshot;
  stale: boolean;
  listeners: Set<() => void>;
  controller: AbortController | null;
}
