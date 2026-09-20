import type {
  AssetItem,
  Candidate,
  MediaRef,
  ShotItem,
} from "./episode-workflow";

export const DEMO_MODELS = {
  script: ["A", "B"].map((x) => ({
    value: `demo-script-${x}`,
    label: `演示·剧本模型 ${x}`,
  })),
  analysis: ["A", "B"].map((x) => ({
    value: `demo-analysis-${x}`,
    label: `演示·素材分析模型 ${x}`,
  })),
  assetImage: ["A", "B"].map((x) => ({
    value: `demo-image-${x}`,
    label: `演示·文生图模型 ${x}`,
  })),
  storyboardText: ["A", "B"].map((x) => ({
    value: `demo-storyboard-${x}`,
    label: `演示·分镜模型 ${x}`,
  })),
  storyboardImage: ["A", "B"].map((x) => ({
    value: `demo-frame-${x}`,
    label: `演示·分镜图模型 ${x}`,
  })),
  video: ["A", "B"].map((x) => ({
    value: `demo-video-${x}`,
    label: `演示·图生视频模型 ${x}`,
  })),
} as const;

const stableId = (value: string) => {
  let hash = 5381;
  for (const character of value) hash = (hash * 33) ^ character.codePointAt(0)!;
  return (hash >>> 0).toString(36);
};

export function sampleScript(novel: string): Candidate<string> | null {
  const source = novel.trim();
  if (!source) return null;
  const excerpt = source.slice(0, 48);

  return {
    id: `demo-script-${stableId(source)}`,
    source: "demo",
    value: `演示剧本草稿（请编辑并确认）\n\n来源片段：「${excerpt}」\n\n场次 1：人物在场景中遇到一个需要自行补全的转折。\n动作：请依据原文补写动作与对白。`,
  };
}

export function sampleAssets(): AssetItem[] {
  return [
    [
      "character",
      "林小雨",
      "二十多岁的青年调查员，齐耳短发，身穿深蓝色雨衣，左手握着旧地图。雨夜路灯从侧后方勾出轮廓，神情警觉，写实电影质感，半身人物设定。",
    ],
    [
      "scene",
      "旧城雨巷",
      "深夜的旧城石板巷，雨水映出昏黄路灯，远处一扇木门半掩，薄雾压低街道纵深。冷蓝环境光与暖色灯光形成对比，宽幅电影构图。",
    ],
    [
      "prop",
      "旧铜手提灯",
      "一盏磨损的铜制手提灯，玻璃灯罩透出暖黄色光，握柄缠着褪色布条，细小雨滴附着在铜面。近景静物，写实材质与柔和暗部。",
    ],
  ].map(([kind, name, description], index) => ({
    id: `demo-asset-${index + 1}`,
    kind: kind as AssetItem["kind"],
    name,
    description,
    linkedResourceId: null,
    imageCandidates: [],
    selectedImageId: null,
    review: "review" as const,
  }));
}

export function sampleShots(
  script: string,
  assets: readonly AssetItem[] = [],
): ShotItem[] {
  const basis = script.trim() || "empty-script";
  const fixtureId = stableId(basis);
  const character = assets.find((asset) => asset.kind === "character");
  const scene = assets.find((asset) => asset.kind === "scene");
  const prop = assets.find((asset) => asset.kind === "prop");
  const person = character?.name || "林夏";
  const place = scene?.name || "老巷";
  const object = prop?.name || "黑色雨伞";
  const assetIds = [scene, character, prop]
    .filter((asset): asset is AssetItem => asset !== undefined)
    .map((asset) => asset.id);
  return [
    [
      "雨巷来客",
      `雨夜，${person}带着${object}走进${place}。镜头从巷口平视，缓慢跟随人物向旧公寓移动，湿润的石板路映出门灯的暖光。`,
      "人物走进巷口，缓慢向前。",
      `雨夜，${place}，中远景，平视构图。${person}带着${object}，背对镜头走向巷尾旧公寓。湿润石板路反射暖黄色门灯，冷蓝色环境光，细密雨丝，写实电影质感。人物外观、场景及道具造型遵循关联素材。`,
    ],
    [
      "门前迟疑",
      `${person}在${place}的旧公寓门前停下，握紧手中的${object}。镜头切至侧面近景，人物缓缓抬眼看向紧闭的木门，迟迟没有敲门。`,
      "人物停下脚步，握紧道具，抬眼望向木门。",
      `雨夜，${place}，旧公寓木门前，侧面近景。${person}手持${object}，抬眼望向木门，神情犹疑。暖黄色门灯照亮侧脸，背景为冷蓝色雨幕，浅景深，写实电影质感。人物及道具外观遵循关联素材，与上一镜保持一致。`,
    ],
  ].map(([title, description, action, imagePrompt], index) => ({
    id: `demo-shot-${fixtureId}-${index + 1}`,
    title,
    description,
    imagePrompt,
    videoPrompt:
      index === 0
        ? `镜头缓慢向前推进，${person}沿湿润的石板路走向公寓，衣摆随步伐轻微晃动，雨水持续落下。保持人物外观、${object}与场景一致，运动平稳。`
        : `镜头缓慢推近${person}的侧脸。人物停下脚步，握紧${object}，抬眼望向木门，短暂停顿。暖色灯光稳定，背景雨水持续落下，保持人物和道具外观一致。`,
    action,
    dialogue: "",
    plannedMs: 3000,
    assetIds: [...assetIds],
    review: "review" as const,
    firstFrames: [],
    selectedFirstId: null,
    endFrames: [],
    selectedEndId: null,
    videos: [],
    selectedVideoId: null,
    frameReview: "not_started" as const,
    videoReview: "not_started" as const,
  }));
}

export function sampleImage(seed: string): MediaRef {
  return { kind: "demo-image", id: `demo-image-${stableId(seed)}` };
}

export function sampleMotion(shotId: string): MediaRef {
  return { kind: "demo-motion", id: `demo-motion-${stableId(shotId)}` };
}
