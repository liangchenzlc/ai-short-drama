import type { AgentAttachment, AgentSendInput } from '@host/types/assistant/engine';

export type ContextSnapshot = Pick<AgentSendInput, 'attachment_ids' | 'skills' | 'video_audio'>;

export interface AgentContextController {
  snapshot: () => ContextSnapshot;
  openSkills: () => void;
  openUpload: (kind: 'image' | 'text') => void;
  openAssets: () => void;
  sent: (ids: string[]) => void;
  reference: (type: 'media' | 'asset', id: string) => Promise<boolean>;
}

export interface PendingUpload {
  file: File;
  key: string;
  scope: string;
  kind: AgentAttachment['kind'];
}

export interface PendingReference {
  type: 'media' | 'asset';
  id: string;
  key: string;
  scope: string;
}
