import type { AssemblyClip, AssemblyState, FrameTransform } from '@host/types/creation/assembly/document';
import type { EditorSound } from '@host/types/creation/assembly/sessions/assembly-document';
import type { AudioClip } from '@host/types/creation/sound/tracks';
import type { ReactNode } from 'react';

export interface PictureInspectorProps {
  clip?: AssemblyClip;
  editor: { edit: (update: (state: AssemblyState) => AssemblyState) => void };
  pictureLocked: boolean;
  t: FrameTransform;
  frameChange: (patch: Partial<FrameTransform>) => void;
  onMaterial: (audio: boolean, replacing?: boolean, kind?: AudioClip['kind']) => void;
}

export interface AudioInspectorProps {
  disabled: boolean;
  onMaterial: PictureInspectorProps['onMaterial'];
  onSelectAux: (id: string) => void;
  doc: EditorSound;
  audio?: AudioClip;
  isTrackLocked: (id: AudioClip['kind'] | 'subtitles') => boolean;
  audioChange: (patch: Partial<AudioClip>) => void;
  linkControl: ReactNode;
  change: (document: EditorSound) => void;
  analysis: { waveforms: Record<string, number[]> };
  editor: { value: AssemblyState | null };
  clips: AssemblyClip[];
  busy: boolean;
  normalizeLoudness: () => Promise<void>;
}
