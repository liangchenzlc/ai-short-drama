export interface ReferenceImage {
  media_id: string;
  name: string;
  url: string;
  width?: number;
  height?: number;
}

export interface ReferenceImages {
  row_version: string;
  items: ReferenceImage[];
}
