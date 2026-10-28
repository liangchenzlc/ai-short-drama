import { ApiError, errorMessage, http } from '@host/api/http';
import type { CanvasSummaryDto } from '@host/types/canvas/api';
import type {
  Page,
  ProjectCreateFields,
  ProjectDto,
  ProjectFields,
  RemoteProject,
} from '@host/types/projects/management';
const project = (dto: ProjectDto): RemoteProject => ({
  projectId: dto.id,
  name: dto.name,
  workspaceMode: dto.workspace_mode,
  primaryCanvasId: dto.primary_canvas_id ?? null,
  canvasCount: dto.canvas_count ?? 0,
  rowVersion: dto.row_version,
  ownerUserId: dto.owner_user_id,
  capabilities: dto.capabilities,
  coverUrl: dto.cover_url ?? null,
  lastOpenedAt: dto.last_opened_at ?? dto.created_at ?? '',
});
export const projectsApi = {
  async list(offset = 0, q = '', signal?: AbortSignal) {
    const { data } = await http.get<Page<ProjectDto>>('/projects', {
      params: { offset, limit: 20, q },
      signal,
    });
    return { ...data, items: data.items.map(project) };
  },
  async get(id: string, signal?: AbortSignal) {
    return project(
      (
        await http.get<ProjectDto>(`/projects/${encodeURIComponent(id)}`, {
          signal,
        })
      ).data,
    );
  },
  async create(body: ProjectCreateFields, key: string) {
    return project(
      (
        await http.post<ProjectDto>('/projects', body, {
          headers: { 'Idempotency-Key': key },
        })
      ).data,
    );
  },
  async canvas(id: string, canvasId: string, signal?: AbortSignal) {
    return (
      await http.get<{ id: string; project_id: string; source_key: string }>(
        `/projects/${encodeURIComponent(id)}/canvases/${encodeURIComponent(canvasId)}`,
        { signal },
      )
    ).data;
  },
  async canvases(id: string, signal?: AbortSignal) {
    return (await http.get<{ items: CanvasSummaryDto[] }>(`/projects/${encodeURIComponent(id)}/canvases`, { signal }))
      .data.items;
  },
  async update(id: string, body: ProjectFields) {
    return project((await http.patch<ProjectDto>(`/projects/${encodeURIComponent(id)}`, body)).data);
  },
  async remove(id: string, rowVersion: string) {
    await http.delete(`/projects/${encodeURIComponent(id)}`, {
      params: { row_version: rowVersion },
    });
  },
  async open(id: string, signal?: AbortSignal) {
    return project(
      (await http.post<ProjectDto>(`/projects/${encodeURIComponent(id)}/open`, undefined, { signal })).data,
    );
  },
};
export function projectError(error: unknown, action?: 'create') {
  if (error instanceof ApiError) {
    if (error.status === 404) return '项目不存在或已不可用，请返回项目列表。';
    if (action === 'create' && (!error.status || error.status >= 500))
      return '创建结果尚未确认，请保留原内容重试核对。';
  }
  return errorMessage(error);
}
