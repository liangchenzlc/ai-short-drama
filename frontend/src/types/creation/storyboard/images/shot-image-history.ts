import type {
  AssetFilters,
  GenerationDetail,
  GenerationFilters,
  GenerationSummary,
  MediaAsset,
  Page,
} from '@host/types/generations/api';

export interface ShotImageHistoryState {
  tasks: GenerationSummary[];
  candidates: MediaAsset[];
  loading: boolean;
  error: string;
  hasMoreTasks: boolean;
  hasMoreCandidates: boolean;
}

export interface HistoryApi {
  listTasks: (filters: GenerationFilters, signal: AbortSignal) => Promise<Page<GenerationSummary>>;
  listCandidates: (filters: AssetFilters, signal: AbortSignal) => Promise<Page<MediaAsset>>;
  detail: (id: string, signal: AbortSignal) => Promise<GenerationDetail>;
}

export interface Cursor {
  offset: number;
  total: number;
  head: string[];
}

export type Operation = 'refresh' | 'poll' | 'tasks' | 'candidates';
