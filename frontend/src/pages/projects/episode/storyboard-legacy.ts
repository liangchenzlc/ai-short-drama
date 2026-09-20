import { sampleImage } from "../../../features/projects/episode-demo";
import {
  bindGridCell,
  createGridBatches,
} from "../../../features/projects/episode-grid";
import {
  adoptFrame,
  shotText,
  type EpisodeWorkflow,
  type MediaRef,
  type ShotItem,
} from "../../../features/projects/episode-workflow";
import {
  storyboardReady,
  shotTextReady,
} from "../../../features/projects/episode-review";
import type { ListedMedia } from "../../../features/projects/episode-media";

// Retained for existing saved workflows; the new storyboard UI does not expose grid/frame controls.
export function confirmShotText(
  value: EpisodeWorkflow,
  shotId: string,
): EpisodeWorkflow {
  const shot = value.shots.find((item) => item.id === shotId);
  if (!shot || !shotTextReady(value, { ...shot, review: "confirmed" }))
    return value;
  return {
    ...value,
    shots: value.shots.map((item) =>
      item.id === shotId
        ? { ...item, approvedText: shotText(item), review: "confirmed" }
        : item,
    ),
    reviews: { ...value.reviews, storyboard: "review" },
  };
}
export function confirmStoryboard(
  value: EpisodeWorkflow,
  media: readonly ListedMedia[] = [],
): EpisodeWorkflow {
  return storyboardReady(value, media)
    ? { ...value, reviews: { ...value.reviews, storyboard: "confirmed" } }
    : value;
}

const sameMedia = (left: MediaRef, right: MediaRef) =>
  left.kind === right.kind && left.id === right.id;

export function reorderStoryboardShots(
  value: EpisodeWorkflow,
  shotIds: readonly string[],
): EpisodeWorkflow {
  if (
    shotIds.length !== value.shots.length ||
    new Set(shotIds).size !== value.shots.length
  )
    return value;
  const byId = new Map(value.shots.map((shot) => [shot.id, shot]));
  const shots = shotIds.map((id) => byId.get(id));
  return shots.some((shot) => shot === undefined)
    ? value
    : { ...value, shots: shots as ShotItem[] };
}

export function addStoryboardShot(value: EpisodeWorkflow): EpisodeWorkflow {
  const shot: ShotItem = {
    id: crypto.randomUUID(),
    title: `镜头 ${value.shots.length + 1}`,
    description: "",
    action: "",
    dialogue: "",
    plannedMs: 3000,
    assetIds: [],
    review: "review",
    firstFrames: [],
    selectedFirstId: null,
    endFrames: [],
    selectedEndId: null,
    videos: [],
    selectedVideoId: null,
    frameReview: "not_started",
    videoReview: "not_started",
  };
  return {
    ...value,
    shots: [...value.shots, shot],
    reviews: {
      ...value.reviews,
      storyboard: "review",
      video:
        value.reviews.video === "confirmed" ? "review" : value.reviews.video,
    },
  };
}

export function removeStoryboardShot(
  value: EpisodeWorkflow,
  shotId: string,
): EpisodeWorkflow {
  if (!value.shots.some((shot) => shot.id === shotId)) return value;
  const shots = value.shots.filter((shot) => shot.id !== shotId);
  return {
    ...value,
    shots,
    gridBatches: value.gridBatches.map((batch) => ({
      ...batch,
      cells: batch.cells.filter((cell) => cell.shotId !== shotId),
    })),
    reviews: {
      ...value.reviews,
      storyboard: "review",
      video:
        shots.length > 0 &&
        shots.every((shot) => shot.videoReview === "confirmed")
          ? "confirmed"
          : "review",
    },
  };
}

export function createStoryboardGrids(value: EpisodeWorkflow): EpisodeWorkflow {
  const boundIds = new Set(
    value.gridBatches.flatMap((batch) =>
      batch.cells.map((cell) => cell.shotId),
    ),
  );
  const unboundIds = value.shots
    .map((shot) => shot.id)
    .filter((id) => !boundIds.has(id));
  if (!unboundIds.length) return value;
  const usedBatchIds = new Set(value.gridBatches.map((batch) => batch.id));
  let nextIndex = 1;
  const additions = createGridBatches(unboundIds, (_, ids) =>
    sampleImage(`grid-sheet-${ids.join("-")}`),
  ).map((batch) => {
    while (usedBatchIds.has(`grid-${nextIndex}`)) nextIndex += 1;
    const id = `grid-${nextIndex++}`;
    usedBatchIds.add(id);
    return {
      ...batch,
      id,
      sheet: sampleImage(
        `grid-sheet-${id}-${batch.cells.map((cell) => cell.shotId).join("-")}`,
      ),
    };
  });
  return {
    ...value,
    gridBatches: [...value.gridBatches, ...additions],
    reviews: { ...value.reviews, storyboard: "review" },
  };
}

export function bindStoryboardGridCell(
  value: EpisodeWorkflow,
  batchId: string,
  index: number,
  shotId: string,
  ref: MediaRef | null,
): EpisodeWorkflow {
  const batch = value.gridBatches.find((item) => item.id === batchId);
  if (!batch || (ref !== null && sameMedia(ref, batch.sheet))) return value;
  const nextBatch = bindGridCell(batch, index, shotId, ref);
  if (nextBatch === batch) return value;
  return {
    ...value,
    gridBatches: value.gridBatches.map((item) =>
      item.id === batchId ? nextBatch : item,
    ),
    reviews: { ...value.reviews, storyboard: "review" },
  };
}

export function confirmStoryboardGridCell(
  value: EpisodeWorkflow,
  batchId: string,
  index: number,
  shotId: string,
): EpisodeWorkflow {
  const batch = value.gridBatches.find((item) => item.id === batchId);
  const cell = batch?.cells.find(
    (item) => item.index === index && item.shotId === shotId,
  );
  if (!batch || !cell?.ref) return value;
  return {
    ...value,
    gridBatches: value.gridBatches.map((item) =>
      item.id !== batchId
        ? item
        : {
            ...item,
            cells: item.cells.map((candidate) =>
              candidate.index === index
                ? { ...candidate, review: "confirmed" as const }
                : candidate,
            ),
          },
    ),
  };
}

/** A confirmed crop becomes a shot-owned candidate; the sheet can never be adopted. */
export function adoptGridCellAsFirstFrame(
  value: EpisodeWorkflow,
  batchId: string,
  index: number,
  shotId: string,
): EpisodeWorkflow {
  const batch = value.gridBatches.find((item) => item.id === batchId);
  const cell = batch?.cells.find(
    (item) => item.index === index && item.shotId === shotId,
  );
  if (
    !batch ||
    !cell?.ref ||
    cell.review !== "confirmed" ||
    sameMedia(cell.ref, batch.sheet) ||
    cell.ref.kind === "project-image"
  )
    return value;

  const ref = cell.ref;
  const candidateId = `grid-${batchId}-cell-${index}-${ref.kind}-${ref.id}`;
  const withCandidate = {
    ...value,
    shots: value.shots.map((shot) =>
      shot.id !== shotId
        ? shot
        : {
            ...shot,
            firstFrames: shot.firstFrames.some(
              (candidate) => candidate.id === candidateId,
            )
              ? shot.firstFrames
              : [
                  ...shot.firstFrames,
                  {
                    id: candidateId,
                    value: ref,
                    source:
                      ref.kind === "demo-image"
                        ? ("demo" as const)
                        : ("import" as const),
                    usableForVideo: true,
                  },
                ],
          },
    ),
  };
  return adoptFrame(withCandidate, shotId, "first", candidateId);
}
