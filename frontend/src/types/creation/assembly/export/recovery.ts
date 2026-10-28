import type { RenderJob, RenderBody } from '@host/types/creation/assembly/document';

export type AssemblySubmitRender = (
  kind: 'export' | 'preview' | 'retry',
  body: RenderBody | Record<string, never>,
  key: string,
  jobId?: string,
) => Promise<RenderJob>;
