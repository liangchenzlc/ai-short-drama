import type { ReactNode } from 'react';
import type { AudioCandidate, Extraction, SoundDocument, SoundState } from '@host/types/creation/sound/tracks';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';

export interface SoundEdit {
  document: SoundDocument;
  reviewed: boolean;
  timeline_hash?: string;
}

export interface SoundPanelProps {
  projectId: string;
  disabled: boolean;
  readOnly?: boolean;
  flushVideo: () => Promise<boolean>;
  onSaved: () => Promise<unknown>;
  registerBarrier: (barrier: NavigationBarrier | null) => void;
  renderToolbar?: (trigger: ReactNode) => ReactNode;
  panelContainer?: HTMLElement | null;
  onOpenChange?: (open: boolean) => void;
}

export interface SoundHelpProps {
  title: ReactNode;
  label?: string;
}

export interface SoundVolumeProps {
  label: string;
  value: number;
  disabled: boolean;
  onChange: (value: number) => void;
}

export interface SoundEditorProps {
  state: SoundState;
  busy: boolean;
  disabled: boolean;
  inline: boolean;
  onEdit: (document: SoundDocument) => void;
}

export interface SoundSubtitlesEditorProps extends SoundEditorProps {
  srtUrl: string;
  onImport: (file: File) => Promise<void>;
  onFill: () => Promise<void>;
  onNotice: (message: string) => void;
}

export interface SoundMixingEditorProps extends SoundEditorProps {
  onMetadata: (state: SoundState) => void;
  onUpload: (file: File) => Promise<void>;
  onNotice: (message: string) => void;
  onAudioError: (message: string) => void;
}

export interface SoundDialogueSelection {
  selected: string;
  setSelected: (id: string) => void;
}

export interface SoundExtractionState {
  extraction: Extraction | null;
  extractId: string;
  onExtract: (configId?: string) => Promise<void>;
  onAdoptExtraction: () => Promise<void>;
}

export interface SoundSpeechState {
  candidates: AudioCandidate[];
  active: boolean;
  dirty: boolean;
  generate: () => Promise<void>;
  resume: (id: string) => Promise<void>;
  adopt: (lineId: string, mediaId: string) => Promise<void>;
  onAudioError: () => void;
}

export interface SoundDialogueEditorProps extends SoundEditorProps {
  scope: string;
  selection: SoundDialogueSelection;
  extractionState: SoundExtractionState;
  speech: SoundSpeechState;
  onSaveVoice: (character: string, voice: string) => Promise<void>;
}
