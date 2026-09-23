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
