import type { AgentSubject } from '@host/types/assistant/references/agent-scope';
import type { ShotRead, StoryboardApi, StoryboardPage } from '@host/types/creation/storyboard/shots';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import type { Dispatch, RefObject, SetStateAction } from 'react';

export interface ShotDeleteNotice {
  id: string;
  type: 'error' | 'warning';
  message: string;
  description?: string;
}

export interface ShotMutationOptions {
  api: StoryboardApi;
  collection: {
    pageRef: RefObject<StoryboardPage | null>;
    getShot: (id: string) => ShotRead | undefined;
    setPage: (page: StoryboardPage | null) => void;
    setShot: (shot: ShotRead, version?: string) => void;
    mergeLoadedPage: (remote: StoryboardPage) => StoryboardPage | null;
    restoreShotDraft: (remote: ShotRead, version?: string) => ShotRead;
    removedShotIds: RefObject<Set<string>>;
    storyboardVersionFloor: RefObject<string>;
    extraShots: RefObject<Map<string, ShotRead>>;
    editing: { ids: () => string[]; forget: (id: string) => void };
  };
  selection: {
    selectedShotRef: RefObject<string | null>;
    selectionEpoch: RefObject<number>;
    selectShotId: (id: string | null) => void;
    selectSubject?: (subject: AgentSubject | null) => void;
    toggleBatch: (id: string, checked: boolean) => void;
  };
  lifecycle: {
    contextEpoch: RefObject<number>;
    isCurrentContext: (epoch: number) => boolean;
    currentAccess: RefObject<{ readOnly: boolean; busy: boolean; loading: boolean; loadError: string }>;
  };
  locks: {
    shotMutation: RefObject<symbol | null>;
    shotLocks: RefObject<Map<string, symbol>>;
    dialogueBarrier: RefObject<NavigationBarrier | null>;
  };
  state: {
    setBusy: (busy: boolean) => void;
    setMessage: (message: string) => void;
    setDeletingShotId: (id: string | null) => void;
    setDeleteNotice: (notice: ShotDeleteNotice | null) => void;
    setLoading: (loading: boolean) => void;
    setLoadError: (error: string) => void;
    setStoryboardRevision: Dispatch<SetStateAction<number>>;
  };
  flushAll: () => Promise<boolean>;
}
