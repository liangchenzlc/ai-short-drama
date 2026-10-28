export interface CanvasArchiveResult {
  count: number;
  projectIds: string[];
  remaining: number;
}

export interface CanvasArchiveProgress {
  message: string;
  completed: number;
  total: number;
}

export interface CanvasArchiveBridge {
  pendingArchive(accountId: string): { title: string; count: number } | null;
  recoverArchive(accountId: string, signal: AbortSignal): Promise<CanvasArchiveResult>;
  importArchive(
    file: File,
    accountId: string,
    signal: AbortSignal,
    onProgress: (progress: CanvasArchiveProgress) => void,
  ): Promise<CanvasArchiveResult>;
  exportArchive(projectId: string, title: string, accountId: string, signal: AbortSignal): Promise<void>;
}
