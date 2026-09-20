import type { GridBatch, MediaRef, Review } from "./episode-workflow";

export const groupShotIds = (ids: readonly string[]) =>
  Array.from({ length: Math.ceil(new Set(ids).size / 9) }, (_, index) =>
    Array.from(new Set(ids)).slice(index * 9, index * 9 + 9),
  );

export function createGridBatches(
  ids: readonly string[],
  sheetFactory: (batchIndex: number, shotIds: readonly string[]) => MediaRef,
): GridBatch[] {
  return groupShotIds(ids).map((shotIds, batchIndex) => ({
    id: `grid-${batchIndex + 1}`,
    sheet: sheetFactory(batchIndex, shotIds),
    cells: shotIds.map((shotId, index) => ({
      index,
      shotId,
      ref: null,
      review: "not_started" as Review,
    })),
  }));
}

export function bindGridCell(
  batch: GridBatch,
  index: number,
  shotId: string,
  ref: MediaRef | null,
): GridBatch {
  const cell = batch.cells.find((item) => item.index === index);
  if (!cell || cell.shotId !== shotId) return batch;

  return {
    ...batch,
    cells: batch.cells.map((item) =>
      item.index === index
        ? { ...item, ref, review: ref === null ? "not_started" : "review" }
        : item,
    ),
  };
}
