import type { RenderBody } from '@host/types/creation/assembly/document';

export interface RenderAttempt {
  format: 1;
  kind: 'export' | 'preview' | 'retry';
  key: string;
  body: RenderBody | Record<string, never>;
  job_id?: string;
}
