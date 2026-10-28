import { http } from '../http';

export interface StorySettingsDocument { logline: string; synopsis: string; genre: string; character_bios: string }
export interface StorySettings extends StorySettingsDocument { row_version: string }
export function storySettingsApi(projectId: string) {
  const path = `/projects/${encodeURIComponent(projectId)}/story-settings`;
  return {
    async get() { return (await http.get<StorySettings>(path)).data; },
    async save(body: StorySettings) { return (await http.put<StorySettings>(path, body)).data; },
  };
}

export interface WritingNovel { id: string; content: string; updated_at: string | null }
export interface EditorAttributes { bold?: boolean; italic?: boolean; header?: 1 | 2 | 3; blockquote?: boolean }
export interface EditorDocument { ops: { insert: string; attributes?: EditorAttributes }[] }
export interface WritingScript extends WritingNovel { state: 'unconfirmed' | 'confirmed'; editor_document?: EditorDocument | null }
export interface WritingTask { task_id: string; target: 'outline' | 'script'; operation: 'story_outline' | 'script_generate' | 'script_rewrite'; status: 'queued' | 'running' }
export interface WritingResponse {
   content_version: string; novel: WritingNovel | null; outline: WritingNovel | null;
  editing_script: WritingScript | null; confirmed_script_id: string | null;
  active_writing_tasks: WritingTask[];
}
export interface NovelBody { content_version: string; content: string }
export interface ScriptBody extends NovelBody { script_id: string | null; editor_document?: EditorDocument | null; change_source?: 'edit' | 'import' }
export interface WritingTransport {
  get(): Promise<WritingResponse>;
  novel(body: NovelBody): Promise<{ content_version: string; novel: WritingNovel }>;
  outline(body: NovelBody): Promise<{ content_version: string; outline: WritingNovel }>;
  script(body: ScriptBody): Promise<{ content_version: string; script: WritingScript; confirmed_script_id: string | null }>;
  select(id: string, body: { content_version: string }): Promise<WritingResponse>;
  confirm(id: string, body: { content_version: string }): Promise<WritingResponse>;
}

export function episodeWritingApi(projectId: string): WritingTransport {
  const root = `/projects/${encodeURIComponent(projectId)}`;
  return {
    async get() { return (await http.get<WritingResponse>(`${root}/writing`)).data; },
    async novel(body) { return (await http.put(`${root}/novel`, body)).data; },
    async outline(body) { return (await http.put(`${root}/outline`, body)).data; },
    async script(body) { return (await http.put(`${root}/script`, body)).data; },
    async select(id, body) { return (await http.put<WritingResponse>(`${root}/editing-script`, { ...body, script_id: id })).data; },
    async confirm(id, body) { return (await http.post<WritingResponse>(`${root}/scripts/${encodeURIComponent(id)}/confirm`, body)).data; },
  };
}
