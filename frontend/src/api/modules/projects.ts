import { ApiError, errorMessage, http } from '../http';
import type { WebProject } from '../../types/projects';
import type { Episode } from '../../features/projects/project-detail-model';

interface ProjectDto {
  workspace_mode?: WorkspaceMode; primary_canvas_id?: string | null; canvas_count?: number;
  row_version?: string; owner_user_id?: string; capabilities?: Record<string, boolean>;
  id: string; name: string; synopsis: string; aspect: '16:9' | '9:16'; style: string;
  last_opened_at: string | null; created_at: string | null; episode_count?: number;
}
interface EpisodeDto {
  row_version?: string;
  cover_url?: string | null;
  id: string; project_id: string; position: number; title: string; synopsis: string;
  aspect: '16:9' | '9:16'; style: string; episode_number?: number;
}
interface Page<T> { items: T[]; total: number; offset: number; limit: number }
export type WorkspaceMode = 'standard' | 'infinite_canvas';
export interface RemoteProject extends WebProject { workspaceMode: WorkspaceMode; primaryCanvasId: string | null; canvasCount: number; rowVersion?: string; ownerUserId?: string; capabilities?: Record<string, boolean>; synopsis: string; style: string; episodeCount: number }
export interface RemoteEpisode extends Episode { rowVersion?: string; coverUrl?: string | null; projectId: string; position: number; aspect: '16:9' | '9:16'; style: string; number?: number }
export interface ProjectFields { row_version?: string; name: string; aspect: '16:9' | '9:16'; synopsis: string; style: string }
export interface ProjectCreateFields extends ProjectFields { workspace_mode?: WorkspaceMode }
export interface EpisodeFields { row_version?: string; title: string; synopsis: string; aspect?: '16:9' | '9:16'; style?: string }
const project = (dto: ProjectDto): RemoteProject => ({
  workspaceMode: dto.workspace_mode ?? 'standard', primaryCanvasId: dto.primary_canvas_id ?? null, canvasCount: dto.canvas_count ?? 0,
  rowVersion: dto.row_version, ownerUserId: dto.owner_user_id, capabilities: dto.capabilities,
  projectId: dto.id, name: dto.name, aspect: dto.aspect, style: dto.style, synopsis: dto.synopsis,
  lastOpenedAt: dto.last_opened_at ?? dto.created_at ?? '', episodeCount: dto.episode_count ?? 0,
});
const episode = (dto: EpisodeDto): RemoteEpisode => ({
  rowVersion: dto.row_version,
  coverUrl: dto.cover_url,
  id: dto.id, projectId: dto.project_id, position: dto.position, title: dto.title,
  synopsis: dto.synopsis, aspect: dto.aspect, style: dto.style, number: dto.episode_number,
});
const root = '/projects';
const episodesPath = (id: string) => `${root}/${encodeURIComponent(id)}/episodes`;
export const projectsApi = {
  async list(offset = 0, q = '', signal?: AbortSignal) {
    const { data } = await http.get<Page<ProjectDto>>(root, { params: { offset, limit: 20, q }, signal });
    return { ...data, items: data.items.map(project) };
  },
  async get(id: string, signal?: AbortSignal) {
    return project((await http.get<ProjectDto>(`${root}/${encodeURIComponent(id)}`, { signal })).data);
  },
  async create(body: ProjectCreateFields, idempotencyKey?: string) {
    return project((await http.post<ProjectDto>(root, body, idempotencyKey ? { headers: { 'Idempotency-Key': idempotencyKey } } : undefined)).data);
  },
  async canvas(id: string, canvasId: string, signal?: AbortSignal) {
    return (await http.get<{ id: string; project_id: string; source_key: string }>(`${root}/${encodeURIComponent(id)}/canvases/${encodeURIComponent(canvasId)}`, { signal })).data;
  },
  async update(id: string, body: Partial<ProjectFields>) {
    return project((await http.patch<ProjectDto>(`${root}/${id}`, body)).data);
  },
  async open(id: string, signal?: AbortSignal) {
    return project((await http.post<ProjectDto>(`${root}/${id}/open`, undefined, { signal })).data);
  },
  async remove(id: string) { await http.delete(`${root}/${id}`); },
  async listEpisodes(id: string, offset = 0, limit = 20, signal?: AbortSignal) {
    const { data } = await http.get<Page<EpisodeDto>>(episodesPath(id), { params: { offset, limit }, signal });
    return { ...data, items: data.items.map(episode) };
  },
  async getEpisode(id: string, episodeId: string, signal?: AbortSignal) {
    return episode((await http.get<EpisodeDto>(`${episodesPath(id)}/${encodeURIComponent(episodeId)}`, { signal })).data);
  },
  async createEpisode(id: string, body: EpisodeFields) {
    return episode((await http.post<EpisodeDto>(episodesPath(id), body)).data);
  },
  async updateEpisode(id: string, episodeId: string, body: Partial<EpisodeFields>) {
    return episode((await http.patch<EpisodeDto>(`${episodesPath(id)}/${episodeId}`, body)).data);
  },
  async removeEpisode(id: string, episodeId: string) { await http.delete(`${episodesPath(id)}/${episodeId}`); },
};

export function projectError(error: unknown, action?: 'delete' | 'create') {
  if (error instanceof ApiError) {
    if (error.status === 404) return '项目或分集不存在，可能已被删除。请刷新后重试。';
    if (error.status === 409) return action === 'delete'
      ? '仍有关联分集、素材或制作内容，暂时无法删除。请先清理关联内容。'
      : errorMessage(error);
    if (action === 'create' && (!error.status || error.status >= 500))
      return '未能确认创建结果。请先关闭弹窗并刷新列表，确认是否已创建，避免重复提交。';
  }
  return errorMessage(error);
}
