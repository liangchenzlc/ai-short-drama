import { hasAvailableMedia, type ListedMedia } from "./episode-media";
import {
  isScriptCurrent,
  frameRoleReview,
  type AssetItem,
  type EpisodeWorkflow,
  type ShotItem,
} from "./episode-workflow";

export function assetImageReady(
  asset: AssetItem,
  media: readonly ListedMedia[] = [],
) {
  const selected = asset.imageCandidates.find(
    (candidate) => candidate.id === asset.selectedImageId,
  );
  return (
    asset.review === "confirmed" &&
    !!asset.name.trim() &&
    !!asset.description.trim() &&
    !!selected &&
    (selected.value.kind === "demo-image" ||
      (selected.value.kind === "project-image" &&
        hasAvailableMedia(media, selected.value.id, "image/")))
  );
}
export function shotTextReady(value: EpisodeWorkflow, shot: ShotItem) {
  return (
    isScriptCurrent(value) &&
    shot.review === "confirmed" &&
    !!shot.title.trim() &&
    !!shot.description.trim() &&
    !!shot.action.trim() &&
    shot.plannedMs > 0
  );
}
export function shotInputIssue(
  value: EpisodeWorkflow,
  shot: ShotItem,
  media: readonly ListedMedia[] = [],
): string | null {
  if (!isScriptCurrent(value))
    return "请先返回第二阶段，基于当前剧本、画幅和风格生成文本框架";
  if (!shotTextReady(value, shot))
    return "请先返回分镜阶段确认本镜文字、动作和时长";
  if (
    shot.assetIds.some((id) => {
      const asset = value.assets.find((item) => item.id === id);
      return !asset || !assetImageReady(asset, media);
    })
  )
    return "请先返回素材阶段确认本镜引用的素材图片";
  return null;
}
export function storyboardReady(
  value: EpisodeWorkflow,
  media: readonly ListedMedia[] = [],
) {
  return (
    value.shots.length > 0 &&
    value.shots.every((shot) => {
      const first = shot.firstFrames.find(
        (candidate) => candidate.id === shot.selectedFirstId,
      );
      return (
        !shotInputIssue(value, shot, media) &&
        frameRoleReview(shot, "first") === "confirmed" &&
        !!first &&
        (first.value.kind === "demo-image" ||
          (first.value.kind === "project-image" &&
            hasAvailableMedia(media, first.value.id, "image/")))
      );
    })
  );
}
