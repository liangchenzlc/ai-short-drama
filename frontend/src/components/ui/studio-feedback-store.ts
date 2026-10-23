export type StudioFeedbackType = 'error' | 'warning' | 'info' | 'success';

export interface FeedbackEntry<T = unknown> {
  id: string;
  type: StudioFeedbackType;
  summary: string;
  content: T;
  /** chat=助手对话内的问题，只在对话里显示；默认 document，进入文档保存状态面板。 */
  scope?: 'chat' | 'document';
}

/** One store per React provider: no account, dialog or application-root leakage. */
export function createFeedbackStore<T>() {
  let snapshot: FeedbackEntry<T>[] = [];
  const listeners = new Set<() => void>();
  const notices = new Map<string, { signature: string; time: number }>();
  const emit = () => listeners.forEach(listener => listener());
  return {
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    getSnapshot: () => snapshot,
    upsert(entry: FeedbackEntry<T>) {
      snapshot = [...snapshot.filter(item => item.id !== entry.id), entry];
      emit();
    },
    remove(id: string) {
      if (!snapshot.some(item => item.id === id)) return;
      snapshot = snapshot.filter(item => item.id !== id);
      emit();
    },
    shouldNotify(key: string, signature: string, now = Date.now()) {
      const previous = notices.get(key);
      if (previous?.signature === signature && now - previous.time < 30_000) return false;
      notices.set(key, { signature, time: now });
      for (const [id, value] of notices) if (now - value.time >= 30_000) notices.delete(id);
      return true;
    },
  };
}

/** Legacy mixed-result channels only; new operations should pass an explicit type. */
export function feedbackType(text: string): StudioFeedbackType {
  if (/待核对|尚未确认|结果不明|未查到|版本|已变化|冲突|请先|请核对|不支持|超出|超过|上限/.test(text)) return 'warning';
  if (/失败|无法|损坏|错误|不可用|断开/.test(text)) return 'error';
  if (/成功|已提交|已开始|已找到|已保存|已复制|已加入|已共享|已删除/.test(text)) return 'success';
  return 'info';
}
