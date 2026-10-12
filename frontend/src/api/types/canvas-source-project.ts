export interface CanvasSourceProjectContext {
  canvas_key: string;
  canvas_row_version: string;
  owning_project_id: string;
  source_project_id: string;
}

export interface CanvasSourceEpisodeSummary {
  id: string;
  title: string;
  position: number;
  content_version: string;
  kind: "episode";
}

export interface CanvasSourceProjectRead extends CanvasSourceProjectContext {
  project: {
    id: string;
    row_version: string;
    name: string;
    synopsis: string;
    aspect: "16:9" | "9:16";
    style_text: string;
  };
  episodes: CanvasSourceEpisodeSummary[];
  capabilities: {
    source_style_snapshot: "unavailable";
    source_asset_folders: "unavailable";
    source_assets: "not_included";
  };
}

export interface CanvasSourceEpisodeRead extends CanvasSourceProjectContext {
  episode: CanvasSourceEpisodeSummary;
  novel:
    | { state: "present"; content_format: "plain_text"; plain_text: string }
    | { state: "missing"; content_format: "plain_text"; plain_text: null };
}
