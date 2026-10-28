/** 结构化素材（素材卡片/分镜卡片）与附件共用同一条素材行，只差缩略图来源。 */
export interface ComposerMaterial {
  key: string;
  name: string;
  kind: string;
  thumbnail?: string | null;
  onRemove: () => void;
}
