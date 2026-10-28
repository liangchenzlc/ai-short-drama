import type { AudioClip, SoundDocument, SubtitleStyle } from '@host/types/creation/sound/tracks';

export type EditorSound = SoundDocument & {
  schema_version: 2;
  audio_clips: AudioClip[];
  subtitle_style: SubtitleStyle;
  tracks: NonNullable<SoundDocument['tracks']>;
};
