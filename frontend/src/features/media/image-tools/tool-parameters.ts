import { buildAnglePrompt } from '@host/shared/media/image-tools/angle';
import {
  buildEmotionPrompt,
  canvasEmotionPresets,
  resolveEmotionEditRegion,
} from '@host/shared/media/image-tools/emotion';
import {
  buildImageLightingPrompt,
  defaultLightingOptions
} from '@host/shared/media/image-tools/lighting';
import {
  buildPortraitTexturePrompt
} from '@host/shared/media/image-tools/portrait-texture';
import type {
  ImageToolParameters
} from '@host/types/media/image-groups';
import type { ImageToolParameterInput } from '@host/types/media/image-tools/parameters';

export function buildToolParameters({
  tool,
  selection,
  parameters,
  size,
  instructions,
  output,
  texture
}: ImageToolParameterInput): ImageToolParameters {
  if (tool === 'crop' || tool === 'emotion') {
    if (tool === 'crop') return { ...selection };
    const preset = canvasEmotionPresets.find((value) => value.id === parameters.preset)!;
    const faceBox = {
      id: 'target',
      x: selection.x * size.width,
      y: selection.y * size.height,
      width: selection.width * size.width,
      height: selection.height * size.height,
      source: 'manual' as const,
    };
    return {
      ...selection,
      preset: preset.id,
      instructions: [
        buildEmotionPrompt(
          {
            presetId: preset.id,
            intimacy: preset.intimacy,
            arousal: preset.arousal,
            characterName: '目标人物',
            faceBox,
          },
          resolveEmotionEditRegion(faceBox, size.width, size.height),
        ),
        instructions.trim(),
      ]
        .filter(Boolean)
        .join('\n'),
    };
  }
  if (tool === 'resize')
    return {
      output_width: output.width,
      output_height: output.height,
      interpolation: parameters.interpolation,
    };
  if (tool === 'split') return { rows: parameters.rows, columns: parameters.columns };
  if (tool === 'angle')
    return {
      ...parameters,
      instructions: [
        buildAnglePrompt({
          horizontalAngle: parameters.horizontal_angle!,
          pitchAngle: parameters.pitch_angle!,
          cameraDistance: parameters.camera_distance!,
          wideAngle: !!parameters.wide_angle,
        }),
        instructions.trim(),
      ]
        .filter(Boolean)
        .join('\n'),
    };
  if (tool === 'texture')
    return {
      instructions: buildPortraitTexturePrompt(instructions, texture),
    };
  if (tool === 'lighting')
    return {
      ...parameters,
      instructions: buildImageLightingPrompt(
        {
          ...defaultLightingOptions,
          azimuth: parameters.azimuth!,
          elevation: parameters.elevation!,
          brightness: parameters.brightness!,
          lightColor: parameters.light_color!,
          rimLight: !!parameters.rim_light,
          stylePreset: parameters.preset!,
        },
        instructions,
      ),
    };
  return { instructions: instructions.trim() };
}
