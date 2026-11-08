import type { ImageGroup } from '@host/types/media/image-groups';

export interface ImageGroupPreviewProps {
  group: ImageGroup | null;
  loading?: boolean;
  error?: string;
  label: string;
  disabled: boolean;
  imageModelId?: string;
  onRefresh: () => void;
  onBeforeChange: () => Promise<boolean>;
  onChanged: () => void;
  onAddReference?: (mediaId: string) => Promise<void>;
  onUseForVideo?: (mediaId: string) => void;
  onBusyChange?: (busy: boolean) => void;
  emptyAction?: React.ReactNode;
  emptyCount?: number;
  historical?: boolean;
  primaryStale?: boolean;
  currentImage?: { media_id: string; url: string } | null;
  presentation?: 'asset-creation';
}
