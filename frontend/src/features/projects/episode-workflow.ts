import { readEpisodeDraft, readProjectDetails } from "./project-detail-model";
import {
  isImageSettings,
  isVideoSettings,
  type ShotImageSettings,
  type ShotVideoSettings,
} from "./shot-generation-settings";

export type StageId = "source" | "script" | "assets" | "storyboard" | "video";
export type Review = "not_started" | "review" | "confirmed" | "stale";
export type DemoRun = "idle" | "preparing" | "running" | "failed";
export type MediaRef = {
  kind: "demo-image" | "demo-motion" | "project-image" | "project-video";
  id: string;
};
export type Candidate<T> = {
  id: string;
  value: T;
  source: "demo" | "manual" | "import";
  usableForVideo?: boolean;
};
export type AssetItem = {
  id: string;
  kind: "character" | "scene" | "prop";
  name: string;
  description: string;
  linkedResourceId: string | null;
  imageCandidates: Candidate<MediaRef>[];
  selectedImageId: string | null;
  review: Review;
  approvedText?: { name: string; description: string };
};
export type ShotItem = {
  id: string;
  title: string;
  description: string;
  script?: string;
  imageSettings?: ShotImageSettings;
  videoSettings?: ShotVideoSettings;
  /** Optional for compatibility with previously saved V2 shots. */
  imagePrompt?: string;
  videoPrompt?: string;
  action: string;
  dialogue: string;
  plannedMs: number;
  assetIds: string[];
  review: Review;
  firstFrames: Candidate<MediaRef>[];
  selectedFirstId: string | null;
  endFrames: Candidate<MediaRef>[];
  selectedEndId: string | null;
  videos: Candidate<MediaRef>[];
  selectedVideoId: string | null;
  frameReview: Review;
  /** Optional on old V2 drafts; normalize each selected role when reading. */
  firstFrameReview?: Review;
  endFrameReview?: Review;
  videoReview: Review;
  approvedText?: {
    title: string;
    description: string;
    script?: string;
    action: string;
    dialogue: string;
    plannedMs: number;
    assetIds: string[];
  };
};
export type GridBatch = {
  id: string;
  sheet: MediaRef;
  cells: {
    index: number;
    shotId: string;
    ref: MediaRef | null;
    review: Review;
  }[];
};
export type EpisodeWorkflow = {
  version: 2;
  novel: string;
  scriptDraft: string;
  scriptCandidates: Candidate<string>[];
  approvedScript: {
    text: string;
    aspect: "16:9" | "9:16";
    style: string;
  } | null;
  aspect: "16:9" | "9:16";
  style: string;
  models: {
    script: string;
    analysis: string;
    assetImage: string;
    storyboardText: string;
    storyboardImage: string;
    video: string;
  };
  assets: AssetItem[];
  shots: ShotItem[];
  gridBatches: GridBatch[];
  storyboardMode: "frames" | "grid";
  reviews: Record<StageId, Review>;
  legacyNotes: {
    characters: string;
    props: string;
    scenes: string;
    imagePrompt: string;
    videoPrompt: string;
  };
};

const staleWhenPresent = (review: Review, hasResult: boolean): Review =>
  hasResult ? "stale" : "not_started";
const staleStageWhenAffected = (review: Review, hasResult: boolean): Review =>
  hasResult ? "stale" : review;

const hasStoryboardResult = (shot: ShotItem) =>
  shot.firstFrames.length > 0 || shot.endFrames.length > 0;
const hasVideoResult = (shot: ShotItem) => shot.videos.length > 0;
export function frameRoleReview(shot: ShotItem, role: "first" | "end"): Review {
  return (
    (role === "first" ? shot.firstFrameReview : shot.endFrameReview) ??
    ((role === "first" ? shot.selectedFirstId : shot.selectedEndId)
      ? shot.frameReview
      : "not_started")
  );
}
export function staleFrameReviews(shot: ShotItem) {
  return {
    firstFrameReview: staleWhenPresent(
      frameRoleReview(shot, "first"),
      shot.firstFrames.length > 0,
    ),
    endFrameReview: staleWhenPresent(
      frameRoleReview(shot, "end"),
      shot.endFrames.length > 0,
    ),
  };
}
export function isScriptCurrent(value: EpisodeWorkflow) {
  return (
    value.reviews.script === "confirmed" &&
    !!value.approvedScript?.text.trim() &&
    value.approvedScript.text === value.scriptDraft &&
    value.approvedScript.aspect === value.aspect &&
    value.approvedScript.style === value.style
  );
}
export const assetText = (asset: AssetItem) => ({
  name: asset.name,
  description: asset.description,
});
export const shotText = (shot: ShotItem) => ({
  title: shot.title,
  description: shot.description,
  ...(shot.script !== undefined ? { script: shot.script } : {}),
  action: shot.action,
  dialogue: shot.dialogue,
  plannedMs: shot.plannedMs,
  assetIds: [...shot.assetIds],
});

