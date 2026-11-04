export interface MemoryItem {
  id: string;
  kind: 'preference' | 'correction' | 'setting' | 'decision' | 'reference';
  memory_key: string;
  title: string;
  content: string;
  aliases: string[];
  tags: string[];
  pinned: boolean;
  state: 'active' | 'forgotten' | 'superseded';
  origin: 'explicit' | 'extracted';
  content_version: string;
  created_at: string;
  updated_at: string;
}

export interface MemoryPage {
  items: MemoryItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface MemoryListParams { query?: string; offset: number; limit: number }
export type MemoryFields = Pick<MemoryItem, 'title' | 'content'>;
export interface MemoryPatch extends MemoryFields { content_version: string }
export interface MemorySubmission { patch: MemoryPatch; revision: number }

export interface MemoryEdit {
  saved: MemoryItem;
  value: MemoryFields;
  revision: number;
  blocked: boolean;
  issue: string;
  conflict: MemoryItem | null;
  submission: MemorySubmission | null;
}

export interface MemoryDelete { item: MemoryItem; unknown: boolean; issue: string }
export interface MemoryManagerProps { open: boolean; onClose: () => void }

export interface MemoryManagerController {
  page: MemoryPage;
  loading: boolean;
  listIssue: string;
  query: string;
  offset: number;
  selected: MemoryEdit | null;
  saving: boolean;
  deleting: MemoryDelete | null;
  deletingBusy: boolean;
  read: () => Promise<void>;
  setQuery: (value: string) => void;
  setOffset: (value: number) => void;
  select: (item: MemoryItem) => void;
  back: () => void;
  edit: (field: keyof MemoryFields, value: string) => void;
  retry: () => Promise<void>;
  resolve: (useServer: boolean) => void;
  requestDelete: (item: MemoryItem) => void;
  cancelDelete: () => void;
  confirmDelete: () => Promise<void>;
}
