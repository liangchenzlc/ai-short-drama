import type { AssemblyClip } from '@host/types/creation/assembly/document';
import type { SoundDocument } from '@host/types/creation/sound/tracks';

export interface AssemblyDraftDocument {
  assembly_id: string;
  aspect: 'auto' | '16:9' | '9:16';
  resolution: '720p' | '1080p';
  clips: AssemblyClip[];
  sound?: SoundDocument;
}
