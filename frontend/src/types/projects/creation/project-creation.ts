import type { ProjectCreateFields } from '@host/types/projects/management';

export interface ProjectCreationAttempt {
  key: string;
  body: ProjectCreateFields;
  fingerprint: string;
}

export interface CreationStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
