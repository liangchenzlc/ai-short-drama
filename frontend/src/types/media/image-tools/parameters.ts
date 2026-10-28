import type { ImageTool, ImageToolParameters } from '@host/types/media/image-groups';
import type { ImageCropRect } from '@host/types/shared/media/image-tools/image-data';
import type { PortraitTextureSettings } from '@host/types/shared/media/image-tools/portrait-texture';

export interface ImageToolParameterInput {
  tool: ImageTool;
  selection: ImageCropRect;
  parameters: ImageToolParameters;
  size: { width: number; height: number };
  output: { width: number; height: number };
  instructions: string;
  texture: PortraitTextureSettings;
}
