import type { GlobalAsset } from "../assets/asset-model";

export interface Episode {
  id: string;
  title: string;
  synopsis: string;
}

export interface ProjectDetails {
  style: string;
  synopsis: string;
  aspect?: "16:9" | "9:16";
}

export interface EpisodeShot {
  id: string;
  title: string;
  description: string;
}

export interface EpisodeDraft {
  script: string;
  characters: string;
  props: string;
  scenes: string;
  shots: EpisodeShot[];
  imagePrompt: string;
  videoPrompt: string;
}

type ReadStore = Pick<Storage, "getItem">;
type WriteStore = Pick<Storage, "setItem">;
const episodeKey = (projectId: string) => `avi-episodes-${projectId}`;
const detailsKey = (projectId: string) => `avi-project-details-${projectId}`;
const resourcesKey = (projectId: string) =>
  `avi-project-resources-${projectId}`;
const draftKey = (projectId: string, episodeId: string) =>
  `avi-episode-draft-${projectId}-${episodeId}`;

function readJson(storage: ReadStore, key: string): unknown {
  try {
    return JSON.parse(storage.getItem(key) ?? "null");
  } catch {
    return null;
  }
}

export function readEpisodes(
  projectId: string,
  storage: ReadStore = localStorage,
): Episode[] {
  const value = readJson(storage, episodeKey(projectId));
  return Array.isArray(value)
    ? value.filter(
        (item): item is Episode =>
          typeof item?.id === "string" &&
          typeof item?.title === "string" &&
          typeof item?.synopsis === "string",
      )
    : [];
}

export function saveEpisodes(
  projectId: string,
  episodes: Episode[],
  storage: WriteStore = localStorage,
) {
  storage.setItem(episodeKey(projectId), JSON.stringify(episodes));
}

export function ensureExampleEpisode(
  projectId: string,
  storage: ReadStore & WriteStore = localStorage,
): Episode[] {
  const episodes = readEpisodes(projectId, storage);
  if (episodes.length) return episodes;

  const example: Episode = {
    id: "example-rainy-night",
    title: "示例：雨夜借光",
    synopsis: "雨夜，阿遥在街口借到一盏灯，循着灯光寻找失踪的朋友。",
  };
  saveEpisodes(projectId, [example], storage);
  saveEpisodeDraft(
    projectId,
    example.id,
    {
      ...emptyEpisodeDraft,
      script:
        "第一场 · 雨夜街口\n\n阿遥冒雨走进空荡的街道，远处亮起一盏灯。\n阿遥：能借我一点光吗？\n灯的主人抬头，指向街道尽头。",
      shots: [
        {
          id: "example-opening-shot",
          title: "雨夜街口",
          description: "远景。雨丝划过路灯，阿遥撑伞进入画面。",
        },
        {
          id: "example-lantern-shot",
          title: "借光",
          description: "中景。阿遥停在灯前，暖光照亮她的脸。",
        },
      ],
    },
    storage,
  );
  return [example];
}

export function readProjectDetails(
  projectId: string,
  storage: ReadStore = localStorage,
): ProjectDetails {
  const value = readJson(storage, detailsKey(projectId));
  const item =
    value && typeof value === "object"
      ? (value as Partial<ProjectDetails>)
      : {};
  return {
    style: typeof item.style === "string" ? item.style : "",
    synopsis: typeof item.synopsis === "string" ? item.synopsis : "",
    ...(item.aspect === "16:9" || item.aspect === "9:16"
      ? { aspect: item.aspect }
      : {}),
  };
}

export function saveProjectDetails(
  projectId: string,
  details: ProjectDetails,
  storage: WriteStore = localStorage,
) {
  storage.setItem(detailsKey(projectId), JSON.stringify(details));
}

export function validAsset(value: unknown): value is GlobalAsset {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<GlobalAsset>;
  const visualRef = item.visualRef;
  const validVisualRef =
    visualRef === undefined ||
    (typeof visualRef === "object" &&
      visualRef !== null &&
      (visualRef.kind === "demo-image" || visualRef.kind === "project-image") &&
      typeof visualRef.id === "string" &&
      (visualRef.kind === "demo-image"
        ? /^demo-image-[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(visualRef.id)
        : /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
            visualRef.id,
          )));
  return (
    typeof item.id === "string" &&
    typeof item.name === "string" &&
    typeof item.description === "string" &&
    typeof item.category === "string" &&
    Array.isArray(item.tags) &&
    item.tags.every((tag) => typeof tag === "string") &&
    ["character", "scene", "prop"].includes(item.kind ?? "") &&
    validVisualRef
  );
}

export function readProjectResources(
  projectId: string,
  storage: ReadStore = localStorage,
): GlobalAsset[] {
  const value = readJson(storage, resourcesKey(projectId));
  return Array.isArray(value) ? value.filter(validAsset) : [];
}

export function saveProjectResources(
  projectId: string,
  resources: GlobalAsset[],
  storage: WriteStore = localStorage,
) {
  storage.setItem(resourcesKey(projectId), JSON.stringify(resources));
}

export const emptyEpisodeDraft: EpisodeDraft = {
  script: "",
  characters: "",
  props: "",
  scenes: "",
  shots: [],
  imagePrompt: "",
  videoPrompt: "",
};

export function readEpisodeDraft(
  projectId: string,
  episodeId: string,
  storage: ReadStore = localStorage,
): EpisodeDraft {
  const value = readJson(storage, draftKey(projectId, episodeId));
  const item =
    value && typeof value === "object" ? (value as Partial<EpisodeDraft>) : {};
  return {
    script: typeof item.script === "string" ? item.script : "",
    characters: typeof item.characters === "string" ? item.characters : "",
    props: typeof item.props === "string" ? item.props : "",
    scenes: typeof item.scenes === "string" ? item.scenes : "",
    shots: Array.isArray(item.shots)
      ? item.shots.filter(
          (shot): shot is EpisodeShot =>
            typeof shot?.id === "string" &&
            typeof shot?.title === "string" &&
            typeof shot?.description === "string",
        )
      : [],
    imagePrompt: typeof item.imagePrompt === "string" ? item.imagePrompt : "",
    videoPrompt: typeof item.videoPrompt === "string" ? item.videoPrompt : "",
  };
}

export function saveEpisodeDraft(
  projectId: string,
  episodeId: string,
  draft: EpisodeDraft,
  storage: WriteStore = localStorage,
) {
  storage.setItem(draftKey(projectId, episodeId), JSON.stringify(draft));
}
