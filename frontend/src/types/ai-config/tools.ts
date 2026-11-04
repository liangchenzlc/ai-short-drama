import type { ServiceTypeDto } from '@host/types/ai-config/api';
export interface ModelTestRequestDto {
  config_id: string;
  prompt: string;
  clientOperationId: string;
}

export interface ProtocolProviderDto {
  id: string;
  name: string;
  categories: ServiceTypeDto[];
  enabled: boolean;
  unavailableReason?: string;
  create?: string;
}
export interface ModelTestDto {
  id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  result?: {
    mode?: string;
    text?: string;
    images?: { url?: string; dataUrl: string }[];
    video?: { url?: string; dataUrl: string };
    audio?: { url?: string; dataUrl: string };
  };
  error?: string;
  errorCode?: string;
  canCancel: boolean;
}
