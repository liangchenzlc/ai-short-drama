import type { WebProject } from '@host/types/projects/model';

export type WorkspaceMode = 'standard' | 'infinite_canvas';

export interface ProjectDto {
  id: string;
  name: string;
  workspace_mode: WorkspaceMode;
  row_version: string;
  owner_user_id?: string;
  capabilities?: Record<string, boolean>;
  primary_canvas_id?: string | null;
  canvas_count?: number;
  cover_url?: string | null;
  last_opened_at: string | null;
  created_at: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}

export interface RemoteProject extends WebProject {
  workspaceMode: WorkspaceMode;
  primaryCanvasId: string | null;
  canvasCount: number;
  rowVersion: string;
  ownerUserId?: string;
  capabilities?: Record<string, boolean>;
  coverUrl: string | null;
}

export interface ProjectFields {
  row_version: string;
  name: string;
}

export interface ProjectCreateFields {
  name: string;
  workspace_mode: WorkspaceMode;
}