export function addAsset(
  value: EpisodeWorkflow,
  kind: AssetItem["kind"],
): EpisodeWorkflow {
  const asset: AssetItem = {
    id: crypto.randomUUID(),
    kind,
    name: "",
    description: "",
    linkedResourceId: null,
    imageCandidates: [],
    selectedImageId: null,
    review: "review",
  };
  return {
    ...value,
    assets: [...value.assets, asset],
    reviews: { ...value.reviews, assets: "review" },
  };
}
export function removeAsset(
  value: EpisodeWorkflow,
  id: string,
): EpisodeWorkflow {
  if (!value.assets.some((asset) => asset.id === id)) return value;
  const affected = editAsset(value, id, {});
  return {
    ...affected,
    assets: affected.assets.filter((asset) => asset.id !== id),
    shots: affected.shots.map((shot) =>
      shot.assetIds.includes(id)
        ? {
            ...shot,
            assetIds: shot.assetIds.filter((assetId) => assetId !== id),
            review: "review",
          }
        : shot,
    ),
    reviews: {
      ...affected.reviews,
      storyboard: value.shots.some((shot) => shot.assetIds.includes(id))
        ? "review"
        : affected.reviews.storyboard,
    },
  };
}
const hasGridResult = (state: EpisodeWorkflow, shotIds?: ReadonlySet<string>) =>
  state.gridBatches.some((batch) =>
    batch.cells.some(
      (cell) => cell.ref !== null && (!shotIds || shotIds.has(cell.shotId)),
    ),
  );
const staleGridCells = (
  state: EpisodeWorkflow,
  affectsShot: (shotId: string) => boolean,
) =>
  state.gridBatches.map((batch) => ({
    ...batch,
    cells: batch.cells.map((cell) =>
      affectsShot(cell.shotId)
        ? {
            ...cell,
            review: staleWhenPresent(cell.review, cell.ref !== null),
          }
        : cell,
    ),
  }));

export function editScript(
  state: EpisodeWorkflow,
  text: string,
): EpisodeWorkflow {
  const hasAssets = state.assets.length > 0;
  const hasStoryboard =
    state.shots.some(hasStoryboardResult) || hasGridResult(state);
  const hasVideos = state.shots.some(hasVideoResult);
  return {
    ...state,
    scriptDraft: text,
    assets: state.assets.map((asset) => ({
      ...asset,
      approvedText:
        asset.approvedText ??
        (asset.review === "confirmed" ? assetText(asset) : undefined),
      review: staleWhenPresent(asset.review, true),
    })),
    shots: state.shots.map((shot) => ({
      ...shot,
      approvedText:
        shot.approvedText ??
        (shot.review === "confirmed" ? shotText(shot) : undefined),
      ...staleFrameReviews(shot),
      review: staleWhenPresent(shot.review, true),
      frameReview: staleWhenPresent(
        shot.frameReview,
        hasStoryboardResult(shot),
      ),
      videoReview: staleWhenPresent(shot.videoReview, hasVideoResult(shot)),
    })),
    gridBatches: staleGridCells(state, () => true),
    reviews: {
      ...state.reviews,
      script: "review",
      assets: staleStageWhenAffected(state.reviews.assets, hasAssets),
      storyboard: staleStageWhenAffected(
        state.reviews.storyboard,
        hasStoryboard,
      ),
      video: staleStageWhenAffected(state.reviews.video, hasVideos),
    },
  };
}

