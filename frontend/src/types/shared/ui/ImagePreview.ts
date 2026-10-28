import type { ImgHTMLAttributes, ReactNode } from 'react';

export interface ImagePreviewProps {
  src: string;
  alt: string;
  title?: string;
  onClose: () => void;
  canClose?: boolean;
  footer?: ReactNode;
  presentation?: 'standard-creation';
}

export interface PreviewImageProps extends Omit<ImgHTMLAttributes<HTMLImageElement>, 'src' | 'alt' | 'onClick'> {
  src: string;
  alt: string;
  /** Class for the thumbnail button; className and style apply to the image. */
  triggerClassName?: string;
  previewTitle?: string;
  presentation?: 'standard-creation';
}
