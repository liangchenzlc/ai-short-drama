export type ImageCropRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type ImageAngleTransform = {
  horizontalAngle: number;
  pitchAngle: number;
  cameraDistance: number;
  wideAngle: boolean;
};

export type ImageUpscaleAlgorithm = 'nearest' | 'bilinear' | 'high';

export type ImageUpscaleParams = {
  targetLongEdge: number;
  algorithm: ImageUpscaleAlgorithm;
};

export type ImageSplitParams = {
  rows: number;
  columns: number;
};

export type ImageSplitPiece = {
  row: number;
  column: number;
  dataUrl: string;
};
