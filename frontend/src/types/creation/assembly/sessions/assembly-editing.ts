import type { AssemblyClip } from '@host/types/creation/assembly/document';

export interface TimelineEntry {
  clip: AssemblyClip;
  start: number;
  end: number;
}
