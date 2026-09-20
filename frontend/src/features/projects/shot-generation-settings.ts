import type { EpisodeWorkflow, ShotItem } from "./episode-workflow";

export const imageResolutions = ["1K", "2K", "4K"] as const;
export const imageAspects = ["16:9", "9:16", "1:1", "4:3", "3:4"] as const;
export const imageLayouts = [
  { value: "single", label: "单图", cells: 1 },
  { value: "four", label: "四宫格", cells: 4 },
  { value: "five", label: "五宫格", cells: 5 },
  { value: "nine", label: "九宫格", cells: 9 },
] as const;

export type ImageAspect = (typeof imageAspects)[number];
export type ShotImageSettings = {
  resolution: (typeof imageResolutions)[number];
  aspect: ImageAspect | "inherit";
  layout: (typeof imageLayouts)[number]["value"];
};
export type ShotVideoSettings = {
  resolution: "720p" | "1080p";
  durationMs: number;
};

export function isImageSettings(value: unknown): value is ShotImageSettings {
  if (!value || typeof value !== "object") return false;
  const item = value as ShotImageSettings;
  return (
    imageResolutions.includes(item.resolution) &&
    (item.aspect === "inherit" || imageAspects.includes(item.aspect)) &&
    imageLayouts.some((layout) => layout.value === item.layout)
  );
}

export function isVideoSettings(value: unknown): value is ShotVideoSettings {
  if (!value || typeof value !== "object") return false;
  const item = value as ShotVideoSettings;
  return (
    ["720p", "1080p"].includes(item.resolution) &&
    Number.isSafeInteger(item.durationMs) &&
    item.durationMs > 0
  );
}

export function imageSettingsForShot(shot: ShotItem): ShotImageSettings {
  return isImageSettings(shot.imageSettings)
    ? shot.imageSettings
    : { resolution: "2K", aspect: "inherit", layout: "single" };
}

export function videoSettingsForShot(shot: ShotItem): ShotVideoSettings {
  return isVideoSettings(shot.videoSettings)
    ? shot.videoSettings
    : {
        resolution: "1080p",
        durationMs:
          Number.isSafeInteger(shot.plannedMs) && shot.plannedMs > 0
            ? shot.plannedMs
            : 5000,
      };
}

export function shotScript(shot: ShotItem): string {
  return (
    shot.script ??
    [
      shot.description,
      shot.action && `动作：${shot.action}`,
      shot.dialogue && `对白：${shot.dialogue}`,
    ]
      .filter(Boolean)
      .join("\n\n")
  );
}

export function imageDimensions(
  resolution: ShotImageSettings["resolution"],
  aspect: ImageAspect,
) {
  const longEdge = { "1K": 1024, "2K": 2048, "4K": 4096 }[resolution];
  const [width, height] = aspect.split(":").map(Number);
  const scale = longEdge / Math.max(width, height);
  return {
    width: Math.round(width * scale),
    height: Math.round(height * scale),
  };
}

/** Settings describe the next request; adopted media and their review stay intact. */
export function updateShotGenerationSettings(
  state: EpisodeWorkflow,
  id: string,
  patch: Partial<Pick<ShotItem, "imageSettings" | "videoSettings">>,
): EpisodeWorkflow {
  if (
    !state.shots.some((shot) => shot.id === id) ||
    (patch.imageSettings !== undefined &&
      !isImageSettings(patch.imageSettings)) ||
    (patch.videoSettings !== undefined && !isVideoSettings(patch.videoSettings))
  )
    return state;
  return {
    ...state,
    shots: state.shots.map((shot) =>
      shot.id === id ? { ...shot, ...patch } : shot,
    ),
  };
}
