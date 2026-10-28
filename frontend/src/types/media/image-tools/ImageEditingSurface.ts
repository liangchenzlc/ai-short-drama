export interface ImageEditingHandle {
  image: () => Promise<Blob>;
  mask: () => Promise<Blob>;
}

export type DrawMode = 'brush' | 'rectangle' | 'text';
