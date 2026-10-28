import type { ShotRead } from '@host/types/creation/storyboard/shots';
import type { Page } from '@host/types/generations/api';

export interface StoryboardShotListProps {
  loading: boolean;
  page: Page<ShotRead> | null;
  loadError: string;
  readOnly: boolean;
  busy: boolean;
  add: () => void;
  visibleShots: ShotRead[];
  setSearch: (value: string) => void;
  setPendingOnly: (value: boolean) => void;
  mediaTab: string;
  selectedShotId: string | null;
  selectionDisabled: boolean;
  batchSelection: { enabled: boolean; ids: string[]; toggle: (id: string, checked: boolean) => void };
  batchMode: boolean;
  selectShot: (id: string) => void;
  deleteShot: (id: string) => void;
  deletingShotId: string | null;
  lockedShots: ReadonlySet<string>;
  moreLoading: boolean;
  moreError: string;
  loadMoreShots: () => void;
}
