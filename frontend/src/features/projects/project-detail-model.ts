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
const detailsKey = (projectId: string) => `avi-project-details-${projectId}`;
const draftKey = (projectId: string, episodeId: string) =>
  `avi-episode-draft-${projectId}-${episodeId}`;

function readJson(storage: ReadStore, key: string): unknown {
  try {
    return JSON.parse(storage.getItem(key) ?? "null");
  } catch {
    return null;
  }
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
