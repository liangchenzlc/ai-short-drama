export interface StorySettingsDocument {
  logline: string;
  synopsis: string;
  genre: string;
  character_bios: string;
}

export interface StorySettings extends StorySettingsDocument {
  row_version: string;
}

export interface WritingDocument {
  id: string;
  content: string;
  updated_at: string | null;
}

export interface WritingScript extends WritingDocument {
  state: 'unconfirmed' | 'confirmed';
}

export interface WritingTask {
  task_id: string;
  target: 'script';
  operation: 'script_rewrite';
  status: 'queued' | 'running';
}

export interface WritingResponse {
  content_version: string;
  editing_script: WritingScript | null;
  confirmed_script_id: string | null;
  active_writing_tasks: WritingTask[];
}

export interface WritingBody {
  content_version: string;
  content: string;
}

export interface ScriptBody extends WritingBody {
  script_id: string | null;
  change_source?: 'edit' | 'import';
}

export interface WritingTransport {
  get(): Promise<WritingResponse>;
  script(body: ScriptBody): Promise<{
    content_version: string;
    script: WritingScript;
    confirmed_script_id: string | null;
  }>;
}
