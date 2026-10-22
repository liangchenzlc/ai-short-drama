import type { TextGenerationRequest } from '../../api/types/generations';

export interface NovelGenerationAttempt { format: 1; key: string; body: TextGenerationRequest }
const decimal = (value: unknown) => typeof value === 'string' && /^[0-9]+$/.test(value);
const keys = (value: object, allowed: string[]) => Object.keys(value).every(key => allowed.includes(key));

/** Recovery stores only this workflow's frozen input, never provider credentials. */
export function isNovelGenerationAttempt(value: unknown): value is NovelGenerationAttempt {
  if (!value || typeof value !== 'object' || Array.isArray(value) || !keys(value, ['format', 'key', 'body'])) return false;
  const attempt = value as Partial<NovelGenerationAttempt>;
  if (attempt.format !== 1 || typeof attempt.key !== 'string' || !/^[0-9a-f-]{36}$/i.test(attempt.key)) return false;
  const body = attempt.body;
  if (!body || !keys(body, ['config_id', 'source', 'instructions', 'parameters'])) return false;
  if (body.config_id !== undefined && !decimal(body.config_id)) return false;
  if (typeof body.instructions !== 'string' || body.instructions.length > 4000) return false;
  const source = body.source as { scene?: unknown; project_id?: unknown; content_version?: unknown; draft_id?: unknown; draft_version?: unknown } | undefined;
  if (!source || !keys(source, ['scene', 'project_id', 'content_version', 'draft_id', 'draft_version'])) return false;
  if (source.scene !== 'novel_script' || ![source.project_id, source.content_version, source.draft_id, source.draft_version].every(decimal)) return false;
  return !!body.parameters && keys(body.parameters, ['max_output_tokens']) && body.parameters.max_output_tokens === 8192;
}
