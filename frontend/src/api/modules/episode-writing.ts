import { http } from '../http';

export interface WritingNovel { id: string; content: string; updated_at: string | null }
export interface WritingScript extends WritingNovel { state: 'unconfirmed' | 'confirmed' }
export interface WritingResponse {
  episode_id: string; content_version: string; novel: WritingNovel | null;
  editing_script: WritingScript | null; confirmed_script_id: string | null;
}
export interface NovelBody { content_version: string; content: string }
export interface ScriptBody extends NovelBody { script_id: string | null }
export interface WritingTransport {
  get(): Promise<WritingResponse>;
  novel(body: NovelBody): Promise<{ content_version: string; novel: WritingNovel }>;
  script(body: ScriptBody): Promise<{ content_version: string; script: WritingScript }>;
  select(id: string, body: { content_version: string }): Promise<WritingResponse>;
  confirm(id: string, body: { content_version: string }): Promise<WritingResponse>;
}
export function episodeWritingApi(projectId: string, episodeId: string): WritingTransport {
  const root = `/projects/${encodeURIComponent(projectId)}/episodes/${encodeURIComponent(episodeId)}`;
  return {
    async get() { return (await http.get<WritingResponse>(`${root}/writing`)).data; },
    async novel(body) { return (await http.put(`${root}/novel`, body)).data; },
    async script(body) { return (await http.put(`${root}/script`, body)).data; },
    async select(id, body) { return (await http.put<WritingResponse>(`${root}/editing-script`, { ...body, script_id: id })).data; },
    async confirm(id, body) { return (await http.post<WritingResponse>(`${root}/scripts/${encodeURIComponent(id)}/confirm`, body)).data; },
  };
}
