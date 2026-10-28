export type CreationStage = 'source' | 'assets' | 'storyboard' | 'assembly';
export interface CreationModelSelections {
  script: string;
  analysis: string;
  assetImage: string;
  storyboardText: string;
  storyboardImage: string;
  video: string;
}
export interface CreationPreferences {
  models: CreationModelSelections;
}