export function editAsset(
  state: EpisodeWorkflow,
  id: string,
  patch: Partial<Pick<AssetItem, "name" | "description">>,
): EpisodeWorkflow {
  const affectedShots = state.shots.filter((shot) =>
    shot.assetIds.includes(id),
  );
  const affectedShotIds = new Set(affectedShots.map((shot) => shot.id));
  const hasStoryboard =
    affectedShots.some(hasStoryboardResult) ||
    hasGridResult(state, affectedShotIds);
  const hasVideos = affectedShots.some(hasVideoResult);
  return {
    ...state,
    assets: state.assets.map((asset) =>
      asset.id === id
        ? {
            ...asset,
            ...patch,
            approvedText:
              asset.approvedText ??
              (asset.review === "confirmed" ? assetText(asset) : undefined),
            review: "review",
          }
        : asset,
    ),
    shots: state.shots.map((shot) =>
      shot.assetIds.includes(id)
        ? {
            ...shot,
            ...staleFrameReviews(shot),
            frameReview: staleWhenPresent(
              shot.frameReview,
              hasStoryboardResult(shot),
            ),
            videoReview: staleWhenPresent(
              shot.videoReview,
              hasVideoResult(shot),
            ),
          }
        : shot,
    ),
    gridBatches: staleGridCells(state, (shotId) => affectedShotIds.has(shotId)),
    reviews: {
      ...state.reviews,
      assets: "review",
      storyboard: staleStageWhenAffected(
        state.reviews.storyboard,
        hasStoryboard,
      ),
      video: staleStageWhenAffected(state.reviews.video, hasVideos),
    },
  };
}

export function editShot(
  state: EpisodeWorkflow,
  id: string,
  patch: Partial<
    Pick<
      ShotItem,
      | "title"
      | "description"
      | "script"
      | "imagePrompt"
      | "action"
      | "dialogue"
      | "plannedMs"
      | "assetIds"
    >
  >,
): EpisodeWorkflow {
  const target = state.shots.find((shot) => shot.id === id);
  const hasStoryboard = target !== undefined && hasStoryboardResult(target);
  const hasVideos = target !== undefined && hasVideoResult(target);
  return {
    ...state,
    shots: state.shots.map((shot) =>
      shot.id === id
        ? {
            ...shot,
            ...staleFrameReviews(shot),
            ...patch,
            approvedText:
              shot.approvedText ??
              (shot.review === "confirmed" ? shotText(shot) : undefined),
            review: "review",
            frameReview: staleWhenPresent(
              shot.frameReview,
              hasStoryboardResult(shot),
            ),
            videoReview: staleWhenPresent(
              shot.videoReview,
              hasVideoResult(shot),
            ),
          }
        : shot,
    ),
    gridBatches: staleGridCells(state, (shotId) => shotId === id),
    reviews: {
      ...state.reviews,
      storyboard: staleStageWhenAffected(
        state.reviews.storyboard,
        hasStoryboard || hasGridResult(state, new Set([id])),
      ),
      video: staleStageWhenAffected(state.reviews.video, hasVideos),
    },
  };
}

/** Motion instructions only invalidate the video, never its source image. */
export function editShotVideoPrompt(
  state: EpisodeWorkflow,
  id: string,
  videoPrompt: string,
): EpisodeWorkflow {
  const shot = state.shots.find((item) => item.id === id);
  if (!shot || shot.videoPrompt === videoPrompt) return state;
  return {
    ...state,
    shots: state.shots.map((item) =>
      item.id === id
        ? {
            ...item,
            videoPrompt,
            videoReview: staleWhenPresent(
              item.videoReview,
              hasVideoResult(item),
            ),
          }
        : item,
    ),
    reviews: {
      ...state.reviews,
      video: staleStageWhenAffected(state.reviews.video, hasVideoResult(shot)),
    },
  };
}

export function adoptFrame(
  state: EpisodeWorkflow,
  shotId: string,
  role: "first" | "end",
  candidateId: string,
): EpisodeWorkflow {
  const shot = state.shots.find((item) => item.id === shotId);
  const candidates = role === "first" ? shot?.firstFrames : shot?.endFrames;
  if (!shot || !candidates?.some((candidate) => candidate.id === candidateId))
    return state;

  return {
    ...state,
    shots: state.shots.map((item) =>
      item.id === shotId
        ? {
            ...item,
            ...(role === "first"
              ? { selectedFirstId: candidateId }
              : { selectedEndId: candidateId }),
            firstFrameReview:
              role === "first" ? "confirmed" : frameRoleReview(item, "first"),
            endFrameReview:
              role === "end" ? "confirmed" : frameRoleReview(item, "end"),
            frameReview:
              frameRoleReview(item, role === "first" ? "end" : "first") ===
              "stale"
                ? "stale"
                : "confirmed",
            videoReview: staleWhenPresent(
              item.videoReview,
              hasVideoResult(item),
            ),
          }
        : item,
    ),
    reviews: {
      ...state.reviews,
      video: staleStageWhenAffected(state.reviews.video, hasVideoResult(shot)),
    },
  };
}

