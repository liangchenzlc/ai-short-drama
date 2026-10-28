export type AnnotationPoint = { x: number; y: number };

export type AnnotationOperation =
  | { type: 'brush'; color: string; size: number; points: AnnotationPoint[] }
  | {
      type: 'rectangle';
      color: string;
      size: number;
      x: number;
      y: number;
      width: number;
      height: number;
    }
  | {
      type: 'text';
      color: string;
      size: number;
      x: number;
      y: number;
      text: string;
    };

export type AnnotationHistory = {
  items: AnnotationOperation[];
  redo: AnnotationOperation[];
};
