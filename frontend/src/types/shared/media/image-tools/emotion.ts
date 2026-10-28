export type CanvasFaceBox = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  confidence?: number;
  source: 'detected' | 'manual';
};

export type CanvasEmotionEditRegion = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type CanvasEmotionImageArtifacts = {
  sourceDataUrl: string;
  maskDataUrl: string;
  characterDataUrl: string;
  editRegion: CanvasEmotionEditRegion;
  imageWidth: number;
  imageHeight: number;
};

export type CanvasEmotionPreset = {
  id: string;
  label: string;
  intimacy: -2 | -1 | 0 | 1 | 2;
  arousal: -2 | -1 | 0 | 1 | 2;
  prompt: string;
};

export type CanvasEmotionParams = {
  presetId: string;
  intimacy: number;
  arousal: number;
  characterName: string;
  faceBox: CanvasFaceBox;
};

export type CanvasEmotionEditMode = 'provider-mask' | 'local-composite';

export type CanvasEmotionEditPlan = {
  mode: CanvasEmotionEditMode;
  includeMask: boolean;
  notice?: string;
};

export type BlendshapeName =
  | 'browInnerUp'
  | 'browDown_L'
  | 'browDown_R'
  | 'browOuterUp_L'
  | 'browOuterUp_R'
  | 'eyeBlink_L'
  | 'eyeBlink_R'
  | 'eyeSquint_L'
  | 'eyeSquint_R'
  | 'eyeWide_L'
  | 'eyeWide_R'
  | 'cheekSquint_L'
  | 'cheekSquint_R'
  | 'noseSneer_L'
  | 'noseSneer_R'
  | 'jawOpen'
  | 'mouthPucker'
  | 'mouthClose'
  | 'mouthSmile_L'
  | 'mouthSmile_R'
  | 'mouthFrown_L'
  | 'mouthFrown_R'
  | 'mouthDimple_L'
  | 'mouthDimple_R'
  | 'mouthShrugUpper'
  | 'mouthPress_L'
  | 'mouthPress_R'
  | 'mouthStretch_L'
  | 'mouthStretch_R';

export type CanvasEmotionBlendshapes = Partial<Record<BlendshapeName, number>>;

export type EmotionEditEllipse = {
  centerX: number;
  centerY: number;
  radiusX: number;
  radiusY: number;
};
