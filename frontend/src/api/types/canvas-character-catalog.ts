import type { CanvasCharacterImageResourceDto } from './canvas-character-images'

export interface CanvasCharacterVoiceSampleRead {
  binding_version: string
  media_id: string
  checksum_sha256: string
  duration_ms: number
  resource: Omit<CanvasCharacterImageResourceDto, 'kind'> & { kind: 'audio' }
}

export type CanvasCharacterRepresentationRole =
  | 'primary'
  | 'front'
  | 'side'
  | 'back'
  | 'turnaround_sheet'
  | 'expression_sheet'

export type CanvasCharacterJsonValue =
  | string
  | number
  | boolean
  | null
  | CanvasCharacterJsonValue[]
  | { [key: string]: CanvasCharacterJsonValue }

export interface CanvasCharacterRepresentationRead {
  reference_id: string
  role: CanvasCharacterRepresentationRole
  ordinal: number
  media_id: string
  format_code: string
  mime_type: string | null
  original_name: string
  byte_size: number | null
  width: number | null
  height: number | null
}

export interface CanvasCharacterCatalogItemRead {
  asset_id: string
  asset_row_version: string
  current_version_id: string
  version_id: string
  version: string
  position: number
  name: string
  label: string
  description: string
  prompt: string
  tags: string[]
  definition: { [key: string]: CanvasCharacterJsonValue }
  definition_origin: 'legacy_current' | 'structured'
  representations: CanvasCharacterRepresentationRead[]
  voice_status: 'missing' | 'ready' | 'unavailable'
  voice_sample: CanvasCharacterVoiceSampleRead | null
}

export interface CanvasCharacterCatalogRead {
  canvas_key: string
  canvas_row_version: string
  source_project_id: string
  items: CanvasCharacterCatalogItemRead[]
}

export interface CanvasCharacterCatalogDetailRead {
  canvas_key: string
  canvas_row_version: string
  source_project_id: string
  item: CanvasCharacterCatalogItemRead
}