export function pendingCount(state: EpisodeWorkflow, id: StageId): number {
  if (id === "source") return state.scriptDraft.trim() ? 0 : 1;
  if (id === "script") return isScriptCurrent(state) ? 0 : 1;
  if (id === "assets")
    return state.assets.length
      ? state.assets.filter(
          (asset) => asset.review !== "confirmed" || !asset.selectedImageId,
        ).length
      : 1;
  if (id === "storyboard")
    return state.shots.length
      ? state.shots.filter(
          (shot) =>
            shot.review !== "confirmed" ||
            frameRoleReview(shot, "first") !== "confirmed",
        ).length + (state.reviews.storyboard === "confirmed" ? 0 : 1)
      : 1;
  return state.shots.length
    ? state.shots.filter((shot) => shot.videoReview !== "confirmed").length
    : 1;
}
export function nearestPendingStage(state: EpisodeWorkflow): StageId {
  return (
    (["source", "script", "assets", "storyboard", "video"] as const).find(
      (id) => pendingCount(state, id) > 0,
    ) ?? "video"
  );
}

export function stageStatus(state: EpisodeWorkflow, id: StageId): Review {
  if (id === "assets" && state.assets.some((asset) => asset.review === "stale"))
    return "stale";
  if (
    id === "storyboard" &&
    (state.shots.some(
      (shot) => shot.review === "stale" || shot.frameReview === "stale",
    ) ||
      state.gridBatches.some((batch) =>
        batch.cells.some((cell) => cell.review === "stale"),
      ))
  )
    return "stale";
  if (
    id === "video" &&
    state.shots.some((shot) => shot.videoReview === "stale")
  )
    return "stale";
  return state.reviews[id];
}

type ReadStore = Pick<Storage, "getItem">;
type WriteStore = Pick<Storage, "setItem">;
export type WorkflowDefaults = {
  aspect?: "16:9" | "9:16";
  style?: string;
};

const workflowKey = (projectId: string, episodeId: string) =>
  `avi-episode-workflow-v2-${projectId}-${episodeId}`;
const reviews = (): Record<StageId, Review> => ({
  source: "not_started",
  script: "not_started",
  assets: "not_started",
  storyboard: "not_started",
  video: "not_started",
});
const models = () => ({
  script: "演示剧本模型",
  analysis: "演示素材分析模型",
  assetImage: "演示素材图片模型",
  storyboardText: "演示分镜脚本模型",
  storyboardImage: "演示分镜图片模型",
  video: "演示分镜视频模型",
});

function resolvedDefaults(
  projectId: string,
  defaults: WorkflowDefaults,
  storage: ReadStore,
) {
  const project = readProjectDetails(projectId, storage);
  return {
    aspect: defaults.aspect ?? project.aspect ?? "16:9",
    style: defaults.style ?? project.style,
  };
}

export function emptyWorkflow(defaults: WorkflowDefaults): EpisodeWorkflow {
  return {
    version: 2,
    novel: "",
    scriptDraft: "",
    scriptCandidates: [],
    approvedScript: null,
    aspect: defaults.aspect ?? "16:9",
    style: defaults.style ?? "",
    models: models(),
    assets: [],
    shots: [],
    gridBatches: [],
    storyboardMode: "frames",
    reviews: reviews(),
    legacyNotes: {
      characters: "",
      props: "",
      scenes: "",
      imagePrompt: "",
      videoPrompt: "",
    },
  };
}

function parseWorkflow(storage: ReadStore, key: string): unknown {
  try {
    return JSON.parse(storage.getItem(key) ?? "null");
  } catch {
    return null;
  }
}

function isReview(value: unknown): value is Review {
  return ["not_started", "review", "confirmed", "stale"].includes(
    value as string,
  );
}

function isAspect(value: unknown): value is "16:9" | "9:16" {
  return value === "16:9" || value === "9:16";
}

