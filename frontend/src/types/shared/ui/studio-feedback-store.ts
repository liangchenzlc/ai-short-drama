export type StudioFeedbackType = 'error' | 'warning' | 'info' | 'success';

export interface FeedbackStore<T> {
  subscribe(listener: () => void): () => void;
  getSnapshot(): FeedbackEntry<T>[];
  upsert(entry: FeedbackEntry<T>): void;
  remove(id: string): void;
  shouldNotify(key: string, signature: string, now?: number): boolean;
}

export interface FeedbackEntry<T = unknown> {
  id: string;
  type: StudioFeedbackType;
  summary: string;
  content: T;
  /** chat=助手对话内的问题，只在对话里显示；默认 document，进入文档保存状态面板。 */
  scope?: 'chat' | 'document';
}
