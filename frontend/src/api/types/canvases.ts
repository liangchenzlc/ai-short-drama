export interface CanvasSummaryDto {
  id: string;
  project_id: string;
  source_key: string;
  title: string;
  row_version: string;
  schema_version: number;
  created_at: string;
  updated_at: string;
}

export interface CanvasReadDto extends CanvasSummaryDto {
  source_document: Record<string, unknown>;
  resource_aliases?: Record<string, string[]>;
}

export interface CanvasRecycleRestoreRequestDto {
  archive_key: string;
}

export interface CanvasRecycleStatusDto {
  source_key: string;
  project_id: string;
  archive_key: string;
  expected_row_version: string;
  committed_row_version: string;
  state: "archived" | "superseded" | "purged";
}

export interface CanvasRecycleItemDto {
  source_key: string;
  project_id: string;
  archive_key: string;
  deleted_at: string;
  source_document: Record<string, unknown>;
}

export interface CanvasRecycleListDto { items: CanvasRecycleItemDto[] }
export interface CanvasRecyclePurgeDto {
  source_key: string;
  archive_key: string;
  state: "purged";
}

export interface CanvasResourceNormalizeDto {
  resource_map: Record<string, string>;
  resource_aliases: Record<string, string[]>;
}

export interface CanvasCreationDto extends CanvasSummaryDto, CanvasResourceNormalizeDto {}

export interface CanvasInitialDrawingDto {
  drawingId: string;
  engine: 'excalidraw';
  revision: '0';
  snapshot: unknown;
  shapeCount: number;
  pageCount: 1;
  previewResourceId?: string;
  render?: {
    resourceId?: string;
    pageId?: string;
    width?: number;
    height?: number;
    mimeType?: string;
    background?: 'white' | '';
    storageKey?: string;
  };
}

export interface CanvasCreationRequestDto {
  title?: string;
  source_key?: string;
  source_document?: Record<string, unknown>;
  drawing_documents?: CanvasInitialDrawingDto[];
}

export interface CanvasWorkspaceDto {
  items: (CanvasSummaryDto & {
    folder_id?: string | null;
    canvas_title?: string | null;
    node_count: number;
    preview_nodes: Record<string, unknown>[];
    source_document?: Record<string, unknown>;
    resource_aliases?: Record<string, string[]>;
  })[];
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
}

export interface CanvasViewportDto {
  x: number;
  y: number;
  k: number;
}

export interface CanvasViewportRequestDto {
  expected_viewport: CanvasViewportDto;
  viewport: CanvasViewportDto;
}

export interface CanvasViewPreferencesDto {
  appearance: {
    mode: 'light' | 'dark' | 'custom';
    custom?: {
      baseTheme: 'light' | 'dark';
      backgroundColor: string;
      backgroundBrightness: number;
      gridColor: string;
      gridOpacity: number;
    } | null;
  } | null;
  backgroundMode: 'dots' | 'lines' | 'blank';
  showImageInfo: boolean;
}

export interface CanvasViewPreferencesRequestDto {
  expected_preferences: CanvasViewPreferencesDto;
  preferences: CanvasViewPreferencesDto;
}

export interface CanvasRevisionDto {
  id: string;
  canvas_id: string;
  row_version: string;
  title: string;
  node_count: number;
  connection_count: number;
  payload_bytes: number;
  reason: 'automatic' | 'before_restore';
  created_at: string;
  content_updated_at: string;
}

export type CanvasDrawingHeadsDto = Record<string, { revision: string; deleted: boolean }>;
