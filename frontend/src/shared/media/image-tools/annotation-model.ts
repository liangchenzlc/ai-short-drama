import type {
  AnnotationHistory,
  AnnotationOperation,
  AnnotationPoint,
} from '@host/types/shared/media/image-tools/annotation-model';
export function normalizeAnnotationRect(start: AnnotationPoint, end: AnnotationPoint) {
  return {
    x: Math.min(start.x, end.x),
    y: Math.min(start.y, end.y),
    width: Math.abs(end.x - start.x),
    height: Math.abs(end.y - start.y),
  };
}

export const annotationHistory = {
  empty: (): AnnotationHistory => ({ items: [], redo: [] }),
  push: (history: AnnotationHistory, operation: AnnotationOperation): AnnotationHistory => ({
    items: [...history.items, operation],
    redo: [],
  }),
  undo: (history: AnnotationHistory): AnnotationHistory =>
    history.items.length
      ? {
          items: history.items.slice(0, -1),
          redo: [...history.redo, history.items[history.items.length - 1]],
        }
      : history,
  redo: (history: AnnotationHistory): AnnotationHistory =>
    history.redo.length
      ? {
          items: [...history.items, history.redo[history.redo.length - 1]],
          redo: history.redo.slice(0, -1),
        }
      : history,
};