function isMediaRef(value: unknown): value is MediaRef {
  if (!value || typeof value !== "object") return false;
  const media = value as Partial<MediaRef>;
  if (typeof media.id !== "string") return false;
  if (media.kind === "demo-image") {
    return /^demo-image-[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(media.id);
  }
  if (media.kind === "demo-motion") {
    return /^demo-motion-[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(media.id);
  }
  if (media.kind === "project-image" || media.kind === "project-video") {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      media.id,
    );
  }
  return false;
}

function isCandidate(value: unknown, validValue: (value: unknown) => boolean) {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<Candidate<unknown>>;
  return (
    typeof candidate.id === "string" &&
    validValue(candidate.value) &&
    ["demo", "manual", "import"].includes(candidate.source ?? "") &&
    (candidate.usableForVideo === undefined ||
      typeof candidate.usableForVideo === "boolean")
  );
}

function isStringArray(value: unknown): value is string[] {
  return (
    Array.isArray(value) && value.every((item) => typeof item === "string")
  );
}

function isAsset(value: unknown): value is AssetItem {
  if (!value || typeof value !== "object") return false;
  const asset = value as Partial<AssetItem>;
  return (
    typeof asset.id === "string" &&
    ["character", "scene", "prop"].includes(asset.kind ?? "") &&
    typeof asset.name === "string" &&
    typeof asset.description === "string" &&
    (asset.linkedResourceId === null ||
      typeof asset.linkedResourceId === "string") &&
    Array.isArray(asset.imageCandidates) &&
    asset.imageCandidates.every((item) => isCandidate(item, isMediaRef)) &&
    (asset.selectedImageId === null ||
      typeof asset.selectedImageId === "string") &&
    isReview(asset.review) &&
    (asset.approvedText === undefined ||
      (typeof asset.approvedText?.name === "string" &&
        typeof asset.approvedText?.description === "string"))
  );
}

function isShot(value: unknown): value is ShotItem {
  if (!value || typeof value !== "object") return false;
  const shot = value as Partial<ShotItem>;
  const images = (candidates: unknown) =>
    Array.isArray(candidates) &&
    candidates.every((item) => isCandidate(item, isMediaRef));
  return (
    typeof shot.id === "string" &&
    typeof shot.title === "string" &&
    typeof shot.description === "string" &&
    (shot.script === undefined || typeof shot.script === "string") &&
    (shot.imageSettings === undefined || isImageSettings(shot.imageSettings)) &&
    (shot.videoSettings === undefined || isVideoSettings(shot.videoSettings)) &&
    (shot.imagePrompt === undefined || typeof shot.imagePrompt === "string") &&
    (shot.videoPrompt === undefined || typeof shot.videoPrompt === "string") &&
    typeof shot.action === "string" &&
    typeof shot.dialogue === "string" &&
    typeof shot.plannedMs === "number" &&
    Number.isFinite(shot.plannedMs) &&
    isStringArray(shot.assetIds) &&
    isReview(shot.review) &&
    images(shot.firstFrames) &&
    (shot.selectedFirstId === null ||
      typeof shot.selectedFirstId === "string") &&
    images(shot.endFrames) &&
    (shot.selectedEndId === null || typeof shot.selectedEndId === "string") &&
    images(shot.videos) &&
    (shot.selectedVideoId === null ||
      typeof shot.selectedVideoId === "string") &&
    isReview(shot.frameReview) &&
    (shot.firstFrameReview === undefined || isReview(shot.firstFrameReview)) &&
    (shot.endFrameReview === undefined || isReview(shot.endFrameReview)) &&
    isReview(shot.videoReview) &&
    (shot.approvedText === undefined ||
      (typeof shot.approvedText?.title === "string" &&
        typeof shot.approvedText?.description === "string" &&
        (shot.approvedText.script === undefined ||
          typeof shot.approvedText.script === "string") &&
        typeof shot.approvedText?.action === "string" &&
        typeof shot.approvedText?.dialogue === "string" &&
        typeof shot.approvedText?.plannedMs === "number" &&
        Number.isFinite(shot.approvedText.plannedMs) &&
        isStringArray(shot.approvedText.assetIds)))
  );
}

function isGridBatch(value: unknown): value is GridBatch {
  if (!value || typeof value !== "object") return false;
  const batch = value as Partial<GridBatch>;
  if (
    typeof batch.id !== "string" ||
    !isMediaRef(batch.sheet) ||
    !Array.isArray(batch.cells) ||
    batch.cells.length > 9
  ) {
    return false;
  }
  const shotIds = new Set<string>();
  const indexes = new Set<number>();
  return batch.cells.every((cell) => {
    if (
      typeof cell?.index !== "number" ||
      !Number.isInteger(cell.index) ||
      cell.index < 0 ||
      cell.index > 8 ||
      indexes.has(cell.index) ||
      typeof cell.shotId !== "string" ||
      shotIds.has(cell.shotId) ||
      (cell.ref !== null && !isMediaRef(cell.ref)) ||
      !isReview(cell.review)
    ) {
      return false;
    }
    indexes.add(cell.index);
    shotIds.add(cell.shotId);
    return true;
  });
}

function hasModels(value: unknown): value is EpisodeWorkflow["models"] {
  if (!value || typeof value !== "object") return false;
  const models = value as Partial<EpisodeWorkflow["models"]>;
  return [
    models.script,
    models.analysis,
    models.assetImage,
    models.storyboardText,
    models.storyboardImage,
    models.video,
  ].every((model) => typeof model === "string");
}

function isWorkflow(value: unknown): value is EpisodeWorkflow {
  if (!value || typeof value !== "object") return false;
  const state = value as Partial<EpisodeWorkflow>;
  return (
    state.version === 2 &&
    typeof state.novel === "string" &&
    typeof state.scriptDraft === "string" &&
    Array.isArray(state.scriptCandidates) &&
    state.scriptCandidates.every((item) =>
      isCandidate(item, (v) => typeof v === "string"),
    ) &&
    (state.approvedScript === null ||
      (typeof state.approvedScript === "object" &&
        typeof state.approvedScript.text === "string" &&
        isAspect(state.approvedScript.aspect) &&
        typeof state.approvedScript.style === "string")) &&
    isAspect(state.aspect) &&
    typeof state.style === "string" &&
    hasModels(state.models) &&
    Array.isArray(state.assets) &&
    state.assets.every(isAsset) &&
    Array.isArray(state.shots) &&
    state.shots.every(isShot) &&
    Array.isArray(state.gridBatches) &&
    state.gridBatches.every(isGridBatch) &&
    (state.storyboardMode === "frames" || state.storyboardMode === "grid") &&
    state.reviews !== undefined &&
    (Object.keys(reviews()) as StageId[]).every((stage) =>
      isReview(state.reviews?.[stage]),
    ) &&
    state.legacyNotes !== undefined &&
    typeof state.legacyNotes.characters === "string" &&
    typeof state.legacyNotes.props === "string" &&
    typeof state.legacyNotes.scenes === "string" &&
    typeof state.legacyNotes.imagePrompt === "string" &&
    typeof state.legacyNotes.videoPrompt === "string"
  );
}

function freshWorkflow(state: EpisodeWorkflow): EpisodeWorkflow {
  return JSON.parse(JSON.stringify(state)) as EpisodeWorkflow;
}

export function readWorkflow(
  projectId: string,
  episodeId: string,
  defaults: WorkflowDefaults,
  storage: ReadStore = localStorage,
): EpisodeWorkflow {
  const saved = parseWorkflow(storage, workflowKey(projectId, episodeId));
  if (isWorkflow(saved)) {
    const result = freshWorkflow(saved);
    result.shots = result.shots.map((shot) => ({
      ...shot,
      firstFrameReview: frameRoleReview(shot, "first"),
      endFrameReview: frameRoleReview(shot, "end"),
    }));
    return result;
  }

  const resolved = resolvedDefaults(projectId, defaults, storage);
  const legacy = readEpisodeDraft(projectId, episodeId, storage);
  const state = emptyWorkflow(resolved);
  return {
    ...state,
    scriptDraft: legacy.script,
    shots: legacy.shots.map((shot) => ({
      id: shot.id,
      title: shot.title,
      description: shot.description,
      action: "",
      dialogue: "",
      plannedMs: 0,
      assetIds: [],
      review: "not_started",
      firstFrames: [],
      selectedFirstId: null,
      endFrames: [],
      selectedEndId: null,
      videos: [],
      selectedVideoId: null,
      frameReview: "not_started",
      videoReview: "not_started",
    })),
    legacyNotes: {
      characters: legacy.characters,
      props: legacy.props,
      scenes: legacy.scenes,
      imagePrompt: legacy.imagePrompt,
      videoPrompt: legacy.videoPrompt,
    },
  };
}

export function saveWorkflow(
  projectId: string,
  episodeId: string,
  value: EpisodeWorkflow,
  storage: WriteStore = localStorage,
) {
  if (!isWorkflow(value)) {
    return { ok: false, error: "本集内容无效，未保存。" } as const;
  }
  try {
    storage.setItem(workflowKey(projectId, episodeId), JSON.stringify(value));
    return { ok: true } as const;
  } catch {
    return { ok: false, error: "本集内容未能保存，请检查本地空间。" } as const;
  }
}
