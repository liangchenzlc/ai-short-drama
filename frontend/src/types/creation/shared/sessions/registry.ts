/** Standard-workspace queues outlive the page that subscribes to them. */
export interface StandardSessionController {
  getSnapshot(): {
    status?: string;
    message?: string;
    dirty?: boolean;
    busy?: boolean;
    recoveryBlocked?: boolean;
  };
  subscribe(listener: () => void): () => unknown;
  dispose(): void;
  suspend?(): void;
  resume?(): void;
  hasUnsettled?(): boolean;
  hasUnpersisted?(): boolean;
  hasMemoryOnlyChanges?(): boolean;
  flush?(): Promise<unknown>;
  retry?(): Promise<unknown>;
}

export interface StandardSessionDetails {
  label?: string;
  projectId?: string;
}

export interface StandardSaveEntry extends StandardSessionDetails {
  key: string;
  status: string;
  message: string;
  hasUnpersisted: boolean;
  flush?: () => Promise<unknown>;
  retry?: () => Promise<unknown>;
}

export interface Entry {
  accountId: string;
  resourceKey: string;
  controller: StandardSessionController;
  details: StandardSessionDetails;
  references: number;
  unsubscribe: () => unknown;
  timer?: ReturnType<typeof setTimeout>;
}
