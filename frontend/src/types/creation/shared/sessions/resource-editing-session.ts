export interface ResourceRecovery<Document> {
  format: 1;
  base_version: string;
  document: Document;
  pending?: { base_version: string; document: Document };
}

export interface ResourceEditingOptions<Resource, Document> {
  read(): Promise<Resource>;
  save(resource: Resource, document: Document, assertActive: () => void): Promise<Resource>;
  version(resource: Resource): string;
  document(resource: Resource): Document;
  apply(resource: Resource, document: Document): Resource;
  recovery: {
    read(): ResourceRecovery<Document> | null;
    save(value: ResourceRecovery<Document>): void;
    clear(): void;
    export?(): string | null;
    previous?(): string | null;
    backup?(raw: string): void;
  };
  pauseMs?: number;
}

export interface ResourceEditingSnapshot<Resource, Document> {
  value: Resource | null;
  loaded: boolean;
  dirty: boolean;
  busy: boolean;
  status: 'loading' | 'saved' | 'unsaved' | 'saving' | 'error' | 'conflict';
  message: string;
  recoveryBlocked: boolean;
  recovered: ResourceRecovery<Document> | null;
}
