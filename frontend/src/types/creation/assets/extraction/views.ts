import type { ExtractionCandidate } from '@host/types/creation/assets/extraction';
import type { AssetDraft, AssetKind } from '@host/types/creation/assets/management';
import type { GenerationSummary } from '@host/types/generations/api';

export interface ExtractionSettingsProps {
  projectId: string;
  readOnly: boolean;
  loaded: boolean;
  busy: boolean;
  canExtract: boolean;
  kinds: AssetKind[];
  setKinds: (kinds: AssetKind[]) => void;
  instructions: string;
  setInstructions: (instructions: string) => void;
  modelId: string;
  onModelChange: (id: string) => void;
  pending: boolean;
  onHistory: () => void;
  onConfirmScript: () => void;
}

export interface ExtractionHistoryProps {
  historyError: string;
  historyLoading: boolean;
  tasks: GenerationSummary[];
  busy: boolean;
  historyOffset: number;
  total: number;
  refreshTasks: () => Promise<unknown>;
  showTask: (id: string) => Promise<void>;
  moreHistory: () => Promise<void>;
}

export interface ExtractionCandidateCardProps {
  item: ExtractionCandidate;
  draft: AssetDraft;
  disabled: boolean;
  stale: boolean;
  dirty: boolean;
  selected: boolean;
  editing: boolean;
  choice: string;
  onSelect: (selected: boolean) => void;
  onToggle: () => void;
  onEdit: (change: Partial<AssetDraft>) => void;
  onKind: (kind: AssetKind) => void;
  onChoice: (choice: string) => void;
}
