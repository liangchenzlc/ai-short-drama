export type PortraitTextureSettings = {
  personSceneFusion: 'light' | 'natural' | 'deep';
  lightingFusion: 'soft' | 'natural' | 'atmosphere';
  skin: 'clear' | 'natural' | 'real';
  texture: 'soft' | 'natural' | 'grain';
  sharpness: 'soft' | 'standard' | 'high';
};

export type PortraitTextureSettingKey = keyof PortraitTextureSettings;

export type PortraitTextureOption = {
  value: string;
  label: string;
  prompt: string;
};

export type PortraitTextureGroup = {
  key: PortraitTextureSettingKey;
  label: string;
  options: readonly PortraitTextureOption[];
};
